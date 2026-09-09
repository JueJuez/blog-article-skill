"""articles/dedup.py — 增量去重（A2）+ 双端同步登记表（PLAN-20260906 任务1）

按规范化 URL（或正文内容 hash）记录已总结项，重复运行时跳过并提示已存在，
避免重复消耗 token。

登记表（1.1，PLAN-20260908）：真源升格为 notes/_meta/summary_registry.json；
两代旧档 .cache/dedup.json（最老）→ notes/_meta/sync_ledger.json 首读时
自动合并搬家（新档已存在则所有旧档保留不动）。
记录 schema：{source_url, title, filename, feishu_link, obsidian_link, ts}，
feishu_link / obsidian_link 是各端最后一次验证成功时写入的指针。

并发安全（P0-3）：写入方（mark_summarized / set_links / clear_links）读-改-写
全程持有 O_EXCL 文件锁，并以临时文件 + os.replace 原子落盘；读取方不加锁，
靠原子替换保证不会读到半文件。锁等待超时后放行兜底
（与 feishu.py/_node_creation_lock 同模式）。
"""

import os
import json
import hashlib
import time
import tempfile
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit

# 仓库根（articles/ 上一级）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_DIR = os.path.join(_ROOT, ".cache")
# 1.1（PLAN-20260908）：登记表真源升格为 summary_registry.json；
# sync_ledger.json（P0-2）与 .cache/dedup.json（P0 前）为两代旧档，首读自动合并搬家。
_DEFAULT_INDEX_FILE = os.path.join(_ROOT, "notes", "_meta", "summary_registry.json")
_INDEX_FILE = _DEFAULT_INDEX_FILE
_LEGACY_INDEX_FILE = os.path.join(_ROOT, "notes", "_meta", "sync_ledger.json")
_LEGACY_CACHE_FILE = os.path.join(_CACHE_DIR, "dedup.json")
# P0-3：O_EXCL 锁目录（跨进程；tests 通过 patch _LOCK_DIR 隔离）
_LOCK_DIR = os.path.join(tempfile.gettempdir(), "blog_article_skill_dedup_locks")
_LOCK_TIMEOUT = 10.0


@contextmanager
def _index_lock():
    """登记表写入锁：O_EXCL 独占创建，超时放行避免陈锁死等。"""
    os.makedirs(_LOCK_DIR, exist_ok=True)
    lock_path = os.path.join(_LOCK_DIR, "dedup_index.lock")
    deadline = time.time() + _LOCK_TIMEOUT
    fd = None
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.time() >= deadline:
                break  # 超时放行：与 feishu/drain 锁的兜底语义一致
            time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
            try:
                os.remove(lock_path)
            except OSError:
                pass


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


# 1.4（P1-1）：站点参数白名单——query 只保留稳定键参数，其余为分享/追踪噪声剥离；
# 分P（bilibili p=）是不同内容必须保留；未列出的 host 维持原全保留行为。
_QUERY_KEEP_BY_HOST = {
    "bilibili.com": {"p"},
    "mp.weixin.qq.com": {"__biz", "mid", "idx", "sn"},
    "youtube.com": {"v"},
}


def _host_query_keep(netloc: str) -> "set | None":
    """按域名后缀匹配参数白名单（space.bilibili.com / m.youtube.com 等子域同规则）。"""
    host = netloc.split("@")[-1].split(":")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    for domain, keep in _QUERY_KEEP_BY_HOST.items():
        if host == domain or host.endswith("." + domain):
            return keep
    return None


def _normalize_url(url: str) -> str:
    """规范化 URL：去 scheme 大小写、去末尾斜杠、去 fragment、排序 query。

    1.4（P1-1）：白名单 host 只保留稳定键参数（公众号 __biz/mid/idx/sn、
    B站 p=、YouTube v=），chksm/vd_source 等可变参数剥离避免同源多键。
    """
    try:
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower()
        netloc = parts.netloc.lower()
        path = parts.path.rstrip("/")
        pairs = parts.query.split("&") if parts.query else []
        keep = _host_query_keep(netloc)
        if keep is not None:
            pairs = [p for p in pairs if p.split("=", 1)[0] in keep]
        # query 排序，避免 ?a=1&b=2 与 ?b=2&a=1 视为不同
        query = "&".join(sorted(pairs))
        return urlunsplit((scheme, netloc, path, query, ""))
    except Exception:
        return url.strip().lower()


# 1.4（P1-1）：b23.tv 短链展开（进程内缓存；网络失败降级原样，短链 path 唯一仍可作键）。
_B23_CACHE: dict = {}


