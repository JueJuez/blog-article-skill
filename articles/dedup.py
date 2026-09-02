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


def batch_is_summarized(urls) -> set:
    """批量查询：返回 urls 中已总结的子集（一次读索引，避免逐条读文件 IO）。

    用于需要大量判断的场景——如 `--all-videos` 全量重抓时跳过已总结视频，
    防止 seen 门禁被绕过后又把已总结项重新入队/重复落盘。
    """
    urls = [u for u in urls if u and u.strip()]
    if not urls:
        return set()
    index = _load_index()
    return {u for u in urls if index.get(_key_for(u)[1])}


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


# ---------------- 跨来源标题/内容去重（公众号 ↔ scys） ----------------
# 背景：生财有术同时订阅公众号与 scys 站内，同一篇帖子两边 URL 不同，
# 按 URL 的 is_summarized 挡不住；这里以 notes/_scraped/scys/ 原文归档为基准，
# 做标题高度相似 + 正文前缀相似判断（DECISION 2026-09-03，公众号侧拦截）。

import re as _re
from difflib import SequenceMatcher as _SeqMatcher

_PUNCT_RE = _re.compile(
    r"[\s，。！？、：；“”‘’（）《》【】·…—\-_|,.!?:;'\"()\[\]（）]+")
_SCYS_DIR = os.path.join(_ROOT, "notes", "_scraped", "scys")
_SIM_THRESHOLD = 0.85
_CONTENT_PREFIX_LEN = 300

_scys_archive_cache = None


def normalize_title(title: str) -> str:
    """标题规范化：去空白/标点、小写——公众号与 scys 同步帖标题常有一字之差或截断。"""
    return _PUNCT_RE.sub("", (title or "")).lower()


def _load_scys_archive():
    """惰性加载 scys 原文归档的 (规范化标题, 规范化正文前缀) 列表，进程内缓存。"""
    global _scys_archive_cache
    if _scys_archive_cache is not None:
        return _scys_archive_cache
    archive = []
    if os.path.isdir(_SCYS_DIR):
        for fn in os.listdir(_SCYS_DIR):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(_SCYS_DIR, fn)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read(4000)
            except Exception:
                continue
            title = ""
            for line in text.splitlines():
                if line.startswith("#"):
                    title = line.lstrip("#").strip()
                    break
            body = text.split("---", 1)[-1]
            archive.append((
                normalize_title(title),
                normalize_title(body)[:_CONTENT_PREFIX_LEN],
                title or fn,
            ))
    _scys_archive_cache = archive
    return archive


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # 互为前缀/包含（公众号标题常被截断到 64 字内）
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 10 and shorter in longer:
        return 1.0
    return _SeqMatcher(None, a, b).ratio()


def find_cross_duplicate(title: str = "", content: str = "",
                         threshold: float = _SIM_THRESHOLD) -> dict:
    """判断一篇公众号文章是否与 scys 已归档内容高度相似。

    标题相似 ≥ threshold，或正文前缀 _CONTENT_PREFIX_LEN 字相似 ≥ threshold，
    即视为同一篇帖子在两个来源重复。返回命中的 {"title", "sim", "via"}，未命中 {}。
    """
    norm_title = normalize_title(title)
    norm_content = normalize_title(content)[:_CONTENT_PREFIX_LEN]
    if len(norm_title) < 8 and len(norm_content) < 50:
        return {}
    best = {}
    for a_title, a_body, raw_title in _load_scys_archive():
        sim_t = _similar(norm_title, a_title) if norm_title and a_title else 0.0
        sim_c = _similar(norm_content, a_body) if norm_content and a_body else 0.0
        sim = max(sim_t, sim_c)
        if sim >= threshold and sim > best.get("sim", 0):
            best = {"title": raw_title, "sim": round(sim, 3),
                    "via": "title" if sim_t >= sim_c else "content"}
    return best
