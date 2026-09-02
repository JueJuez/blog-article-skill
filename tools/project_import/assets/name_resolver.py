"""Project-name resolution: turn a bare project name into a GitHub repo URL.

Used when an input (especially xiaoheihe.cn posts) mentions projects by name but
does not include their repository links. The flow is:

    body text -> extract_project_name_candidates -> search_github_by_name
             -> (url, owner_repo, stars, desc) hit

Search strategy (to dodge the unauthenticated 10 req/min GitHub *search* API limit):

  1. GitHub search API (api.github.com/search/repositories). If GITHUB_TOKEN is set
     this is authenticated (~5000/h); otherwise anonymous (~10/min).
  2. On rate-limit / empty result, fall back to GitHub's *web* search page
     (github.com/search?q=...&type=repositories) and parse the first repo link.
     This is a normal HTML page fetch, not subject to the search-API rate limit.
"""
import json
import os
import re
import time
import urllib.request
import urllib.parse
import urllib.error


# xiaoheihe posts commonly title each project like:
#   "[1] ChinaTextbook: ..."  or  "2 ebook2audiobook: ..."  or  "1⃣ ProjectName：描述"
_HEADING_RE = re.compile(
    r"(?:^|\n)\s*(?:\[\d+\]|\d+\.|【\d+】|\d+⃣)?\s*"
    r"([A-Za-z][A-Za-z0-9._-]{2,})\s*[:：]\s*",
    re.MULTILINE,
)

# Tokens that are almost certainly natural language, not project names.
_STOP_WORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had", "her",
    "was", "one", "our", "out", "day", "get", "has", "him", "his", "how", "its",
    "may", "new", "now", "old", "see", "two", "who", "boy", "did", "she", "use",
    "way", "many", "this", "that", "with", "have", "from", "they", "know", "want",
    "been", "good", "much", "some", "time", "very", "when", "come", "here", "just",
    "like", "long", "make", "more", "only", "over", "such", "take", "than", "them",
    "well", "were", "what", "will", "your", "about", "could", "other", "right",
    "think", "where", "being", "every", "great", "might", "shall", "still", "those",
    "while", "which", "would", "there", "their", "should", "world", "years", "after",
    "again", "never", "these", "under", "really", "something", "also", "into", "most",
    "then", "than", "very", "well", "work", "life", "even", "back", "first", "last",
    "next", "people", "year", "years", "down", "off", "too", "any", "same", "each",
    "few", "between", "both", "own", "part", "place", "made", "make", "making", "made",
    "may", "say", "says", "said", "say", "does", "doesn", "don", "didn", "isn", "wasn",
    "aren", "weren", "won", "wouldn", "couldn", "shouldn", "hasn", "haven", "hadn",
    "does", "did", "done", "doing", "will", "would", "shall", "may", "might", "must",
    "can", "could", "need", "needs", "needed", "dare", "dares", "dared", "ought",
    "used", "get", "gets", "got", "gotten", "getting", "go", "goes", "went", "gone",
    "going", "come", "comes", "came", "coming", "take", "takes", "took", "taken",
    "taking", "give", "gives", "gave", "given", "giving", "put", "puts", "putting",
    "set", "sets", "setting", "run", "runs", "ran", "running", "let", "lets", "letting",
    "see", "sees", "saw", "seen", "seeing", "look", "looks", "looked", "looking",
    "find", "finds", "found", "finding", "use", "uses", "used", "using", "help",
    "helps", "helped", "helping", "show", "shows", "showed", "shown", "showing",
    "tell", "tells", "told", "telling", "try", "tries", "tried", "trying", "ask",
    "asks", "asked", "asking", "seem", "seems", "seemed", "seeming", "feel", "feels",
    "felt", "feeling", "leave", "leaves", "left", "leaving", "call", "calls", "called",
    "calling", "keep", "keeps", "kept", "keeping", "bring", "brings", "brought",
    "bringing", "begin", "begins", "began", "begun", "beginning", "start", "starts",
    "started", "starting", "turn", "turns", "turned", "turning", "become", "becomes",
    "became", "becoming", "seem", "seems", "seemed", "seeming",
    # Platform / generic technology tokens that are almost never the repo name itself
    "github", "gitee", "gitlab", "docker", "kubernetes", "npm", "yarn", "pip",
    "python", "javascript", "typescript", "react", "vue", "angular", "node",
    "css", "html", "sql", "api", "url", "http", "https", "json", "xml", "yaml",
    "ui", "ux", "ide", "cli", "gui", "os", "cpu", "gpu", "ram", "ssd",
    "pdf", "epub", "mp3", "mp4", "txt", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
})


def _is_likely_repo_name(token: str) -> bool:
    """Heuristic: does this token look like a project/repo name?"""
    if not token:
        return False
    if len(token) < 3 or len(token) > 40:
        return False
    lower = token.lower()
    if lower in _STOP_WORDS:
        return False
    # Contains separator/digit -> strong signal
    if "_" in token or "-" in token or any(c.isdigit() for c in token):
        return True
    # CamelCase transition
    if re.search(r"[a-z][A-Z]", token):
        return True
    # All-caps acronym (e.g. MCP, TTS) - allow short ones
    if token.isupper():
        return True
    return False


