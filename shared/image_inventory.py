"""shared/image_inventory.py — 图片信息缺口台账（D2 / PLAN-20261026 §5）

**要解决的问题**：抓取层只用 trafilatura 抽正文，`<img>` 全丢，连「源里有几张图」都没记录。
于是 XMind / 鱼骨图 / 数据截图里的知识**静默消失**，稿子读起来却毫无破绽 —— 这是 D2。

**做法**：抓取时（HTML 还在手上）就地记一份**图片台账**，落在
`notes/_meta/image_inventory/<url 前 16 位 hash>.json`；稿子/队列/报告只拿它的**一句话结论**。

四条铁律（PLAN §5）：

1. **区分「有图」与「已读图」**：`total` 是发现几张，`read` 是实际看了几张。只有 `read` 才算读到，
   **不能凭 alt 认定已读**（alt 常常是「图片」两个字）。
2. **取不到图片信息 ≠ 没有图片**：CDP 文本通道 / 字幕 / 纯文本源记 `unknown_no_html`，
   绝不报 `no_images` 冒充干净。
3. **不进正文**：台账只落 `notes/_meta/`，笔记正文与 frontmatter 一个字段都不加（下游 KB 按段落检索）。
4. **不擅自扩能力**：本模块只记录与提示，不下载、不 OCR、不批量请求；识图由调用方按需做，
   结果用 `mark_read()` 回填（带方法、范围、失败原因）。

状态机（`state`）：

    unknown_no_html      取不到 HTML，无法判断有没有图
    no_images            HTML 解析完，确实一张 img 都没有
    decorative_only      有 img，但全是装饰/图标（按启发式，仍计入 total）
    images_not_read      有内容图，一张没读
    images_partial_read  读了一部分
    images_read          全读

**全部是报告项**：不计分、不拦盘、不触发重交、永不抛异常。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from urllib.parse import urljoin

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE_DIR = os.path.join(_ROOT, "notes", "_meta", "image_inventory")

STATE_UNKNOWN = "unknown_no_html"
STATE_NO_IMAGES = "no_images"
STATE_DECORATIVE_ONLY = "decorative_only"
STATE_NOT_READ = "images_not_read"
STATE_PARTIAL = "images_partial_read"
STATE_READ = "images_read"

# 装饰图启发式：命中即打标，仍计入 total（不静默丢，方便抽检）
_DECOR_HINT = re.compile(
    r"(icon|logo|avatar|emoji|badge|spacer|pixel|placeholder|loading|arrow|bullet"
    r"|qrcode|qr_code|erweima|二维码|头像|图标)", re.I)
_DATA_URI = re.compile(r"^data:", re.I)


def _digest(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def url_key(url: str) -> str:
    return _digest(url or "")[:16]


def _pick_src(tag) -> str:
    """取图片地址：懒加载属性优先于 src（src 常是占位图）。"""
    for attr in ("data-src", "data-original", "data-actualsrc", "data-lazy-src", "src"):
        val = (tag.get(attr) or "").strip()
        if val:
            return val
    srcset = (tag.get("srcset") or "").strip()
    if srcset:
        first = srcset.split(",")[0].strip().split(" ")[0]
        if first:
            return first
    return ""


def _caption(tag) -> str:
    """figcaption 优先，其次 title。alt 单独存，不当 caption。"""
    fig = tag.find_parent("figure")
    if fig:
        cap = fig.find("figcaption")
        if cap:
            return cap.get_text(" ", strip=True)[:200]
    return (tag.get("title") or "").strip()[:200]


def _int_attr(tag, name):
    raw = (tag.get(name) or "").strip().rstrip("px")
    return int(raw) if raw.isdigit() else 0


def _norm_src(url: str) -> str:
    """比 src 时忽略 query/fragment —— CDN 常带 `?utm_source=`，模型/报告里往往不带。"""
    return (url or "").split("#")[0].split("?")[0].rstrip("/")


def same_src(a: str, b: str) -> bool:
    """宽松比对：归一化后相等，或文件名（≥8 字符）相同。"""
    na, nb = _norm_src(a), _norm_src(b)
    if na and na == nb:
        return True
    fa, fb = na.rsplit("/", 1)[-1], nb.rsplit("/", 1)[-1]
    return bool(fa) and fa == fb and len(fa) >= 8


def _recompute(inv: dict) -> dict:
    """状态一律由 `reads`（真实读过的记录）反推，不让 `images[].read` 与 `reads` 各自为政。"""
    read_srcs = [r.get("src") for r in inv.get("reads") or [] if r.get("ok")]
    for img in inv.get("images") or []:
        img["read"] = any(same_src(img.get("src"), s) for s in read_srcs)
    inv.update(classify(inv.get("images") or [], inv.get("html_available", True)))
    if not inv.get("html_available") and read_srcs:
        inv["state"] = STATE_PARTIAL
    return inv


def _decorative_reason(tag, src: str, alt: str, width: int, height: int) -> str:
    if width and height and width <= 48 and height <= 48:
        return f"too_small({width}x{height})"
    hay = " ".join([src, alt, tag.get("class") and " ".join(tag.get("class") or []) or "",
                    tag.get("id") or ""])
    m = _DECOR_HINT.search(hay)
    if m:
        return f"hint:{m.group(1).lower()}"
    if not alt and tag.find_parent(["header", "nav", "footer"]) is not None:
        return "chrome_region"
    return ""


def extract_images(html: str, base_url: str = "") -> "list[dict]":
    """从 HTML 抽图片清单。去重按解析后的绝对地址；不下载、不发请求。"""
    if not html:
        return []
    try:
        from bs4 import BeautifulSoup
    except ImportError:                      # bs4 是项目依赖，装不上就如实返回空，不猜
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []
    out, seen = [], set()
    for tag in soup.find_all("img"):
        raw_src = _pick_src(tag)
        if not raw_src:
            out.append({"src": "", "status": "missing_src", "alt": (tag.get("alt") or "").strip()[:200],
                        "caption": _caption(tag), "width": _int_attr(tag, "width"),
                        "height": _int_attr(tag, "height"), "suspect_decorative": False,
                        "decorative_reason": "", "inline": False, "read": False})
            continue
        src = raw_src if _DATA_URI.match(raw_src) else urljoin(base_url, raw_src)
        if src in seen:
            continue
        seen.add(src)
        alt = (tag.get("alt") or "").strip()[:200]
        width, height = _int_attr(tag, "width"), _int_attr(tag, "height")
        reason = _decorative_reason(tag, src, alt, width, height)
        out.append({"src": src, "status": "ok", "alt": alt, "caption": _caption(tag),
                    "width": width, "height": height,
                    "suspect_decorative": bool(reason), "decorative_reason": reason,
                    "inline": bool(_DATA_URI.match(raw_src)), "read": False})
    return out


def classify(images: "list[dict]", html_available: bool = True) -> dict:
    """把清单折成状态。**只描述事实，不推断内容。**"""
    images = images or []
    total = len(images)
    content = [i for i in images if not i.get("suspect_decorative")
               and i.get("status") in ("ok", "missing_src") and not i.get("inline")]
    read = [i for i in images if i.get("read")]
    if not html_available:
        state = STATE_UNKNOWN
    elif total == 0:
        state = STATE_NO_IMAGES
    elif not content:
        state = STATE_DECORATIVE_ONLY
    elif not read:
        state = STATE_NOT_READ
    elif len(read) < len(content):
        state = STATE_PARTIAL
    else:
        state = STATE_READ
    return {"state": state, "html_available": bool(html_available), "total": total,
            "content_total": len(content), "read": len(read),
            "suspect_decorative": sum(1 for i in images if i.get("suspect_decorative")),
            "missing_src": sum(1 for i in images if i.get("status") == "missing_src")}


def record(url: str, *, html: str = "", kind: str = "html", extra: dict = None,
           store_dir: str = "") -> dict:
    """抓取时就地记账。**永不抛异常**；`html=""` 表示这条通道拿不到 HTML。

    Returns: 台账 dict（也写盘），失败时返回同样结构但不落盘。
    """
    html_available = bool(html)
    inv = {"url": url or "", "url_key": url_key(url or ""), "kind": kind,
           "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "html_available": html_available, "images": [], "reads": []}
    try:
        inv["images"] = extract_images(html, url or "") if html_available else []
        inv.update(classify(inv["images"], html_available))
        if extra:
            inv.update(extra)
        path = _path(url or "", store_dir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(inv, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        inv["store_path"] = path
        return inv
    except Exception as exc:                 # 观测零件绝不影响抓取
        inv.setdefault("state", STATE_UNKNOWN)
        inv["error"] = f"{type(exc).__name__}: {exc}"
        return inv


def _path(url: str, store_dir: str = "") -> str:
    return os.path.join(store_dir or STORE_DIR, f"{url_key(url)}.json")


def load_for_url(url: str, store_dir: str = "") -> dict:
    try:
        with open(_path(url, store_dir), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def mark_read(url: str, src: str, *, text: str = "", structure: str = "", method: str = "",
              ok: bool = True, error: str = "", store_dir: str = "") -> dict:
    """回填一次按需识图的结果（谁读的、读了什么、范围到哪）。

    `structure` 只写**能确认的结构**（层级/箭头/关系）；做不到就留空，不许拿文字识别冒充结构理解。
    """
    inv = load_for_url(url, store_dir)
    if not inv:
        inv = record(url, html="", kind="unknown", store_dir=store_dir)
    try:
        rows = inv.setdefault("reads", [])
        rows.append({"src": src, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "ok": bool(ok),
                     "method": method or "unknown", "text": text or "",
                     "structure": structure or "", "error": error or ""})
        _recompute(inv)
        path = _path(url, store_dir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(inv, fh, ensure_ascii=False, indent=1)
    except Exception as exc:
        inv["error"] = f"{type(exc).__name__}: {exc}"
    return inv


def dispatch_hint(inv: dict) -> str:
    """给派单用的一句话（**只在不干净时非空**，纯文本源不产生任何 prompt 变更）。"""
    if not inv:
        return ""
    state = inv.get("state") or ""
    total = inv.get("total") or 0
    content = inv.get("content_total") or 0
    read = inv.get("read") or 0
    if state == STATE_UNKNOWN:
        return ("【图片信息】本篇来源只能取到文字（无 HTML），**无法判断原文有没有图**。"
                "若你判断正文关键结论依赖图表/截图/思维导图，请在对应位置注明「原文可能有图，未读取」，"
                "不要凭文字推测图里的数据或结构。")
    if state == STATE_NOT_READ:
        return (f"【图片信息】源含 {total} 张图片（其中疑似内容图 {content} 张），**本轮未读取**。"
                f"正文若涉及图表/流程图/思维导图，注明「图未读」；不得凭 alt、标题或上下文编造图内数据与层级关系。")
    if state == STATE_PARTIAL:
        return (f"【图片信息】源含 {total} 张图片，已读 {read}/{content} 张内容图。"
                f"未读的图不得推断内容；已读的只能写到实际看到的部分。")
    if state == STATE_READ:
        return (f"【图片信息】源含 {total} 张图片，已读 {read}/{content} 张内容图；"
                f"图内结论须与读取记录一致，不得外推。")
    return ""


def summary_line(inv: dict) -> str:
    """给报告/日志的一行（总是非空，便于「本轮有没有图」可审计）。"""
    if not inv:
        return "图片：无台账（该来源未经本模块记录）"
    state = inv.get("state") or "unknown"
    label = {
        STATE_UNKNOWN: "取不到图片信息（文本通道）",
        STATE_NO_IMAGES: "无图片",
        STATE_DECORATIVE_ONLY: "仅有装饰图",
        STATE_NOT_READ: "有内容图未读",
        STATE_PARTIAL: "部分已读",
        STATE_READ: "已读",
    }.get(state, state)
    return (f"图片：{label}（total {inv.get('total', 0)} / 内容图 {inv.get('content_total', 0)}"
            f" / 已读 {inv.get('read', 0)}）")


def review_reasons(inv: dict) -> "list[str]":
    """进异步审核理由（只提示，不改判定）。"""
    state = (inv or {}).get("state")
    if state == STATE_UNKNOWN:
        return ["image_unknown"]
    if state == STATE_NOT_READ:
        return [f"image_unread:{inv.get('content_total') or inv.get('total') or 0}"]
    if state == STATE_PARTIAL:
        return ["image_partial"]
    return []
