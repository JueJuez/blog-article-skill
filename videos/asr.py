"""videos/asr.py — 本地/任意视频 ASR 转写（P3）

对本地视频文件或无字幕链接，用 yt-dlp 抽音频 → faster-whisper 本地免费转写。
作为「抓取不到字幕」时的自动兜底（用户规则 2026-08-06：抓不到字幕即自动走 ASR）。

依赖（managed venv，已装）：
- yt-dlp         视频/音频下载与抽取
- faster-whisper 本地 ASR（CTranslate2 引擎）
- imageio-ffmpeg 内嵌 ffmpeg 二进制（免系统安装，解决本机无 ffmpeg 问题）

环境变量：
- ASR_MODEL   模型大小/名称（默认 medium；可设 large-v3 / Belle-faster-whisper-large-v3-zh-punct 等）
- ASR_DEVICE  auto|cpu|cuda（默认 auto：有 CUDA 走 cuda/float16，否则 CPU/int8）
- ASR_LANG    转写语言（默认 zh）

注意（PRD 风险边界）：
- 首次运行会下载 Whisper 模型（medium ~1.5GB / large-v3 ~3GB），需联网。
- 24h 超长视频不现实，优先 CC 或先裁片。
- 依赖缺失或下载失败均优雅提示，不阻断主流程。
"""

import os
import re
import tempfile
import hashlib
from typing import Optional, Tuple, List, Dict

try:
    from huggingface_hub import snapshot_download
    _HAS_HF_HUB = True
except Exception:
    _HAS_HF_HUB = False


# ---------------------------------------------------------------------------
# 环境坑自动处理（本机沙箱/Windows 已知，避免每次手敲 export 重踩）
# ---------------------------------------------------------------------------

def _apply_env_defaults():
    """一次性设好本机已知的环境坑（仅当未显式设置），覆盖所有走
    huggingface_hub / ctranslate2 的入口。调用点：_load_model / transcribe_video。

    已解决的问题（2026-08-06 实跑踩坑记录）：
    - HF_HUB_DISABLE_XET=1      : 关 xet 传输后端（否则直连 cas-server.xethub 返回 401）
    - HF_HUB_ENABLE_HF_TRANSFER=0: 关 hf_transfer（与 xet 同类）
    - HF_HOME=系统临时目录       : 默认 cache 的 .incomplete 清理会被沙箱安全删除拦截 →
                                  改放 %TEMP%，其中的 unlink 走原生、不受拦截
    - HF_ENDPOINT=镜像          : 沙箱直连 huggingface.co 超时 → 自动走 hf-mirror.com
    """
    if not os.environ.get("HF_HUB_DISABLE_XET"):
        os.environ["HF_HUB_DISABLE_XET"] = "1"
    if not os.environ.get("HF_HUB_ENABLE_HF_TRANSFER"):
        os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
    if not os.environ.get("HF_HOME"):
        # 固定用户缓存目录（~/.cache/asr_whisper），不放系统 Temp ——
        # 避免 Temp 被磁盘清理误删导致 1.5GB 模型丢失重下。
        os.environ["HF_HOME"] = os.path.expanduser("~/.cache/asr_whisper")
    if not os.environ.get("HF_ENDPOINT"):
        import urllib.request as _u
        try:
            _u.urlopen("https://huggingface.co", timeout=8)
        except Exception:
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"


# ---------------------------------------------------------------------------
# 转写结果缓存（断点续跑：避免重复下载音频 + 重跑 GPU 转写）
# ---------------------------------------------------------------------------

_TRANSCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "transcripts")


def _transcript_cache_path(url: str) -> str:
    os.makedirs(_TRANSCRIPTS_DIR, exist_ok=True)
    try:
        from . import fetch as _f
        if _f.is_bilibili(url):
            key = _f._bili_extract_bvid(url) or url
        elif _f.is_youtube(url):
            key = re.sub(r'[^A-Za-z0-9]', '_', url)[:64]
        else:
            key = hashlib.md5(url.encode("utf-8")).hexdigest()[:16]
    except Exception:
        key = hashlib.md5(url.encode("utf-8")).hexdigest()[:16]
    return os.path.join(_TRANSCRIPTS_DIR, f"{key}.md")