def extract_project_name_candidates(text: str, platform: str = None) -> list[str]:
    """Extract likely project names from body text.

    High-confidence sources are used first (xiaoheihe-style headings). If none
    are found and platform == 'xiaoheihe', fall back to scanning lines that
    contain context keywords (项目/仓库/开源/GitHub/Star/地址) and extracting
    CamelCase / snake_case / alphanumeric tokens.
    """
    text = text or ""
    candidates: list[str] = []
    seen: set[str] = set()

    # 1. Heading pattern: "[1] ProjectName:" or "ProjectName：描述"
    for m in _HEADING_RE.finditer(text):
        name = m.group(1)
        key = name.lower()
        if key not in seen and _is_likely_repo_name(name):
            seen.add(key)
            candidates.append(name)

    # 2. Fallback for xiaoheihe: scan context-keyword lines.
    if not candidates and platform == "xiaoheihe":
        context_keywords = ["项目", "仓库", "开源", "GitHub", "github", "Star", "stars", "地址"]
        token_re = re.compile(r"[A-Za-z][A-Za-z0-9._-]{2,}")
        for line in text.splitlines():
            if not any(kw in line for kw in context_keywords):
                continue
            for token in token_re.findall(line):
                key = token.lower()
                if key not in seen and _is_likely_repo_name(token):
                    seen.add(key)
                    candidates.append(token)

    return candidates


def _github_token() -> str:
    return os.environ.get("GITHUB_TOKEN", "").strip()


def _hit_dict(item: dict):
    return {
        "url": item["html_url"],
        "owner_repo": item["full_name"],
        "stars": item.get("stargazers_count", 0),
        "desc": (item.get("description") or "")[:80],
    }


def _github_api_search(name: str):
    """Call the GitHub search API. Returns (items_list, rate_limited_bool)."""
    q = urllib.parse.quote(f"{name} in:name")
    api = f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc"
    headers = {
        "User-Agent": "project-import",
        "Accept": "application/vnd.github+json",
    }
    tok = _github_token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(api, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            return [], True  # rate limited
        print(f"  ❌ 搜索失败 HTTP {e.code}")
        return [], False
    except Exception as e:
        print(f"  ❌ 搜索失败: {e}")
        return [], False

    return data.get("items") or [], False


def _repo_stars_via_api(owner: str, repo: str):
    """Fetch stars for a resolved owner/repo via the repo API (separate limit pool)."""
    url = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {"User-Agent": "project-import"}
    tok = _github_token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
        return data.get("stargazers_count", 0) or 0
    except Exception:
        return 0


# GitHub search-result HTML: each repo sits in a `repo-list-item` block; the first
# in-page anchor `<a href="/owner/repo">` is the repo link.
_GH_NON_REPO = frozenset({
    "topics", "sponsors", "login", "features", "collections", "orgs",
    "about", "contact", "marketplace", "settings", "explore", "search",
    "new", "notifications", "dashboard", "pricing", "enterprise", "join",
    "careers", "blog", "support", "community", "integrations", "events",
    "customer-stories", "sessions", "codespaces",
})


def _search_github_web(name: str):
    """Web-search fallback: fetch github.com search HTML, return (owner, repo) or None."""
    q = urllib.parse.quote(name)
    url = f"https://github.com/search?q={q}&type=repositories"
    req = urllib.request.Request(url, headers={
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", "replace")
    except Exception:
        return None
    # GitHub search results render each repo title in a `search-title` block,
    # containing an anchor like <a href="/owner/repo" data-component="Link">.
    # Star/unstar anchors have an extra path segment, so the 2-segment regex won't
    # pick them up.
    for block in html.split("search-title")[1:]:
        m = re.search(r'href="/([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9._-]+)"', block[:500])
        if m:
            owner, repo = m.group(1), m.group(2)
            if owner.lower() in _GH_NON_REPO:
                continue
            # Avoid star/unstar/action links (they have 3+ segments).
            if repo.lower() in ("star", "unstar", "stargazers", "issues", "pulls"):
                continue
            return owner, repo
    return None


def search_github_by_name(name: str, *, prefer_exact: bool = True,
                          use_web_fallback: bool = True):
    """Search GitHub for a repo by name (no exact URL known).

    Strategy:
      1. Search API (authenticated if GITHUB_TOKEN set, else anonymous ~10/min).
      2. On rate-limit or empty result, fall back to GitHub web search HTML.

    Returns a dict with url / owner_repo / stars / desc, or None if nothing
    matched / both paths failed.

    With prefer_exact=True, an exact `name` match is preferred over top-by-stars,
    reducing false positives for common words like "Bark" or "Agent".
    """
    items, rate_limited = _github_api_search(name)
    if items:
        if prefer_exact:
            target = name.lower()
            for item in items:
                if (item.get("name") or "").lower() == target:
                    return _hit_dict(item)
            for item in items:
                if (item.get("full_name") or "").lower().endswith(f"/{target}"):
                    return _hit_dict(item)
        return _hit_dict(items[0])

    if not use_web_fallback:
        if not rate_limited:
            print(f"  ⚠️ 未搜到与 '{name}' 匹配的仓库")
        return None

    # Fallback: GitHub web search (not subject to the search-API rate limit).
    if rate_limited:
        print(f"  ⚠️ 搜索 API 限流，改用 GitHub 网页搜索兜底: {name}")
    web = _search_github_web(name)
    if not web:
        if not rate_limited:
            print(f"  ⚠️ 未搜到与 '{name}' 匹配的仓库")
        return None
    owner, repo = web
    stars = _repo_stars_via_api(owner, repo)
    return {
        "url": f"https://github.com/{owner}/{repo}",
        "owner_repo": f"{owner}/{repo}",
        "stars": stars,
        "desc": "",
    }
