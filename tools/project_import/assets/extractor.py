import re
from typing import Set, Tuple, List

from assets.tracker import load_imported_list
from assets.storage import owner_repo_keys as _owner_repo_keys

# 同时支持 github.com 与 gitee.com
REPO_URL_PATTERN = re.compile(
    r'https://(github\.com|gitee\.com)/([a-zA-Z0-9._-]+)/([a-zA-Z0-9._-]+?)(?:\.git)?(?=[/\s\)\]>]|$)'
)
REPO_SSH_PATTERN = re.compile(
    r'git@(github\.com|gitee\.com):([a-zA-Z0-9._-]+)/([a-zA-Z0-9._-]+?)(?:\.git)?(?=[\s,;)\]>（】、，。]|$|\n)'
)

_NON_REPO_OWNERS = frozenset({
    "settings", "notifications", "dashboard", "explore", "marketplace",
    "pulls", "issues", "sponsors", "organizations", "new", "search",
    "collections", "topics", "trending", "about", "contact", "sites",
    "security", "features", "team", "enterprise", "pricing",
    "login", "signup", "join", "careers", "blog", "support",
    "community", "integrations", "events", "customer-stories",
})


def _normalize(repo: str) -> str:
    return repo.rstrip("/").removesuffix(".git")


def _is_valid_repo(owner: str) -> bool:
    return owner.lower() not in _NON_REPO_OWNERS and "." not in owner


def _platform_of(host: str) -> str:
    return "gitee" if host.lower().startswith("gitee") else "github"


def _match_url(url: str):
    m = REPO_URL_PATTERN.match(url)
    if m:
        return m
    return REPO_SSH_PATTERN.match(url)


def extract_repo_urls(text: str) -> List[str]:
    """从文本中提取 GitHub / Gitee 仓库链接（去重、排序、归一化为 https 形式）。"""
    raw_urls = set()
    for match in REPO_URL_PATTERN.finditer(text):
        host, owner, repo = match.group(1), match.group(2), _normalize(match.group(3))
        if _is_valid_repo(owner):
            raw_urls.add(f"https://{host}/{owner}/{repo}")
    for match in REPO_SSH_PATTERN.finditer(text):
        host, owner, repo = match.group(1), match.group(2), match.group(3)
        raw_urls.add(f"https://{host}/{owner}/{repo}")
    return sorted(raw_urls)


# 向后兼容别名
def extract_github_urls(text: str) -> List[str]:
    return extract_repo_urls(text)


def normalize_url(url: str) -> str:
    match = _match_url(url)
    if match:
        return f"https://{match.group(1)}/{match.group(2)}/{_normalize(match.group(3))}"
    return url


def owner_repo_key(url: str) -> str:
    """用于去重 / 已处理判断的键（小写）。跨平台同 owner/repo 视为同一条，
    与 imported.txt 中存量条目保持兼容。"""
    match = _match_url(url)
    if match:
        return f"{match.group(2).lower()}/{_normalize(match.group(3)).lower()}"
    return url.lower()


def parse_repo(url: str) -> Tuple[str, str, str]:
    """返回 (platform, owner, repo)。platform ∈ {'github', 'gitee'}。"""
    match = _match_url(normalize_url(url))
    if match:
        return _platform_of(match.group(1)), match.group(2), _normalize(match.group(3))
    raise ValueError(f"Not a valid repo URL: {url}")


def parse_owner_repo(url: str) -> Tuple[str, str]:
    """向后兼容：仅返回 (owner, repo)。"""
    _, owner, repo = parse_repo(url)
    return owner, repo


def batch_deduplicate(urls: List[str]) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    for url in urls:
        key = owner_repo_key(url)
        if key not in seen:
            seen.add(key)
            result.append(normalize_url(url))
    return result


def filter_imported(
    urls: List[str],
) -> Tuple[List[str], List[str]]:
    imported = load_imported_list()
    new_urls: List[str] = []
    skipped: List[str] = []
    for url in urls:
        key = owner_repo_key(url)
        if key in imported:
            skipped.append(normalize_url(url))
        else:
            new_urls.append(normalize_url(url))
    return new_urls, skipped


def filter_pending(
    urls: List[str],
) -> Tuple[List[str], List[str]]:
    pending = _owner_repo_keys()
    if not pending:
        return list(urls), []
    new_urls: List[str] = []
    skipped: List[str] = []
    for url in urls:
        key = owner_repo_key(url)
        if key in pending:
            skipped.append(normalize_url(url))
        else:
            new_urls.append(normalize_url(url))
    return new_urls, skipped
