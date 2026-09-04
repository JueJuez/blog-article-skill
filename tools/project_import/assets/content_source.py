"""Input routing for project_import.

Given a URL or free text, produce the text(s) to scan for repo references.

Three input kinds:
  - direct repo links   : free text containing github.com / gitee.com URLs
  - article URL         : article body via articles.fetch.fetch_web_content
  - video URL           : description (PRIORITY) + subtitle/transcript
                          (B站 via _bili_get_video_info + fetch_bilibili_transcript;
                           YouTube via watch-page shortDescription + fetch_youtube_transcript)

Plus xiaoheihe.cn posts: App share links are JS-rendered deep links; a generic
crawler only returns a placeholder, so we render them with a headless browser
(Node Playwright + system Chrome) and scan the rendered body text for repo URLs.

Per product decision: for videos, scan the **description first** — subtitles often
only mention a repo *name* without a URL, while the actual link lives in the
description. URL-only extraction (phase 1) means name-only mentions are ignored
for now; name→URL resolution is a later phase.
"""
import os
import re
import sys
import subprocess
import shutil
import glob

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
_XIAOHEIHE_RE = re.compile(r"xiaoheihe\.cn", re.I)
_REPO_HOST_RE = re.compile(r"github\.com|gitee\.com", re.I)

# Node Playwright + system Chrome used to render xiaoheihe.cn deep links.
# 解析 node 可执行文件：环境变量 > 系统 PATH > managed 目录（按用户名派生，不写死版本）
# > 回退到当前用户目录下的等价路径，避免「重装 / 换机器 / node 升版本」后硬路径失效。
def _find_node() -> str:
    env = os.environ.get("NODE_EXE")
    if env and os.path.isfile(env):
        return env
    on_path = shutil.which("node") or shutil.which("node.exe")
    if on_path:
        return on_path
    # managed 安装：~/.workbuddy/binaries/node/versions/<ver>/node.exe
    managed = os.path.join(
        os.path.expanduser("~"), ".workbuddy", "binaries", "node", "versions", "*", "node.exe"
    )
    hits = sorted(glob.glob(managed), reverse=True)
    if hits:
        return hits[0]
    # 最后回退：当前用户名的等价路径
    return os.path.join(
        os.path.expanduser("~"), ".workbuddy", "binaries", "node", "versions", "22.22.2", "node.exe"
    )


_NODE_EXE = _find_node()
_XIAOHEIHE_FETCH = os.path.join(_HERE, "xiaoheihe_fetch.cjs")


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


def fetch_xiaoheihe(url: str) -> str:
    """Render a xiaoheihe.cn post via headless Chrome and return body innerText.

    Returns "" on any failure (missing node, render timeout, blocked page). The
    caller falls back to an empty source so the pipeline reports "no repos found"
    instead of crashing.
    """
    if not os.path.exists(_XIAOHEIHE_FETCH):
        return ""
    try:
        res = subprocess.run(
            [_NODE_EXE, _XIAOHEIHE_FETCH, url],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:
        return ""
    if res.returncode != 0:
        return ""
    text = (res.stdout or "").strip()
    # xiaoheihe sometimes returns the "download app" landing page instead of
    # the actual post (anti-bot / session expired). Treat it as a failed fetch.
    if not text or ("立即下载小黑盒APP" in text and len(text) < 800):
        return ""
    return text


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

    # xiaoheihe.cn posts: render with a headless browser (deep-link / JS-rendered).
    if _XIAOHEIHE_RE.search(text):
        body = fetch_xiaoheihe(text)
        if body:
            return [SourceText("body", body, "xiaoheihe")], "xiaoheihe"
        return [], "xiaoheihe"

    # Otherwise treat as an article URL.
    from articles.fetch import fetch_web_content
    res = fetch_web_content(text)
    if not res:
        return [], "article"
    _, content, _ = res
    return [SourceText("body", content or "", "article")], "article"
