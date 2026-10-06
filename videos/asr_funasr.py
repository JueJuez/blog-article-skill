# -*- coding: utf-8 -*-
"""videos/asr_funasr.py — FunASR (Paraformer-zh) 本地中文特化转写后端。

设计目标：与 faster-whisper 完全隔离，默认不启用，模型与 whisper 同根目录管理。

- 作为**模块**被 asr.py 导入时：只做 subprocess 派发（模块顶层**绝不 import funasr**，
  因此对 whisper 进程零污染、零额外依赖）。
- 作为**脚本**被 funasr venv 的 python 直接运行（__main__）时：真正加载 funasr 转写，
  结果按 [{start, duration, text}] 写出 JSON——与 asr.py 的 whisper 端 schema 对齐。

两种运行模式（脚本侧）：
- 一次性（--wav --out）：兼容旧直接调用 / 单测，处理单个文件即退出。
- 常驻服务（--server）：**启动一次、加载一次 AutoModel，通过 stdin/stdout JSON 行协议
  处理任意多个文件**（批量场景下 FunASR 整组复用，避免逐视频 reload 模型）。
  模块侧 `transcribe_audio_funasr` 默认走常驻服务（首调懒启动、进程级复用）；
  常驻服务不可用则自动回退一次性 subprocess。

环境变量：
- ASR_BACKEND=funasr|auto  由 asr.py 读取（auto=按语言检测路由）。
- FUNASR_VENV_PY       funasr venv 的 python 路径（默认 ~/.venvs/funasr/Scripts/python.exe）。
- ASR_HOTWORDS         分号分隔的领域热词（如 "量化宽松;ETF套利;北向资金"），注入解码提精度。
- FUNASR_MODELS_ROOT   模型根目录（默认 ~/.cache/asr_whisper/funasr_models）。
"""
import argparse
import json
import os
import sys
import subprocess
import tempfile
import wave
import atexit
import threading

FUNASR_MODELS_ROOT = (
    os.environ.get("FUNASR_MODELS_ROOT")
    or os.path.expanduser("~/.cache/asr_whisper/funasr_models")
)


# ---------------------------------------------------------------------------
# 模块级（被 asr.py 导入时只用到这里；不 import funasr）
# ---------------------------------------------------------------------------

def _asr_hotwords():
    hw = os.environ.get("ASR_HOTWORDS", "")
    return [h.strip() for h in hw.split(";") if h.strip()]


def _clean_env():
    """构造传给 funasr worker 子进程的干净环境。

    venv 运行时 PATH 默认混入 anaconda3 目录（含 2018 版 ucrtbase 等旧运行库），
    虽不是 sentencepiece segfault 的主因，但旧运行库可能干扰 torch/funasr 的 C 扩展加载。
    这里去掉所有 anaconda/conda 目录，让 worker 只找系统 + venv 自身的运行时。
    """
    env = os.environ.copy()
    p = env.get("PATH", "")
    kept = [x for x in p.split(";") if x and "anaconda" not in x.lower() and "conda" not in x.lower()]
    env["PATH"] = ";".join(kept)
    return env


def _resolve_funasr_python():
    """定位能跑 funasr 的 python：显式 env > 当前解释器（若已装）> 默认 venv。"""
    env = os.environ.get("FUNASR_VENV_PY")
    if env and os.path.exists(env):
        return env
    try:
        import funasr  # noqa: F401  —— 当前解释器已能 import 就复用
        return sys.executable
    except Exception:
        pass
    default = os.path.expanduser("~/.venvs/funasr/Scripts/python.exe")
    if os.path.exists(default):
        return default
    raise RuntimeError(
        "FunASR 未安装。请运行：python scripts/setup_funasr.py\n"
        f"（会在独立 venv 安装 torch+funasr+modelscope，并把模型下到 {FUNASR_MODELS_ROOT}）"
    )


# ---- 常驻 worker 管理（进程级复用，批量场景避免逐视频 reload）----

_WORKER = {"proc": None, "lock": threading.Lock()}


def _start_worker():
    """懒启动常驻 worker 子进程；已存活则直接返回。返回 Popen 或 None。"""
    with _WORKER["lock"]:
        proc = _WORKER.get("proc")
        if proc is not None and proc.poll() is None:
            return proc
        py = _resolve_funasr_python()
        script = os.path.abspath(__file__)
        cmd = [py, script, "--server", "--models-root", FUNASR_MODELS_ROOT]
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1, env=_clean_env(),
            )
        except Exception as e:
            print(f"   ❌ FunASR 常驻 worker 启动失败: {e}")
            return None
        # 等待 READY 握手（模型加载完成）；import 期的 notice 噪声行直接跳过。
        try:
            while True:
                line = proc.stdout.readline()
                if not line:                      # EOF：worker 已崩溃
                    break
                if line.startswith("READY"):
                    break
                # 非 READY 的前导噪声（torchaudio notice 等）忽略，继续读
        except Exception:
            line = ""
        if not line.startswith("READY"):
            try:
                proc.kill()
            except Exception:
                pass
            print(f"   ❌ FunASR 常驻 worker 未就绪: {line.strip()[:200]}")
            return None
        _WORKER["proc"] = proc
        print("   ✅ FunASR 常驻 worker 就绪（模型已加载，整组复用）")
        return proc


