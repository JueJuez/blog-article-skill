"""videos/multimodal.py — 多模态视频理解（P4，可选）

对画面型内容（无字幕/字幕信息量低），采样帧 + Gemini 视频理解，产出含视觉信息的总结。
best-effort、非阻断：无 Gemini / ffmpeg / 下载失败均优雅跳过，交回字幕/ASR 路径。

支持两类输入：
  - 本地视频/音频文件路径（直接采样帧）
  - 视频 URL（best-effort 用 yt-dlp 下载到临时文件后再采样；带超时，失败即跳过）
"""

import os
import tempfile
import threading
from typing import Optional, List


def _has_ffmpeg() -> bool:
    try:
        import subprocess
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False


def sample_frames(path: str, n: int = 4) -> List[str]:
    """用 ffmpeg 从视频均匀采样 n 帧，返回图片路径列表。"""
    if not _has_ffmpeg():
        print("⚠️ 未检测到 ffmpeg，无法采样帧（多模态理解需要 ffmpeg）")
        return []
    tmpdir = tempfile.mkdtemp(prefix="frames_")
    try:
        import subprocess
        dur = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=True
        ).stdout.strip()
        duration = float(dur) if dur else 0.0
        if duration <= 0:
            return []
        frames = []
        for i in range(n):
            t = duration * (i + 0.5) / n
            out = os.path.join(tmpdir, f"frame_{i}.jpg")
            subprocess.run(
                ["ffmpeg", "-y", "-ss", str(t), "-i", path, "-frames:v", "1", out],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
            )
            if os.path.exists(out):
                frames.append(out)
        return frames
    except Exception as e:
        print(f"⚠️ 帧采样失败: {e}")
        return []


def _download_for_multimodal(url: str, timeout: int = 60) -> Optional[str]:
    """best-effort 用 yt-dlp 把视频下载到临时文件，便于采样帧。超时/失败返回 None。

    用 daemon 线程 + join(timeout) 避免下载卡死时阻塞主流程（ThreadPoolExecutor 在
    result 超时后退出 with 块会 join 仍在运行的线程，造成死锁）。
    """
    try:
        import yt_dlp
    except ImportError:
        print("⚠️ 未安装 yt-dlp（pip install yt-dlp），无法为多模态下载视频")
        return None

    tmpdir = tempfile.mkdtemp(prefix="mm_dl_")
    outtmpl = os.path.join(tmpdir, "%(id)s.%(ext)s")
    result: dict = {"path": None}

    def _worker():
        try:
            ydl_opts = {
                "format": "mp4/best[filesize<200M]",
                "outtmpl": outtmpl,
                "quiet": True,
                "no_warnings": True,
                "noprogress": True,
                "socket_timeout": 15,
                "max_filesize": 200 * 1024 * 1024,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(url, download=True)
            for f in os.listdir(tmpdir):
                if f.endswith((".mp4", ".webm", ".mkv", ".mov")):
                    result["path"] = os.path.join(tmpdir, f)
                    return
        except Exception:
            pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout)
    return result["path"]


def _gemini_understand(frames: List[str], vision_prompt: str) -> Optional[str]:
    """用 Gemini 对采样帧做理解，返回文本。无 Gemini 返回 None。"""
    from articles.ai_provider import get_ai_provider
    provider = get_ai_provider("google")
    if provider is None or provider.name != "google":
        print("⚠️ 多模态理解需要已配置的 Google Gemini Provider，跳过")
        return None
    try:
        import google.genai as genai
        from google.genai import types
        genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
        client = genai.Client()
        model = os.getenv("GOOGLE_MODEL", "gemini-2.0-flash")
        contents = [vision_prompt, *[types.Part.from_bytes(
            data=open(f, "rb").read(), mime_type="image/jpeg") for f in frames]]
        resp = client.models.generate_content(model=model, contents=contents)
        return resp.text.strip() if resp and resp.text else None
    except Exception as e:
        print(f"⚠️ Gemini 多模态理解失败: {e}")
        return None


def analyze(url_or_path: str, note_type: str = "key_points",
            prompt: str = "") -> Optional[str]:
    """统一入口：对本地文件或 URL 做多模态理解，返回视觉上下文文本或 None。

    best-effort、非阻断：任意环节失败都返回 None，交回字幕/ASR 路径。
    """
    if not url_or_path:
        return None

    vision_prompt = (prompt or
                     "请基于这些视频帧，描述画面中的关键信息：场景、图表、演示内容、"
                     "产品界面、代码示例等，用于辅助生成视频总结（只输出有助于总结的画面信息）。")

    # 1) 取得本地视频路径（已存在文件直接复用，否则 best-effort 下载）
    local_path: Optional[str] = None
    is_temp = False
    if os.path.exists(url_or_path):
        local_path = url_or_path
    else:
        local_path = _download_for_multimodal(url_or_path)
        is_temp = local_path is not None

    if not local_path or not os.path.exists(local_path):
        return None

    try:
        frames = sample_frames(local_path, n=4)
        if not frames:
            return None
        return _gemini_understand(frames, vision_prompt)
    finally:
        if is_temp:
            try:
                os.remove(local_path)
                os.rmdir(os.path.dirname(local_path))
            except Exception:
                pass


def understand_video(path: str, prompt: str, note_type: str = "key_points") -> Optional[str]:
    """兼容旧接口：对本地文件路径做多模态理解。"""
    return analyze(path, note_type=note_type, prompt=prompt)
