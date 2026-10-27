# -*- coding: utf-8 -*-
"""shared/source_material.py — S6 材料层：同版源的结构索引与回查定位。

PLAN-20261026-low-cost-quality §9.3–§9.6（2026-10-27）。纯函数、零 AI、只读源文本。

定位：
- 现状缺口（S6-0 实查）：源文件（articles 的 `_raw_*.md`、视频的 `transcripts/*.md`）
  是纯文本——标题层级/表格块/代码块没有行号索引，回源定位只能整篇读或 ad-hoc 搜索；
  ASR/CC 的 segments 时间戳在落盘缓存时被丢（`videos/asr.py:_save_transcript_cache`
  只写纯文本），「第几分钟讲了什么」不可回查；HTML 表格行列被 `get_text` 打平。
- 本模块只做**索引与声明**，不改抓取行为：对给定的源文本构建可回查的结构索引，
  绑定与 `prompts/provenance.text_hash` 同口径的 `source_sha256`（同版对账），
  并**如实声明**读取范围与缺口——文本通道拿不到的信息（HTML 表格行列、画面、
  未读图片）记 absent/flattened，不伪造（§9.6：只有文本时不伪造版式、时间戳或画面证据）。
- 图片状态**复用** `shared/image_inventory`（同款 url_key 命名、按 URL 可互查台账），
  不建第二套台账。
- 消费方：S7 独立提炼、写作/复核的回源定位；本轮不接线生成 prompt。

索引落盘：`notes/_meta/source_materials/<url_key>.json`（与图片台账同款 key；
URL 为空时退回 source hash 前 16 位）。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATERIALS_DIR = os.path.join(ROOT, "notes", "_meta", "source_materials")

SCHEMA_VERSION = 1

# 固定的通道限制声明（不伪造的一部分：这些信息文本通道天然拿不到/不保留）
TEXT_CHANNEL_LIMITS = (
    "html_table_rows_flattened",   # 文章 HTML 表格行列在抽文本时被打平（articles/fetch.py get_text）
    "no_visual_evidence",          # 无画面/版式证据
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*)$")
_FENCE_RE = re.compile(r"^```(\S*)\s*$")


def _nows_len(text: str) -> int:
    return len(re.sub(r"\s", "", text or ""))


def url_key_of(url: str, source_text: str = "") -> str:
    """索引文件 key：优先 image_inventory 同款 url_key；无 URL 退回源 hash 前 16 位。"""
    from shared.image_inventory import url_key
    if url:
        return url_key(url)
    from prompts.provenance import text_hash
    return text_hash(source_text)[:16]


def index_filename(url: str, source_text: str = "") -> str:
    return f"{url_key_of(url, source_text)}.json"


def _scan_headings(lines):
    out = []
    for i, ln in enumerate(lines, 1):
        m = _HEADING_RE.match(ln)
        if m:
            out.append({"line": i, "level": len(m.group(1)), "text": m.group(2).strip()})
    return out


def _scan_tables(lines):
    """连续的 markdown 管道行块（行首可选空格、以 | 开头且内部含 |）。"""
    out = []
    start = None
    rows = 0
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        is_pipe = s.startswith("|") and s.count("|") >= 2
        if is_pipe:
            if start is None:
                start = i
                rows = 0
            rows += 1
        else:
            if start is not None:
                out.append({"line_start": start, "line_end": i - 1, "rows": rows})
                start = None
    if start is not None:
        out.append({"line_start": start, "line_end": len(lines), "rows": rows})
    return out


def _scan_code_blocks(lines):
    out = []
    open_line = None
    lang = ""
    for i, ln in enumerate(lines, 1):
        m = _FENCE_RE.match(ln.strip())
        if m and open_line is None:
            open_line, lang = i, m.group(1) or ""
        elif ln.strip().startswith("```") and open_line is not None:
            out.append({"line_start": open_line, "line_end": i, "lang": lang})
            open_line, lang = None, ""
    return out


def _scan_paragraph_bounds(lines):
    """连续非空行块 → [start, end]（1-based，含端点）。"""
    bounds = []
    start = None
    for i, ln in enumerate(lines, 1):
        if ln.strip():
            if start is None:
                start = i
        else:
            if start is not None:
                bounds.append([start, i - 1])
                start = None
    if start is not None:
        bounds.append([start, len(lines)])
    return bounds


def _norm_segments(segments):
    """segments 宽松归一：非法段丢弃；返回 (segments, span_seconds) 或 (None, None)。"""
    if not segments:
        return None, None
    out = []
    end = 0.0
    for s in segments:
        try:
            st = float(s.get("start", 0.0) or 0.0)
            du = float(s.get("duration", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        text = str(s.get("text") or "").strip()
        if not text:
            continue
        out.append({"start": round(st, 3), "duration": round(du, 3), "text": text})
        end = max(end, st + du)
    if not out:
        return None, None
    return out, round(end, 3)


def build_index(source_text: str, *, kind: str, title: str = "", url: str = "",
                image_key: str = "", timestamps=None, text_complete: bool = True) -> dict:
    """对同版源文本构建结构索引。纯函数，不落盘。

    Args:
        source_text: 完整源文本（raw 文件全文 / transcript 全文；hash 按此口径）。
        kind: 通道标识（article / video_cc / video_asr / scys / feishu / …）。
        timestamps: 视频侧 segments [{'start','duration','text'}]；None/空 → absent。
        text_complete: 读取范围由调用方如实声明（部分读取必须传 False）。
    """
    from prompts.provenance import text_hash
    text = source_text or ""
    lines = text.split("\n")
    segs, span = _norm_segments(timestamps)
    return {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "kind": kind or "unknown",
        "title": title or "",
        "url": url or "",
        "url_key": url_key_of(url, text),
        "source_sha256": text_hash(text),
        "chars_total": len(text),
        "chars_nows": _nows_len(text),
        "line_count": len(lines),
        "headings": _scan_headings(lines),
        "tables": _scan_tables(lines),
        "code_blocks": _scan_code_blocks(lines),
        "paragraphs": {"count": len(_scan_paragraph_bounds(lines)),
                       "bounds": _scan_paragraph_bounds(lines)},
        "timestamps": ({"present": True, "span_seconds": span, "segments": segs}
                       if segs else None),
        "coverage": {
            "text": "full" if text_complete else "partial",
            "timestamps": "present" if segs else "absent",
            "images": image_key or "no_inventory",
        },
        "text_channel_limits": list(TEXT_CHANNEL_LIMITS),
    }


def verify(index: dict, source_text: str) -> bool:
    """同版对账：hash 失配（换源/改稿）返回 False，不能当有效交接（§9.6）。"""
    if not isinstance(index, dict):
        return False
    recorded = index.get("source_sha256")
    if not recorded or recorded == "unknown":
        return False
    from prompts.provenance import text_hash
    return text_hash(source_text or "") == recorded


def _path(key_or_url: str, store_dir: str = "") -> str:
    from shared.image_inventory import url_key
    name = url_key(key_or_url) if key_or_url.startswith("http") else key_or_url
    return os.path.join(store_dir or MATERIALS_DIR, f"{name}.json")


def save_index(index: dict, store_dir: str = "") -> str:
    d = store_dir or MATERIALS_DIR
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{index.get('url_key') or index.get('source_sha256', '')[:16]}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    return path


def load_index(url_or_key: str, store_dir: str = "") -> "dict | None":
    """按 URL（自动转 url_key）或裸 key 加载；不存在返回 None。"""
    path = _path(url_or_key, store_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def record_video_material(*, url: str, title: str, text: str, segments=None,
                          text_complete: bool = True, image_key: str = "") -> "str | None":
    """视频转写成功后的材料索引落盘入口（ASR / CC 共用）。

    segments 归一失败时如实记 absent；本函数自身不抛错（索引失败不阻塞转写主流程），
    调用方无需 try/except。
    """
    try:
        idx = build_index(text or "", kind="video_asr" if segments is not None else "video_cc",
                          title=title or "", url=url or "", image_key=image_key,
                          timestamps=segments, text_complete=text_complete)
        return save_index(idx)
    except Exception:
        return None