def _worker_request(wav, hotwords):
    """经常驻 worker 转写单个 wav。返回 segments 或 None（worker 异常时）。"""
    proc = _start_worker()
    if proc is None:
        return None
    payload = json.dumps({"wav": wav, "hotwords": hotwords or []}, ensure_ascii=False)
    try:
        with _WORKER["lock"]:
            proc.stdin.write(payload + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
    except Exception as e:
        _WORKER["proc"] = None
        print(f"   ❌ FunASR worker 通信失败: {e}")
        return None
    if not line:
        _WORKER["proc"] = None
        return None
    try:
        resp = json.loads(line)
    except Exception:
        return None
    if not resp.get("ok"):
        print(f"   ⚠️ FunASR worker 报错: {resp.get('error', 'unknown')}")
        return None
    return resp.get("segments")


def close_funasr_worker():
    """终止常驻 worker（进程退出时自动调用；也可显式释放 VRAM）。"""
    proc = _WORKER.get("proc")
    if proc is not None:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass
        _WORKER["proc"] = None


atexit.register(close_funasr_worker)


def transcribe_audio_funasr(wav, hotwords=None, device="auto"):
    """被 asr.py 调用的入口：优先走常驻 worker（整组复用），失败回退一次性 subprocess。

    Returns: [{start, duration, text}, ...]（与 whisper 端 schema 对齐）；失败返回 None。
    """
    hotwords = hotwords if hotwords is not None else _asr_hotwords()
    segs = _worker_request(wav, hotwords)
    if segs is not None:
        if segs:
            print(f"   ✅ FunASR 完成（{len(segs)} 段，常驻 worker）")
            return segs
        print("   ❌ FunASR 结果为空")
        return None
    # 常驻 worker 不可用 → 一次性 subprocess 兜底
    return _transcribe_oneoff(wav, hotwords, device)


def _transcribe_oneoff(wav, hotwords, device="auto"):
    py = _resolve_funasr_python()
    script = os.path.abspath(__file__)
    tmp = tempfile.NamedTemporaryFile(prefix="funasr_out_", suffix=".json", delete=False)
    tmp.close()
    cmd = [py, script, "--wav", wav, "--out", tmp.name,
           "--models-root", FUNASR_MODELS_ROOT, "--device", device]
    if hotwords:
        cmd += ["--hotwords", ";".join(hotwords)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=_clean_env())
    except subprocess.TimeoutExpired:
        print("   ❌ FunASR 转写超时（30min 上限）")
        return None
    except Exception as e:
        print(f"   ❌ FunASR 派发失败: {e}")
        return None
    if proc.returncode != 0:
        print(f"   ❌ FunASR 转写失败(rc={proc.returncode}): {proc.stderr[-800:]}")
        return None
    try:
        with open(tmp.name, encoding="utf-8") as f:
            data = json.load(f)
        segs = data.get("segments")
        if not segs:
            print("   ❌ FunASR 结果为空")
            return None
        print(f"   ✅ FunASR 完成（{len(segs)} 段）")
        return segs
    except Exception as e:
        print(f"   ❌ FunASR 结果解析失败: {e} | stderr={proc.stderr[-300:]}")
        return None
    finally:
        try:
            os.remove(tmp.name)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 以下仅在「被 funasr venv 的 python 直接运行」时执行
# ---------------------------------------------------------------------------

def _wav_duration(wav):
    try:
        with wave.open(wav, "rb") as wf:
            fr = wf.getframerate()
            n = wf.getnframes()
            if fr:
                return n / fr
    except Exception:
        pass
    try:
        return os.path.getsize(wav) / 32000.0
    except Exception:
        return 0.0


def _split_sentences(text):
    import re
    parts = re.split(r"(?<=[。！？!?；;　\n])", text)
    return [p.strip() for p in parts if p.strip()]


def _timestamp_scale(timestamps, dur):
    flat = [t for pair in timestamps for t in pair if isinstance(t, (int, float))]
    if not flat or dur <= 0:
        return 1.0
    max_ts = max(flat)
    return 0.001 if max_ts > dur * 1.5 else 1.0


def _build_segments(raw, dur):
    # 方案优先级：
    # A) 有整段带标点文本(text) + VAD 时间轴(sentence_info)：把标点句按时间比例映射到 VAD 段，
    #    每段既带时间轴又带标点（最理想：内容质量来自 ct-punc，时间轴来自 VAD）。
    # B) 仅整段带标点文本：单段返回（内容优先）。
    # C) 仅 sentence_info（无标点 VAD 段）：保留时间轴，文本无标点（兜底）。
    text = (raw.get("text") or "").strip()
    si = raw.get("sentence_info")
    if text and si:
        bounds = []
        for s in si:
            st = float(s.get("start") or 0) / 1000.0
            en = float(s.get("end") or 0) / 1000.0
            bounds.append((st, max(en, st)))
        total = bounds[-1][1] if bounds else dur
        sents = _split_sentences(text)
        n = len(sents)
        if n:
            centers = [(st + en) / 2.0 for st, en in bounds]
            buckets = [[] for _ in bounds]
            for i, sent in enumerate(sents):
                ratio = (i + 0.5) / n
                k = min(range(len(bounds)),
                         key=lambda kk: abs(centers[kk] - ratio * total))
                buckets[k].append(sent)
            out = []
            for k, (st, en) in enumerate(bounds):
                t = "".join(buckets[k]).strip()
                if t:
                    out.append({"start": st, "duration": max(0.0, en - st), "text": t})
            if out:
                return out
    if text:
        return [{"start": 0.0, "duration": dur, "text": text}]
    if si:
        return [{"start": float(s.get("start") or 0) / 1000.0,
                 "duration": max(0.0, float(s.get("end") or 0) / 1000.0
                                 - float(s.get("start") or 0) / 1000.0),
                 "text": (s.get("text") or "").strip()} for s in si]
    return []


def _transcribe_one(model, wav, hotwords, device):
    """用已加载的 model 转写单个 wav，返回 segments。"""
    hotword = ";".join([h for h in (hotwords or []) if h]) or None
    gen_kwargs = dict(input=wav, batch_size_s=300, sentence_timestamp=True)
    if hotword:
        gen_kwargs["hotword"] = hotword
    res = model.generate(**gen_kwargs)
    raw = res[0] if res else {}
    dur = _wav_duration(wav)
    return _build_segments(raw, dur)


def _worker_server():
    """常驻服务模式：加载一次模型，循环处理 stdin 上的 JSON 请求。

    协议（stdout，每行一条 JSON）：
      握手：首行 "READY"（模型加载完成后发出）
      请求：{"wav": "...", "hotwords": ["...", ...]}
      响应：{"ok": true, "segments": [...]} / {"ok": false, "error": "..."}
    """
    import contextlib
    import torch
    from funasr import AutoModel

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    root = os.environ.get("FUNASR_MODELS_ROOT") or FUNASR_MODELS_ROOT

    def mdir(name):
        d = os.path.join(root, name)
        return d if os.path.isdir(d) else name

    @contextlib.contextmanager
    def _silence():
        # funasr/torchaudio 在 import 与推理时会向 stdout 打 notice/进度，必须静默，
        # 否则会混进 JSON 协议行、污染握手与响应解析。
        old = sys.stdout
        sys.stdout = open(os.devnull, "w")
        try:
            yield
        finally:
            try:
                sys.stdout.close()
            except Exception:
                pass
            sys.stdout = old

    # 静默 import 期输出（torchaudio 会在 import 时向 stdout 打 "ffmpeg is not installed" 等 notice）
    with _silence():
        import torch  # noqa: F401（确保 device 判断前已可用）
        from funasr import AutoModel
    with _silence():
        print(f"   🎛️ 加载 FunASR Paraformer-zh（device={device}）...", flush=True)
        model = AutoModel(
            model=mdir("paraformer-zh"),
            vad_model=mdir("fsmn-vad"),
            punc_model=mdir("ct-punc"),
            device=device,
        )
    sys.stdout.write("READY\n")          # 握手：模型已就绪
    sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            sys.stdout.write(json.dumps({"ok": False, "error": "bad json"}) + "\n")
            sys.stdout.flush()
            continue
        wav = req.get("wav")
        hotwords = req.get("hotwords") or []
        if not wav or not os.path.exists(wav):
            sys.stdout.write(json.dumps({"ok": False, "error": "missing wav"}) + "\n")
            sys.stdout.flush()
            continue
        try:
            with _silence():
                segs = _transcribe_one(model, wav, hotwords, device)
            sys.stdout.write(json.dumps({"ok": True, "segments": segs or []},
                                       ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stdout.write(json.dumps({"ok": False, "error": str(e)[:500]}) + "\n")
            sys.stdout.flush()


def _worker_main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--models-root", required=True)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--hotwords", default="")
    args = ap.parse_args()

    import torch
    from funasr import AutoModel

    device = args.device
    if device == "auto":
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
    root = args.models_root

    def mdir(name):
        d = os.path.join(root, name)
        return d if os.path.isdir(d) else name

    print(f"   🎛️ 加载 FunASR Paraformer-zh（device={device}）...", flush=True)
    model = AutoModel(
        model=mdir("paraformer-zh"),
        vad_model=mdir("fsmn-vad"),
        punc_model=mdir("ct-punc"),
        device=device,
    )
    hotwords = [h for h in args.hotwords.split(";") if h] or None
    segs = _transcribe_one(model, args.wav, hotwords, device)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"segments": segs or []}, f, ensure_ascii=False)
    print(f"   ✅ FunASR worker 写出 {len(segs or [])} 段", flush=True)


if __name__ == "__main__":
    if "--server" in sys.argv:
        _worker_server()
    else:
        _worker_main()