def expand_url(url: str) -> str:
    """b23.tv 短链展开为长链；非短链原样返回，展开失败降级原样。"""
    u = (url or "").strip()
    try:
        netloc = urlsplit(u).netloc.lower()
    except Exception:
        return u
    if netloc.removeprefix("www.") != "b23.tv":
        return u
    if u in _B23_CACHE:
        return _B23_CACHE[u]
    expanded = u
    try:
        import requests
        resp = requests.head(u, allow_redirects=True, timeout=5)
        if resp.url and resp.url != u:
            expanded = resp.url
    except Exception:
        pass
    _B23_CACHE[u] = expanded
    return expanded


def _hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _load_index() -> dict:
    _migrate_legacy()
    if not os.path.exists(_INDEX_FILE):
        return {}
    try:
        with open(_INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        # 损坏索引不静默吞：改名 .corrupt-<ts> 备份后从空开始
        try:
            os.replace(_INDEX_FILE, _INDEX_FILE + ".corrupt-" + str(int(time.time())))
        except OSError:
            pass
        return {}


def _save_index(index: dict) -> None:
    _ensure_parent(_INDEX_FILE)
    tmp = _INDEX_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _INDEX_FILE)


def _migrate_legacy() -> None:
    """1.1：三代旧档自动合并搬家到 registry（新档已存在则所有旧档保留不动）。

    搬家链：.cache/dedup.json（最老）→ notes/_meta/sync_ledger.json → registry。
    两代旧档同时存在时按老→新顺序合并、新档覆盖老档；损坏旧档视为空表照常
    合并并删除（旧档本就是易失缓存，无修复价值）。
    """
    if os.path.exists(_INDEX_FILE):
        return
    merged: dict = {}
    found = False
    for path in (_LEGACY_CACHE_FILE, _LEGACY_INDEX_FILE):  # 老→新，新覆盖老
        if not os.path.exists(path):
            continue
        found = True
        try:
            with open(path, "r", encoding="utf-8") as f:
                legacy = json.load(f)
            if isinstance(legacy, dict):
                merged.update(legacy)
        except Exception:
            pass
    if not found:
        return
    _save_index(merged)
    for path in (_LEGACY_CACHE_FILE, _LEGACY_INDEX_FILE):
        try:
            os.remove(path)
        except OSError:
            pass


def _key_for(url: str = "", content: str = "", title: str = "") -> (str, str):
    """返回 (键类型, hash)。url 优先，其次内容 hash，最后标题指纹（title_fp）。"""
    if url and url.strip():
        return "url", _hash(_normalize_url(expand_url(url)))
    if content and content.strip():
        return "content", _hash(content)
    if title and title.strip():
        return "title_fp", _hash(normalize_title(title))
    return "none", ""


def _with_link_defaults(rec: dict) -> dict:
    """迁移来的旧记录缺 link 字段：API 层读出时补默认值（不改存储）。"""
    out = dict(rec)
    out.setdefault("feishu_link", "")
    out.setdefault("obsidian_link", "")
    return out


def is_summarized(url: str = "", content: str = "", title: str = "") -> dict:
    """查询是否已总结过（1.3：无 URL 时可按标题指纹 title_fp 查）。

    Returns:
        {} 表示未记录；否则为 {key, source_url, title, filename,
        feishu_link, obsidian_link, ts} 记录
    """
    prefix, h = _key_for(url, content, title)
    if prefix == "none" or not h:
        return {}
    index = _load_index()
    rec = index.get(h)
    if rec:
        return {"key": h, **_with_link_defaults(rec)}
    return {}


def batch_is_summarized(urls, titles=None) -> set:
    """批量查询：返回 urls/titles 中已总结的子集（一次读索引，避免逐条读文件 IO）。

    用于需要大量判断的场景——如 `--all-videos` 全量重抓时跳过已总结视频，
    防止 seen 门禁被绕过后又把已总结项重新入队/重复落盘。
    1.3：titles 批量标题指纹查询；返回命中原文（url 或 title）集合。
    """
    urls = [u for u in (urls or []) if u and u.strip()]
    titles = [t for t in (titles or []) if t and t.strip()]
    if not urls and not titles:
        return set()
    index = _load_index()
    hits = {u for u in urls if index.get(_key_for(u)[1])}
    hits |= {t for t in titles if index.get(_key_for(title=t)[1])}
    return hits


