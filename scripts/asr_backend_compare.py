#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""同一段音频分别用 whisper 与 funasr 两个后端转写，输出对照报告。

用途：验证「换中文特化后端是否真的更准」。两种用法：
  1) 本地音频/视频文件：
     python scripts/asr_backend_compare.py path/to/video.mkv
  2) 链接（会先下音频一次，两个后端复用同一份 wav）：
     python scripts/asr_backend_compare.py "https://www.bilibili.com/video/BVxxxx"

结果：两份转写文本分别存到 transcripts/compare_<key>_{whisper,funasr}.md，
并打印字符数、行数、两版字符级相似度（Levenshtein），供人工/后续比对。
"""
import os
import sys
import json
import tempfile
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from videos.asr import extract_audio, _ffmpeg_exe, _transcript_cache_path  # noqa: E402


def _levenshtein(a, b):
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[lb]


def _similarity(a, b):
    if not a and not b:
        return 1.0
    dist = _levenshtein(a, b)
    return 1.0 - dist / max(len(a), len(b), 1)


def _transcribe_with_backend(wav, backend):
    env = dict(os.environ)
    env["ASR_BACKEND"] = backend
    env["ASR_DEVICE"] = "cuda"
    # 复用 asr.py 的转写入口（按 backend 分派）
    code = (
        "import sys, json; sys.path.insert(0, %r);\n"
        "from videos.asr import transcribe_audio_chunked;\n"
        "segs = transcribe_audio_chunked(%r, device='cuda');\n"
        "text = '\\n'.join(s['text'] for s in segs) if segs else '';\n"
        "print('\\x00RESULT\\x00' + json.dumps({'text': text}))\n"
    ) % (ROOT, wav)
    p = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True)
    out = p.stdout
    marker = "\x00RESULT\x00"
    if marker in out:
        payload = out.split(marker, 1)[1].strip()
        try:
            return json.loads(payload).get("text", "")
        except Exception:
            pass
    print(f"   [{backend}] stderr 末段: {p.stderr[-400:]}")
    return ""


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/asr_backend_compare.py <本地文件|链接>")
        return
    src = sys.argv[1]

    tmp = tempfile.mkdtemp(prefix="asr_cmp_")
    wav = os.path.join(tmp, "audio.wav")
    if os.path.exists(src):
        print("本地文件，抽取音频…")
        if not extract_audio(src, wav, ffmpeg_exe=_ffmpeg_exe()):
            print("❌ 音频抽取失败"); return
    else:
        print("远程链接，走 transcribe_video 的下载逻辑到临时 wav…")
        # 借用 transcribe_video 内部下载：直接调 extract_audio（支持链接）
        if not extract_audio(src, wav, ffmpeg_exe=_ffmpeg_exe()):
            print("❌ 音频下载失败"); return

    print("=== whisper (faster-whisper medium) ===")
    whisp = _transcribe_with_backend(wav, "whisper")
    print("=== funasr (Paraformer-zh) ===")
    fun = _transcribe_with_backend(wav, "funasr")

    out_dir = os.path.join(ROOT, "transcripts")
    os.makedirs(out_dir, exist_ok=True)
    key = os.path.splitext(os.path.basename(src))[0][:40] or "compare"
    wp = os.path.join(out_dir, f"compare_{key}_whisper.md")
    fp = os.path.join(out_dir, f"compare_{key}_funasr.md")
    with open(wp, "w", encoding="utf-8") as f:
        f.write(whisp)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(fun)

    sim = _similarity(whisp, fun)
    print("\n================ 对照报告 ================")
    print(f"whisper 字符数 : {len(whisp)}")
    print(f"funasr  字符数 : {len(fun)}")
    print(f"两版字符级相似度: {sim*100:.1f}%")
    print(f"whisper 输出 : {wp}")
    print(f"funasr  输出 : {fp}")
    print("（无官方字幕作真值时，相似度低≠谁更准；请人工对照术语/同音错处）")


if __name__ == "__main__":
    main()
