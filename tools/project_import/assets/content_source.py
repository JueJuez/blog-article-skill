"""Input routing for project_import.

Given a URL or free text, produce the text(s) to scan for repo references.

Three input kinds:
  - direct repo links   : free text containing github.com / gitee.com URLs
  - article URL         : article body via articles.fetch.fetch_web_content
  - video URL           : description (PRIORITY) + subtitle/transcript
                          (B站 via _bili_get_video_info + fetch_bilibili_transcript;
                           YouTube via watch-page shortDescription + fetch_youtube_transcript)

Per product decision: for videos, scan the **description first** — subtitles often
only mention a repo *name* without a URL, while the actual link lives in the
description. URL-only extraction (phase 1) means name-only mentions are ignored
for now; name→URL resolution is a later phase.
"""
import os
import re
import sys

# Make the parent project importable so `import videos.fetch` / `import articles.fetch`
# works whether this module is run as a script, imported by tests, or invoked by the agent.
_HERE = os.path.dirname(os.path.abspath(__file__))            # .../assets
_PROJECT_IMPORT_DIR = os.path.dirname(_HERE)                  # .../tools/project_import
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_PROJECT_IMPORT_DIR))  # .../blog-article-skill
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from assets.extractor import extract_repo_urls  # noqa: E402

_BILI_RE = re.compile(r"(bilibili\.com/video|b23\.tv)", re.I)
_YT_RE = re.compile(r"(youtube\.com/watch|youtu\.be)", re.I)
_REPO_HOST_RE = re.compile(r"github\.com|gitee\.com", re.I)


class SourceText:
    """One chunk of text to scan for repo URLs."""

    def __init__(self, kind: str, text: str, platform: str = None):
        self.kind = kind            # 'description' | 'subtitle' | 'body' | 'direct'
        self.text = text or ""
        self.platform = platform

    def __repr__(self):
        return f"<SourceText {self.kind} {self.platform or ''} len={len(self.text)}>"


def _is_video_url(url: str) -> bool:
    return bool(_BILI_RE.search(url) or _YT_RE.search(url))


def _bili_description(url: str) -> str:
    from videos.fetch import _bili_get_video_info, _bili_extract_bvid
    bvid = _bili_extract_bvid(url)
    if not bvid:
        return ""
    info = _bili_get_video_info(bvid)
    return (info or {}).get("desc", "") if info else ""


def _bili_subtitle(url: str) -> str:
    from videos.fetch import fetch_bilibili_transcript
    res = fetch_bilibili_transcript(url, lang="zh")
    if not res:
        return ""
    text, *_ = res
    return text or ""


def _yt_description(url: str) -> str:
    import requests
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code == 200:
            # shortDescription inside ytInitialPlayerResponse is JSON-escaped; this
            # pattern tolerates escaped quotes (\").
            m = re.search(r'"shortDescription"\s*:\s*"((?:[^"\\]|\\.)*)"', r.text)
            if m:
                return m.group(1).encode("utf-8").decode("unicode_escape")
    except Exception:
        pass
    return ""


def _yt_subtitle(url: str) -> str:
    from videos.fetch import fetch_youtube_transcript
    res = fetch_youtube_transcript(url)
    if not res:
        return ""
    _, body = res
    if isinstance(body, list):
        return "\n".join(seg.get("text", "") for seg in body)
    return body or ""


def resolve(input_spec: str):
    """Resolve an input into (list[SourceText], platform).

    input_spec: a URL (article / video) or free text (may contain repo links).
    """
    text = (input_spec or "").strip()
    if not text:
        return [], None

    # Free text that itself contains repo links -> scan as-is (direct).
    # This must come BEFORE the "starts with http" check, otherwise a sentence
    # like "https://github.com/a/b https://github.com/c/d" would be mistaken
    # for a single article URL and trigger a real network fetch.
    if extract_repo_urls(text):
        return [SourceText("direct", text)], "direct"

    if not text.startswith("http"):
        return [SourceText("direct", text)], "direct"

    if _is_video_url(text):
        platform = "bilibili" if _BILI_RE.search(text) else "youtube"
        if platform == "bilibili":
            desc, sub = _bili_description(text), _bili_subtitle(text)
        else:
            desc, sub = _yt_description(text), _yt_subtitle(text)
        sources = []
        # Description FIRST (per product decision).
        if desc.strip():
            sources.append(SourceText("description", desc, platform))
        if sub.strip():
            sources.append(SourceText("subtitle", sub, platform))
        return (sources, platform) if sources else ([], platform)

    # Otherwise treat as an article URL.
    from articles.fetch import fetch_web_content
    res = fetch_web_content(text)
    if not res:
        return [], "article"
    _, content, _ = res
    return [SourceText("body", content or "", "article")], "article"