def _load_cached_transcript(url: str) -> Optional[str]:
    """命中缓存返回纯文本；否则 None。缓存仅存文本（标题/作者由 B站 API 现取）。"""
    p = _transcript_cache_path(url)
    if os.path.exists(p) and os.path.getsize(p) > 0:
        try:
            with open(p, encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None
    return None


def _save_transcript_cache(url: str, text: str) -> None:
    p = _transcript_cache_path(url)
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 运行环境探测
# ---------------------------------------------------------------------------

_KNOWN_SIZES = {"tiny", "base", "small", "medium", "large-v2", "large-v3"}


def _resolve_repo_id(model_size: str) -> str:
    """把 model_size 解析成 HuggingFace repo id。"""
    ms = (model_size or "medium").strip()
    if "/" in ms:                      # 已经是完整 repo id
        return ms
    if ms in _KNOWN_SIZES:            # 标准尺寸 → Systran 官方镜像
        return f"Systran/faster-whisper-{ms}"
    return ms


def _resolve_local_model_dir(model_size: str) -> str:
    """把模型下载/解析到「真实文件」本地目录（不用 blobs+symlink 机制），
    返回该目录路径。Windows 沙箱下 symlink 受限，必须落真实文件。

    已缓存的 blob 会被直接复用（local_dir 仅做一次拷贝），不会重下模型。
    """
    _apply_env_defaults()
    repo_id = _resolve_repo_id(model_size)
    safe = repo_id.replace("/", "--")
    base = os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/asr_whisper")
    local_dir = os.path.join(base, "asr_models", safe)
    model_bin = os.path.join(local_dir, "model.bin")
    if os.path.exists(model_bin):
        return local_dir
    os.makedirs(local_dir, exist_ok=True)
    if not _HAS_HF_HUB:
        raise RuntimeError("未安装 huggingface_hub（请 pip install huggingface-hub）")
    snapshot_download(
        repo_id,
        local_dir=local_dir,
        local_dir_use_symlinks=False,   # 关键：真实文件，不创建 symlink
    )
    return local_dir


def _ffmpeg_exe() -> Optional[str]:
    """优先用 imageio-ffmpeg 内嵌二进制（免系统安装），其次回退系统 PATH。"""
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        exe = get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    import shutil
    return shutil.which("ffmpeg")


def _resolve_device(device: str = "auto") -> Tuple[str, str]:
    """返回 (device, compute_type)。auto 时优先 CUDA/float16，否则 CPU/int8。"""
    if device == "cpu":
        return "cpu", "int8"
    if device == "cuda":
        return "cuda", "float16"
    # auto：看 ctranslate2 能否看到 CUDA 设备（CPU-only 的 wheel 会返回 0）
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


_MODEL_CACHE: Dict[Tuple[str, str, str], object] = {}


# ---------------------------------------------------------------------------
# CUDA 运行库自动定位（Windows 上 ctranslate2 需要 cublas/cudart dll 在 DLL 搜索路径）
# ---------------------------------------------------------------------------

_CUDA_DLL_SEARCHED = False


def _ensure_cuda_dlls():
    """把含 cublas64_12.dll 的目录加进进程 DLL 搜索路径（Windows 专用）。

    这样即使用户没手动配 PATH，只要本机有 CUDA 运行库（如 Lenovo/预装 NVIDIA 驱动
    附带的），GPU 转写就能直接生效；找不到则靠 transcribe 的 CPU 回退兜底。
    """
    global _CUDA_DLL_SEARCHED
    if _CUDA_DLL_SEARCHED:
        return
    _CUDA_DLL_SEARCHED = True
    import glob
    checked = set()
    # 已知位置（本机实测可用）
    known = [
        r"C:\Program Files\Lenovo\Lenovo.PFMService\app\modelService",
    ]
    # 浅层扫描 Program Files 下两级的 cublas64_12.dll（避免全盘递归过慢）
    known += glob.glob(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v*\bin")
    known += glob.glob(r"C:\Program Files\*\cublas64_12.dll")
    known += glob.glob(r"C:\Program Files\*\\*\cublas64_12.dll")
    for pather in known:
        d = pather if os.path.isdir(pather) else os.path.dirname(pather)
        if not d or d in checked:
            continue
        checked.add(d)
        if os.path.exists(os.path.join(d, "cublas64_12.dll")):
            try:
                os.add_dll_directory(d)
            except Exception:
                pass


def _load_model(model_size: str, device: str = "auto"):
    """加载（并缓存）faster-whisper 模型；CUDA 创建失败自动回退 CPU。

    模型先解析为真实本地目录（绕开 Windows 沙箱下 blobs+symlink 失败的坑），
    再传给 WhisperModel，避免 'Unable to open file model.bin'。
    """
    import faster_whisper
    _apply_env_defaults()        # 设好 HF 镜像 / xet / HF_HOME 等环境坑
    _ensure_cuda_dlls()          # 把 CUDA 运行库目录加进 DLL 搜索路径（若本机有）
    dev, ct = _resolve_device(device)
    try:
        model_path = _resolve_local_model_dir(model_size)
    except Exception as e:
        print(f"   ❌ 模型解析/下载失败：{e}")
        raise
    key = (model_path, dev, ct)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    try:
        print(f"   🎛️ 加载 Whisper 模型 {model_size}（device={dev}, compute_type={ct}）...")
        model = faster_whisper.WhisperModel(model_path, device=dev, compute_type=ct)
    except Exception as e:
        if dev == "cuda":
            print(f"   ⚠️ CUDA 加载失败（{e}），回退 CPU/int8")
            model = faster_whisper.WhisperModel(model_path, device="cpu", compute_type="int8")
        else:
            raise
    _MODEL_CACHE[key] = model
    return model


# ---------------------------------------------------------------------------
# 音频抽取（yt-dlp + 内嵌 ffmpeg）
# ---------------------------------------------------------------------------

def _build_ydl_opts(out_wav: str, cookie_str: Optional[str] = None,
                    ffmpeg_exe: Optional[str] = None) -> dict:
    opts = {
        "format": "bestaudio/best",
        "extract_audio": True,
        "audio_format": "wav",
        "outtmpl": os.path.splitext(out_wav)[0],  # yt-dlp 会补 .wav
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 30,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
        }],
    }
    if ffmpeg_exe:
        opts["ffmpeg_location"] = ffmpeg_exe
    if cookie_str:
        opts["http_headers"] = {"Cookie": cookie_str}
    # YouTube 走代理（若配置了 YT_PROXY）
    proxy = os.environ.get("YT_PROXY")
    if proxy:
        opts["proxy"] = proxy
    return opts


