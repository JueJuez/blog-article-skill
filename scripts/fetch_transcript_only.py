"""Fetch a video's subtitles/transcript and save to a file (no AI summary).

Usage:
  python scripts/fetch_transcript_only.py "<youtube-or-bilibili-url>" [output_path]

Outputs a plain-text transcript file with timestamps. The video ID (or title)
is used as the filename when output_path is omitted.
"""
import os
import sys
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)


def fmt_ts(sec: float) -> str:
    sec = int(round(sec))
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_transcript(title: str, payload) -> str:
    lines = [f"# {title}", ""]
    if isinstance(payload, str):
        lines.append(payload.strip())
        return "\n".join(lines)
    # list of dicts: {text, start, duration}
    for seg in payload:
        text = seg.get("text", "").strip()
        if not text:
            continue
        ts = fmt_ts(seg.get("start", 0.0))
        lines.append(f"[{ts}] {text}")
    return "\n".join(lines)


def safe_name(s: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|]+", "_", s).strip()
    return s[:80] or "transcript"


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/fetch_transcript_only.py <url> [output_path]")
        return 1
    url = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else None

    from videos.fetch import fetch_transcript, _yt_video_id
    res = fetch_transcript(url)
    if not res:
        print("\n❌ 未能获取字幕（API 与 CDP 回退均失败，可能该视频无字幕或网络不可达）")
        return 1

    title, payload = res
    text = format_transcript(title or _yt_video_id(url) or "", payload)

    out_dir = os.path.join(BASE_DIR, "transcripts")
    os.makedirs(out_dir, exist_ok=True)
    if not out_path:
        vid = _yt_video_id(url) or safe_name(title)
        out_path = os.path.join(out_dir, f"{vid}.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"\n✅ 字幕已保存: {out_path}")
    print(f"   标题: {title}")
    n = len(payload) if isinstance(payload, list) else len(payload.splitlines())
    print(f"   片段数/行数: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
