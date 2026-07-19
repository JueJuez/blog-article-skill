"""articles/dedup.py — 增量去重（A2）

按规范化 URL（或正文内容 hash）记录已总结项，重复运行时跳过并提示已存在，
避免重复消耗 token。

索引持久化在仓库根的 `.cache/dedup.json`，该目录已被 .gitignore 忽略，不进仓库。
"""

import os
import json
import hashlib
import time
from urllib.parse import urlsplit, urlunsplit

# 仓库根（articles/ 上一级）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_DIR = os.path.join(_ROOT, ".cache")
_INDEX_FILE = os.path.join(_CACHE_DIR, "dedup.json")


def _ensure_cache():
    os.makedirs(_CACHE_DIR, exist_ok=True)


def _normalize_url(url: str) -> str:
    """规范化 URL：去 scheme 大小写、去末尾斜杠、去 fragment、排序 query。"""
    try:
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower()
        netloc = parts.netloc.lower()
        path = parts.path.rstrip("/")
        # query 排序，避免 ?a=1&b=2 与 ?b=2&a=1 视为不同
        query = "&".join(sorted(parts.query.split("&"))) if parts.query else ""
        return urlunsplit((scheme, netloc, path, query, ""))
    except Exception:
        return url.strip().lower()


def _hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _load_index() -> dict:
    if not os.path.exists(_INDEX_FILE):
        return {}
    try:
        with open(_INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_index(index: dict) -> None:
    _ensure_cache()
    with open(_INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def _key_for(url: str = "", content: str = "") -> (str, str):
    """返回 (前缀, hash)。url 优先，否则用内容 hash。"""
    if url and url.strip():
        return "url", _hash(_normalize_url(url))
    if content and content.strip():
        return "content", _hash(content)
    return "none", ""


def is_summarized(url: str = "", content: str = "") -> dict:
    """查询是否已总结过。

    Returns:
        {} 表示未记录；否则为 {key, title, filename, ts} 记录
    """
    prefix, h = _key_for(url, content)
    if prefix == "none" or not h:
        return {}
    index = _load_index()
    rec = index.get(h)
    if rec:
        return {"key": h, **rec}
    return {}


def mark_summarized(url: str = "", content: str = "", title: str = "", filename: str = "") -> None:
    """记录一次成功总结。"""
    prefix, h = _key_for(url, content)
    if prefix == "none" or not h:
        return
    index = _load_index()
    index[h] = {
        "title": title or "",
        "filename": filename or "",
        "ts": int(time.time()),
    }
    _save_index(index)