def extract_audio(source: str, out_wav: str,
                  cookie_str: Optional[str] = None,
                  ffmpeg_exe: Optional[str] = None) -> bool:
    """用 yt-dlp 从视频链接/本地文件抽出 wav（ffmpeg 用内嵌 exe，无需系统安装）。

    source: Bilibili/YouTube 链接 或 本地视频/音频路径。
    """
    try:
        import yt_dlp
    except ImportError:
        print("⚠️ 未安装 yt-dlp（pip install yt-dlp）")
        return False
    if not ffmpeg_exe and not _ffmpeg_exe():
        print("⚠️ 未找到 ffmpeg（请 pip install imageio-ffmpeg 或系统安装 ffmpeg）")
        return False
    try:
        opts = _build_ydl_opts(out_wav, cookie_str=cookie_str, ffmpeg_exe=ffmpeg_exe)
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(source, download=True)
        return os.path.exists(out_wav)
    except Exception as e:
        print(f"❌ 音频抽取失败: {e}")
        return False


# ---------------------------------------------------------------------------
# 转写
# ---------------------------------------------------------------------------

def transcribe_audio(wav: str, model_size: str = "medium",
                     language: Optional[str] = None,
                     device: str = "auto") -> Optional[List[Dict]]:
    """对本地 wav 做 faster-whisper 转写 → segments。

    segments schema 对齐 fetch 层：[{"start","duration","text"}, ...]
    device=auto 时若 CUDA 运行库缺失（cublas/cudnn dll 找不到），自动回退 CPU 重试。
    """
    model = _load_model(model_size, device)
    try:
        print(f"   🎙️ faster-whisper 转写中（model={model_size}, lang={language or 'auto'}）...")
        return _run_transcribe(model, wav, language)
    except Exception as e:
        # CUDA 运行库缺失等 GPU 错误 → 回退 CPU 重试一次（仅当未显式锁定 cuda）
        err = str(e)
        if device != "cuda" and ("cublas" in err or "cudnn" in err
                                 or "CUDA" in err or "not found or cannot be loaded" in err):
            print(f"   ⚠️ GPU 转写失败（{e}），回退 CPU/int8 重试")
            cpu_model = _load_model(model_size, "cpu")
            try:
                return _run_transcribe(cpu_model, wav, language)
            except Exception as e2:
                print(f"❌ CPU 重试仍失败: {e2}")
                return None
        print(f"❌ ASR 转写失败: {e}")
        return None


def _run_transcribe(model, wav: str, language: Optional[str]) -> Optional[List[Dict]]:
    segments_gen, info = model.transcribe(
        wav,
        language=language,
        beam_size=5,
        vad_filter=True,                 # 跳过静音段，长音频更稳
        condition_on_previous_text=False,
    )
    segments: List[Dict] = []
    for s in segments_gen:
        txt = (s.text or "").strip()
        if txt:
            segments.append({
                "start": float(s.start),
                "duration": float(s.end - s.start),
                "text": txt,
            })
    if not segments:
        print("❌ ASR 结果为空")
        return None
    print(f"   ✅ ASR 完成（{len(segments)} 段，检测语言 {info.language}）")
    return segments


# ---------------------------------------------------------------------------
# 对外：本地文件 / 链接
# ---------------------------------------------------------------------------

