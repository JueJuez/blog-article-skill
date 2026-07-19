"""videos/asr.py — 本地/任意视频 ASR 转写（P3）

对本地视频文件或无字幕链接，用 yt-dlp 抽音频 → faster-whisper 本地免费转写。

注意（PRD 风险边界）：
- 需要 ffmpeg（音频抽取）与 faster-whisper（模型首次下载 ~150MB–1.5GB）。
- 24h 超长视频不现实，优先 CC 或先裁片。
- 依赖缺失时优雅提示，不阻断主流程。
"""

import os
from typing import Optional, Tuple, List, Dict


def _has_ffmpeg() -> bool:
    try:
        subprocess = __import__("subprocess")
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False


def extract_audio(path: str, out_wav: str) -> bool:
    """用 yt-dlp 从视频/音频抽出 wav（依赖 ffmpeg）。"""
    try:
        import yt_dlp
    except ImportError:
        print("⚠️ 未安装 yt-dlp（pip install yt-dlp）")
        return False
    if not _has_ffmpeg():
        print("⚠️ 未检测到 ffmpeg，无法抽取音频（请安装 ffmpeg 并加入 PATH）")
        return False
    try:
        ydl_opts = {
            "format": "bestaudio/best",
            "extract_audio": True,
            "audio_format": "wav",
            "outtmpl": out_wav,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
            }],
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(path, download=True)
        return os.path.exists(out_wav)
    except Exception as e:
        print(f"❌ 音频抽取失败: {e}")
        return False


def transcribe_file(path: str, model_size: str = "base", language: Optional[str] = None) -> Optional[Tuple[str, List[Dict]]]:
    """转写本地视频/音频文件 → (title, segments)。

    支持本地路径或可直接下载的 URL（yt-dlp 支持的源）。
    """
    try:
        import faster_whisper
    except ImportError:
        print("⚠️ 未安装 faster-whisper（pip install faster-whisper），无法做本地 ASR")
        return None

    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="asr_")
    wav = os.path.join(tmpdir, "audio.wav")
    if not extract_audio(path, wav):
        return None

    try:
        print(f"   🎙️ faster-whisper 转写中（model={model_size}）...")
        model = faster_whisper.WhisperModel(model_size, device="cpu", compute_type="int8")
        segments_gen, info = model.transcribe(wav, language=language, beam_size=5)
        segments = []
        for s in segments_gen:
            segments.append({
                "start": float(s.start),
                "duration": float(s.end - s.start),
                "text": s.text.strip(),
            })
        if os.path.exists(wav):
            os.remove(wav)
        if not segments:
            print("❌ ASR 结果为空")
            return None
        title = os.path.splitext(os.path.basename(path))[0]
        print(f"   ✅ ASR 完成（{len(segments)} 段）")
        return (title, segments)
    except Exception as e:
        print(f"❌ ASR 转写失败: {e}")
        return None


def transcribe_url(url: str, model_size: str = "base", language: Optional[str] = None) -> Optional[Tuple[str, List[Dict]]]:
    """对可直接下载的 URL 做 ASR 转写（兜底：无 CC 的公开视频）。"""
    return transcribe_file(url, model_size=model_size, language=language)
