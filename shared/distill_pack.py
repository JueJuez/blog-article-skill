# -*- coding: utf-8 -*-
"""shared/distill_pack.py — S7 独立提炼产物（pack）的校验与存储。

PLAN-20261026-low-cost-quality §9.3–§9.4（2026-10-27）。**候选实现，未接生产派单**。

职责边界：
- prompt 契约在 `prompts/distill.py`（三类产物定义、枚举真源、自包含任务渲染）；
  本模块只做**机械校验**与**落盘**。
- 校验是**报告项**（对齐 A4 判据分级）：`validate_pack` 返回 ok/issues，不计分、
  不拦盘、不做语义判定——**字段齐全 ≠ 语义完整**；语义核对属独立复核（S8）。
  机械边界包括：schema 字段、同版 hash 对账（§9.6：换源/改稿后旧提炼不能当有效交接）、
  行号 sanity（越界/空行）、关系引用不悬空、枚举合法、`stance=quote` 原话机检
  （去空白口径与 `prompts/extract_checklist` 一致；未命中是 issue，不静默丢弃——
  pack 是结构化中间产物，由编排方/复核者看着 issue 处置）。
- 存储：`notes/_meta/distill_packs/<url_key>.json`（与 S6 材料索引/图片台账同款
  url_key，互查）。**同版重写幂等**（失败可续跑）；`store_dir` 可指定评估目录
  做隔离试验，不污染生产。
- pack 是 JSON sidecar，**不拼接进任何生成 prompt**——消费方式由 S8/S9 的写作
  契约决定；`prompts.distill.DISTILL_MARKER` 的隔离断言测试守这条边界。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKS_DIR = os.path.join(ROOT, "notes", "_meta", "distill_packs")

SCHEMA_VERSION = 1

# 枚举从 prompt 契约侧 import（单一真源），import 失败时兜底（防循环依赖场景）
try:
    from prompts.distill import ITEM_TYPES, STANCE_KINDS, RELATION_KINDS
except Exception:  # pragma: no cover
    ITEM_TYPES = ("viewpoint", "case", "step", "number", "resource", "condition", "counterexample")
    STANCE_KINDS = ("quote", "paraphrase")
    RELATION_KINDS = ("supports", "depends", "limits", "contradicts")

_WS_RE = re.compile(r"\s")
_MAINLINE_FIELDS = ("question", "progression", "conclusions")


def _norm(s: str) -> str:
    """去空白口径（与 extract_checklist / content_signals.nows 一致）。"""
    return _WS_RE.sub("", s or "")


def url_key_of(url: str, source_text: str = "") -> str:
    """与 S6 材料索引/图片台账同款 key；无 URL 退回源 hash 前 16 位。"""
    from shared.image_inventory import url_key
    if url:
        return url_key(url)
    from prompts.provenance import text_hash
    return text_hash(source_text)[:16]


def build_pack(*, title: str, url: str, source_text: str,
               distill_prompt_sha256: str, parsed: dict,
               material_key: str = "", notes: str = "") -> dict:
    """把提炼模型输出的 JSON（parsed）装进带溯源的 pack 壳。

    - `source_sha256` 由本函数按 source_text 现算（不信 parsed 里自带的）。
    - `material_key`：S6 材料索引的 url_key；传入即 material_available=True，
      不传如实 False（不伪造结构可回查）。
    """
    from prompts.provenance import text_hash
    mainline = parsed.get("mainline") or {}
    items = parsed.get("items") or []
    relations = parsed.get("relations") or []
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "distill_pack",
        "recorded_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "url": url or "",
        "url_key": url_key_of(url, source_text),
        "title": title or "",
        "source_sha256": text_hash(source_text or ""),
        "distill_prompt_sha256": distill_prompt_sha256 or "unknown",
        "material_key": material_key or "",
        "material_available": bool(material_key),
        "mainline": {
            "question": str(mainline.get("question") or ""),
            "progression": [str(x) for x in (mainline.get("progression") or [])],
            "conclusions": [str(x) for x in (mainline.get("conclusions") or [])],
        },
        "items": [{
            "id": str(it.get("id") or ""),
            "type": str(it.get("type") or ""),
            "stance": str(it.get("stance") or ""),
            "content": str(it.get("content") or ""),
            "source_lines": list(it.get("source_lines") or []),
        } for it in items],
        "relations": [{
            "from": str(r.get("from") or ""),
            "to": str(r.get("to") or ""),
            "kind": str(r.get("kind") or ""),
            "note": str(r.get("note") or ""),
        } for r in relations],
        "coverage": {
            "source_lines_total": len((source_text or "").split("\n")),
            "item_count": len(items),
            "relation_count": len(relations),
        },
        "notes": notes or "",
    }


def validate_pack(pack: dict, source_text: str) -> dict:
    """机械校验。返回 {"ok": bool, "issues": [str]}；ok 只代表机械边界通过。

    换源/改稿（hash 失配）直接 ok=False——旧提炼不能当有效交接（§9.6）。
    其余 issue 为报告项：交给编排方/独立复核处置，本模块不修改 pack。
    """
    issues: list = []
    if not isinstance(pack, dict):
        return {"ok": False, "issues": ["pack 不是对象"]}
    from prompts.provenance import text_hash

    # ① 同版绑定（硬）
    recorded = pack.get("source_sha256")
    if not recorded or recorded == "unknown":
        issues.append("source_sha256 缺失，无法同版对账")
    elif recorded != text_hash(source_text or ""):
        issues.append("source_sha256 与给定源文本失配（换源/改稿后旧提炼不能当有效交接）")

    # ② 主线地图
    ml = pack.get("mainline") or {}
    if not any((ml.get(f) for f in _MAINLINE_FIELDS)):
        issues.append("主线地图缺失（question/progression/conclusions 全空）")

    # ③ 条目
    items = pack.get("items") or []
    if not items:
        issues.append("信息条目为空（源再短也应有主线外条目；确无可提炼须在 notes 说明）")
    ids = set()
    lines = (source_text or "").split("\n")
    n_lines = len(lines)
    for it in items:
        iid = it.get("id") or ""
        if not iid:
            issues.append("存在无 id 条目")
            continue
        if iid in ids:
            issues.append(f"条目 id 重复：{iid}")
        ids.add(iid)
        if it.get("type") not in ITEM_TYPES:
            issues.append(f"{iid}: type 非法（{it.get('type')!r}）")
        if it.get("stance") not in STANCE_KINDS:
            issues.append(f"{iid}: stance 非法（{it.get('stance')!r}）")
        sl = it.get("source_lines") or []
        if (not isinstance(sl, list) or len(sl) != 2
                or not all(isinstance(x, int) for x in sl)):
            issues.append(f"{iid}: source_lines 须为 [起, 止] 整数二元组")
            continue
        start, end = sl
        if start < 1 or end < start or end > n_lines:
            issues.append(f"{iid}: source_lines [{start},{end}] 越界（源共 {n_lines} 行）")
            continue
        span = lines[start - 1:end]
        if not any(s.strip() for s in span):
            issues.append(f"{iid}: source_lines [{start},{end}] 指向空行，定位可疑")
        if it.get("stance") == "quote":
            if _norm(it.get("content") or "") not in _norm(source_text or ""):
                issues.append(f"{iid}: 标为原话但内容未能在源里命中（去空白口径）——"
                              "改写/拼接的原话不得冒充原句")

    # ④ 关系
    for r in pack.get("relations") or []:
        if r.get("kind") not in RELATION_KINDS:
            issues.append(f"关系 kind 非法（{r.get('kind')!r}）")
        for side in ("from", "to"):
            ref = r.get(side)
            if ref and ref not in ids:
                issues.append(f"关系 {side} 引用悬空：{ref}（不存在于条目 id）")

    # ⑤ 如实声明
    if pack.get("material_available") and not pack.get("material_key"):
        issues.append("material_available=True 但 material_key 为空（不得伪造结构可回查）")

    return {"ok": not issues, "issues": issues}


def merge_chunk_packs(*, title: str, url: str, source_text: str,
                      distill_prompt_sha256: str, parts: list,
                      notes: str = "") -> dict:
    """把同一篇源的分块提炼回包合并成**一个整篇 pack**（>40k 超长源，S9 第 8 条③）。

    `parts`: [{"chunk": {"index","total","start_line","end_line"}, "parsed": {...}}]，按块序给。

    合并口径（如实，不臆造）：
    - 条目 id 加块前缀（`i1` → `c2_i1`）避免跨块撞号；**`source_lines` 不动**——提炼者收到的是
      `prompts.distill.numbered_source` 带全局行号前缀的文本，报的就是全局行号。
    - `relations` 的 from/to 同步加块前缀；**跨块关系提炼者看不到，不臆造**，缺失事实写进 notes。
    - 相邻块重叠区会产生重复条目：按去空白 `content` 去重，保留块序靠前那条，去重数记 notes。
    - `mainline` 按块顺序拼接（question 取首块，progression/conclusions 逐块追加），
      并在 notes 注明「主线为分块拼接」——这与整篇提炼得到的主线不等价，如实标注。
    - `distill_prompt_sha256` 记**整篇**契约 hash（`prompts.distill.prompt_hash`），
      各块自身 hash 另记 `chunk_prompt_sha256s`，供版本绑定逐块对账。
    """
    from prompts.provenance import text_hash
    parts = [p for p in (parts or []) if isinstance(p, dict)]
    order = sorted(parts, key=lambda p: int((p.get("chunk") or {}).get("index") or 0))

    question, prog, concl, notes_out = "", [], [], []
    items, relations, seen = [], [], set()
    chunk_meta, dup_count = [], 0
    for p in order:
        chunk = p.get("chunk") or {}
        parsed = p.get("parsed") or {}
        tag = f"c{int(chunk.get('index') or 0)}"
        ml = parsed.get("mainline") or {}
        if not question and ml.get("question"):
            question = str(ml.get("question") or "")
        prog += [str(x) for x in (ml.get("progression") or [])]
        concl += [str(x) for x in (ml.get("conclusions") or [])]

        local_ids = set()
        n_before = len(items)
        for it in (parsed.get("items") or []):
            cid = str(it.get("id") or "")
            new_id = f"{tag}_{cid}" if cid else ""
            key = _norm(str(it.get("content") or ""))
            if key and key in seen:
                dup_count += 1
                continue
            if key:
                seen.add(key)
            local_ids.add(cid)
            items.append({
                "id": new_id,
                "type": str(it.get("type") or ""),
                "stance": str(it.get("stance") or ""),
                "content": str(it.get("content") or ""),
                "source_lines": list(it.get("source_lines") or []),
            })
        for r in (parsed.get("relations") or []):
            f = str(r.get("from") or "")
            t = str(r.get("to") or "")
            # 指向被去重掉的条目时保留会变悬空引用 → 丢弃并计数
            if f not in local_ids or t not in local_ids:
                continue
            relations.append({"from": f"{tag}_{f}", "to": f"{tag}_{t}",
                              "kind": str(r.get("kind") or ""), "note": str(r.get("note") or "")})
        chunk_meta.append({"index": int(chunk.get("index") or 0),
                           "start_line": chunk.get("start_line"), "end_line": chunk.get("end_line"),
                           "items": len(items) - n_before})

    if len(order) > 1:
        notes_out.append(f"分块提炼：{len(order)} 块合并（主线为逐块拼接，与整篇提炼不等价）")
        notes_out.append("跨块关系未生成：提炼者一次只见一块，不做跨块臆测")
    if dup_count:
        notes_out.append(f"重叠区去重 {dup_count} 条重复条目（保留块序靠前那条）")
    if notes:
        notes_out.append(notes)

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "distill_pack",
        "recorded_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "url": url or "",
        "url_key": url_key_of(url, source_text),
        "title": title or "",
        "source_sha256": text_hash(source_text or ""),
        "distill_prompt_sha256": distill_prompt_sha256 or "unknown",
        "chunk_prompt_sha256s": [str(p.get("prompt_sha256") or "") for p in order],
        "chunks": chunk_meta,
        "material_key": "",
        "material_available": False,
        "mainline": {"question": question, "progression": prog, "conclusions": concl},
        "items": items,
        "relations": relations,
        "coverage": {
            "source_lines_total": len((source_text or "").split("\n")),
            "item_count": len(items),
            "relation_count": len(relations),
            "chunk_count": len(chunk_meta),
        },
        "notes": "；".join(notes_out),
    }


def save_pack(pack: dict, store_dir: str = "") -> str:
    """落盘（同版幂等：同 url_key 直接覆盖）。返回文件路径。"""
    d = store_dir or PACKS_DIR
    os.makedirs(d, exist_ok=True)
    key = pack.get("url_key") or (pack.get("source_sha256") or "")[:16]
    path = os.path.join(d, f"{key}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, indent=1)
    return path


def load_pack(url_or_key: str, store_dir: str = "") -> "dict | None":
    """按 URL（自动转 url_key）或裸 key 加载；不存在返回 None。

    URL 判定用 `://` 而非 `http` 前缀——评估隔离目录用 `eval://s8/D1` 这类
    伪协议时曾读不回自己存的 pack（2026-10-27 S8 首轮实抓）。
    """
    from shared.image_inventory import url_key
    name = url_key(url_or_key) if "://" in (url_or_key or "") else url_or_key
    path = os.path.join(store_dir or PACKS_DIR, f"{name}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None
