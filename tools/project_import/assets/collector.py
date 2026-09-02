import time
import os
import base64
import json
from typing import Optional, Tuple

_API_HEADERS = {"User-Agent": "batch-link-import/1.0"}


def _request_headers(platform: str = "github") -> dict:
    headers = dict(_API_HEADERS)
    if platform == "gitee":
        token = os.environ.get("GITEE_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"
    else:
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def get_readme_url(platform: str, owner: str, repo: str, branch: str = "main") -> str:
    if platform == "gitee":
        return f"https://gitee.com/{owner}/{repo}/raw/{branch}/README.md"
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"


def get_repo_api_url(platform: str, owner: str, repo: str) -> str:
    if platform == "gitee":
        return f"https://gitee.com/api/v5/repos/{owner}/{repo}"
    return f"https://api.github.com/repos/{owner}/{repo}"


def stars_to_score(stars: int) -> int:
    if stars <= 10:
        return 1
    elif stars <= 100:
        return 2
    elif stars <= 500:
        return 3
    elif stars <= 1000:
        return 4
    elif stars <= 5000:
        return 5
    elif stars <= 10000:
        return 6
    elif stars <= 30000:
        return 7
    elif stars <= 100000:
        return 8
    else:
        return 9 if stars <= 500000 else 10


def _fetch_with_retry(
    url: str, platform: str = "github", timeout: int = 15
) -> Tuple[Optional[str], Optional[str]]:
    try:
        import requests
    except ImportError:
        return None, "requests library not available"
    try:
        resp = requests.get(url, timeout=timeout, headers=_request_headers(platform))
        if resp.status_code == 200:
            return resp.text, None
        elif resp.status_code == 404:
            return None, "Not found (404)"
        elif resp.status_code == 403:
            if not _request_headers(platform).get("Authorization"):
                limit = "5000" if platform == "gitee" else "5000"
                return None, (
                    f"Access denied (403) - 可能触发 {platform} API 限流"
                    f"(匿名 60 次/小时)，设置 "
                    f"{'GITEE_TOKEN' if platform == 'gitee' else 'GITHUB_TOKEN'} "
                    f"可提升到 {limit} 次/小时"
                )
            return None, "Access denied (403) - Token 无效或无权限"
        else:
            return None, f"HTTP {resp.status_code}"
    except requests.exceptions.Timeout:
        return None, "Request timed out"
    except requests.exceptions.ConnectionError:
        return None, "Connection error"
    except Exception as e:
        return None, f"Unexpected error: {e}"


def _decode_api_readme(text: str) -> Optional[str]:
    """GitHub / Gitee 的 README API 接口返回 base64 编码内容，这里解码成文本。"""
    try:
        data = json.loads(text)
        content = data.get("content")
        if not content:
            return None
        return base64.b64decode(content).decode("utf-8", errors="replace")
    except Exception:
        return None


def _collect_readme(
    platform: str, owner: str, repo: str,
    max_retries: int = 3, retry_delay: int = 1,
) -> Tuple[Optional[str], Optional[str]]:
    """依次尝试多个 README 源，提升采集成功率。

    GitHub: raw(main) -> raw(master) -> API(readme, base64)
    Gitee : raw(master) -> raw(main) -> API(readme, base64)
    （某些网络环境下 raw.githubusercontent.com 不可达，但 api.github.com 可达，
      因此加一层 API 兜底。）
    """
    if platform == "gitee":
        branches = ["master", "main"]
        api_readme = f"https://gitee.com/api/v5/repos/{owner}/{repo}/readme"
    else:
        branches = ["main", "master"]
        api_readme = f"https://api.github.com/repos/{owner}/{repo}/readme"

    sources = [(f"raw/{b}", get_readme_url(platform, owner, repo, b)) for b in branches]
    sources.append(("api", api_readme))

    last_err = None
    for label, url in sources:
        for attempt in range(max_retries):
            content, err = _fetch_with_retry(url, platform)
            if content is not None:
                if label == "api":
                    content = _decode_api_readme(content)
                    if content is None:
                        last_err = "README 解码失败"
                        continue
                return content, None
            last_err = err
            if err == "Not found (404)":
                break
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
    return None, f"README 获取失败: {last_err}"


def _collect_stars(
    platform: str, owner: str, repo: str,
    max_retries: int = 3, retry_delay: int = 1,
) -> Tuple[Optional[int], Optional[str]]:
    """Stars 通过仓库 API 获取，独立于 README，互不影响。"""
    url = get_repo_api_url(platform, owner, repo)
    for attempt in range(max_retries):
        content, err = _fetch_with_retry(url, platform)
        if content is not None:
            try:
                data = json.loads(content)
                stars = data.get("stargazers_count", 0)
                return stars if stars is not None else 0, None
            except (json.JSONDecodeError, ValueError):
                pass
        if attempt < max_retries - 1:
            time.sleep(retry_delay)
    return 0, f"Stars 获取失败: {err}"


def collect_project_data(
    platform: str, owner: str, repo: str,
    max_retries: int = 3, retry_delay: int = 1,
) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """采集单个仓库的 README 与 Stars。

    Args:
        platform: 'github' 或 'gitee'
        owner, repo: 仓库 owner 与 repo 名
    Returns:
        (readme_text, stars, error) —— README 缺失则整条失败；Stars 失败仅记 0 不阻断
    """
    readme, readme_err = _collect_readme(platform, owner, repo, max_retries, retry_delay)
    stars, _stars_err = _collect_stars(platform, owner, repo, max_retries, retry_delay)
    error = readme_err if readme is None else None
    return readme, stars, error