def mark_summarized(url: str = "", content: str = "", title: str = "",
                    filename: str = "", folder: str = "", note_type: str = "",
                    source: str = "") -> None:
    """记录一次成功总结（读-改-写全程锁内）。

    与 register 同一套 merge 语义（PLAN-20260908 阶段3.4）：已有记录保留
    已写 link 与未传字段，仅用本次非空字段覆盖；key_type/summarized_at 对齐。
    url/content 均空时按标题指纹（title_fp）落键，与 is_summarized 查询对称。
    """
    prefix, h = _key_for(url, content, title)
    if prefix == "none" or not h:
        return
    with _index_lock():
        index = _load_index()
        old = index.get(h) or {}
        index[h] = {
            "key_type": prefix,
            "source_url": url or "",
            "title": title or old.get("title", ""),
            "filename": filename or old.get("filename", ""),
            "folder": folder or old.get("folder", ""),
            "note_type": note_type or old.get("note_type", ""),
            "source": source or old.get("source", ""),
            "summarized_at": time.strftime("%Y-%m-%d"),
            "feishu_link": old.get("feishu_link", ""),
            "obsidian_link": old.get("obsidian_link", ""),
            "ts": int(time.time()),
        }
        _save_index(index)


def register(url: str = "", title: str = "", folder: str = "",
             note_type: str = "", source: str = "") -> dict:
    """落盘登记（1.2，PLAN-20260908）：统一写入口，扩展 schema §3.2 字段。

    url 与 title 至少给一个：有 url 走 url 键；无 url 用标题指纹键
    （title_fp，如直播回放）。重复登记幂等——已有记录保留 link 与未传
    字段，仅用本次非空字段覆盖；summarized_at 刷新为当日。缺参返回 {}。
    """
    prefix, h = _key_for(url=url, title=title)
    if prefix == "none" or not h:
        return {}
    with _index_lock():
        index = _load_index()
        old = index.get(h) or {}
        rec = {
            "key_type": prefix,
            "source_url": url or "",
            "title": title or old.get("title", ""),
            "folder": folder or old.get("folder", ""),
            "note_type": note_type or old.get("note_type", ""),
            "source": source or old.get("source", ""),
            "summarized_at": time.strftime("%Y-%m-%d"),
            "feishu_link": old.get("feishu_link", ""),
            "obsidian_link": old.get("obsidian_link", ""),
            "ts": int(time.time()),
        }
        index[h] = rec
        _save_index(index)
        return {"key": h, **_with_link_defaults(rec)}


def get_entry(url: str = "", content: str = "") -> dict:
    """按 url/content 查登记表记录，未命中返回 {}；旧记录的 link 补默认值。"""
    prefix, h = _key_for(url, content)
    if prefix == "none" or not h:
        return {}
    rec = _load_index().get(h)
    if not rec:
        return {}
    return {"key": h, **_with_link_defaults(rec)}


def set_links(url: str = "", content: str = "", feishu_link: "str | None" = None,
              obsidian_link: "str | None" = None) -> dict:
    """更新已有记录的双端 link（只改传入的字段），返回更新后的记录。

    未命中已有记录时返回 {} 且不新造键——link 只跟着 mark_summarized 建立的记录走。
    """
    prefix, h = _key_for(url, content)
    if prefix == "none" or not h:
        return {}
    with _index_lock():
        index = _load_index()
        rec = index.get(h)
        if not rec:
            return {}
        if feishu_link is not None:
            rec["feishu_link"] = feishu_link
        if obsidian_link is not None:
            rec["obsidian_link"] = obsidian_link
        _save_index(index)
        return {"key": h, **_with_link_defaults(rec)}


def clear_links(url: str = "", content: str = "") -> bool:
    """清空已有记录的双端 link（对账发现两端均丢时用）；未命中返回 False。"""
    prefix, h = _key_for(url, content)
    if prefix == "none" or not h:
        return False
    with _index_lock():
        index = _load_index()
        rec = index.get(h)
        if not rec:
            return False
        rec["feishu_link"] = ""
        rec["obsidian_link"] = ""
        _save_index(index)
        return True


def set_links_by_key(key: str, feishu_link: "str | None" = None,
                     obsidian_link: "str | None" = None) -> dict:
    """按登记表主键 key 直改双端 link（只改传入的字段），返回更新后的记录。

    set_links 按 url/content 键定位，覆盖不了 content-key 记录与对账场景；
    本函数供已知 key 的调用方（vault 对账回写）使用。空 key / 未命中返回 {}。
    """
    if not key:
        return {}
    with _index_lock():
        index = _load_index()
        rec = index.get(key)
        if not rec:
            return {}
        if feishu_link is not None:
            rec["feishu_link"] = feishu_link
        if obsidian_link is not None:
            rec["obsidian_link"] = obsidian_link
        _save_index(index)
        return {"key": key, **_with_link_defaults(rec)}


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