def transcribe_file(path: str, model_size: str = "medium",
                    language: Optional[str] = None,
                    device: str = "auto") -> Optional[Tuple[str, List[Dict]]]:
    """转写本地视频/音频文件 → (title, segments)。无作者（本地文件未知）。"""
    model_size = os.environ.get("ASR_MODEL") or model_size
    ffmpeg_exe = _ffmpeg_exe()
    tmpdir = tempfile.mkdtemp(prefix="asr_")
    wav = os.path.join(tmpdir, "audio.wav")
    try:
        if not extract_audio(path, wav, ffmpeg_exe=ffmpeg_exe):
            return None
        segs = transcribe_audio(wav, model_size, language, device)
        if not segs:
            return None
        title = os.path.splitext(os.path.basename(path))[0]
        return (title, segs)
    finally:
        _cleanup(tmpdir)


def transcribe_video(url: str, lang: str = "zh",
                     model_size: str = "medium",
                     device: str = "auto",
                     force: bool = False) -> Optional[Tuple[str, object, str]]:
    """对链接（Bilibili/YouTube 等 yt-dlp 支持的源）做 ASR 转写兜底。

    Returns: (title, segments_or_text, author)。
      - 默认把 segments 转成纯文本返回（下游 _summarize_and_save 同时支持 list/dict 与 str）；
        同时把文本缓存到 transcripts/<id>.md，供断点续跑与外层模型直接读取。
      - author 对 B站从 view API 取 UP主，其他来源回退空串。失败返回 None。
    """
    _apply_env_defaults()
    model_size = os.environ.get("ASR_MODEL") or model_size
    ffmpeg_exe = _ffmpeg_exe()

    # B站：注入登录态 cookie（部分视频匿名无法下载）；并取标题/UP主
    cookie_str = None
    title, author = "", ""
    try:
        from . import fetch
        if fetch.is_bilibili(url):
            cookie_str = fetch._bili_build_cookies_from_env()
            info = fetch._bili_get_video_info(fetch._bili_extract_bvid(url))
            if info:
                title = info.get("title", "")
                author = info.get("author", "")
    except Exception as e:
        print(f"   ℹ️ B站元数据/ cookie 获取跳过：{e}")

    # 断点续跑：非强制且命中 ASR 缓存 → 跳过下载音频 + GPU 转写，直接返回文本
    if not force:
        cached = _load_cached_transcript(url)
        if cached:
            print(f"   ♻️ 命中 ASR 缓存，跳过下载/转写（{len(cached)} 字）")
            return (title, cached, author)

    tmpdir = tempfile.mkdtemp(prefix="asr_")
    wav = os.path.join(tmpdir, "audio.wav")
    try:
        if not extract_audio(url, wav, cookie_str=cookie_str, ffmpeg_exe=ffmpeg_exe):
            if fetch and fetch.is_bilibili(url) and not cookie_str:
                print("   ℹ️ B站音频下载失败：可能需要登录态。请在本机 Chrome 登录 B站并"
                      "完全退出 Chrome 后重试（让 Playwright 能提取 cookie）。")
            return None
        segs = transcribe_audio(wav, model_size, lang, device)
        if not segs:
            return None
        text = "\n".join(s["text"] for s in segs)
        _save_transcript_cache(url, text)
        # 标题兜底：用文件名（去掉扩展）
        if not title:
            title = os.path.splitext(os.path.basename(wav))[0]
        return (title, text, author)
    finally:
        _cleanup(tmpdir)


def transcribe_url(url: str, model_size: str = "medium",
                   language: Optional[str] = None,
                   device: str = "auto",
                   force: bool = False) -> Optional[Tuple[str, object]]:
    """兼容旧签名：链接 → (title, segments_or_text)。author 丢弃。"""
    r = transcribe_video(url, lang=language or "zh", model_size=model_size,
                         device=device, force=force)
    if not r:
        return None
    return (r[0], r[1])


def _cleanup(tmpdir: str) -> None:
    try:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass


def safe_remove_one(path: str) -> bool:
    """只删明确指定的单个文件；拒绝 glob 通配与目录，防止误删。

    任何代码路径需要删除文件都应走这里，**绝不用 `glob` + `os.remove`**
    （曾因 `glob('notes/_raw_*.md')` 误删 50 个无关暂存文件）。
    """
    if not path or any(c in path for c in "*?[]"):
        print(f"   ⚠️ safe_remove_one 拒绝（疑似通配/非法路径）：{path!r}")
        return False
    if not os.path.isfile(path):
        return False
    try:
        os.remove(path)
        return True
    except Exception as e:
        print(f"   ⚠️ 删除失败：{path} -> {e}")
        return False
