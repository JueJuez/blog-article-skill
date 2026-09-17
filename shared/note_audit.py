# -*- coding: utf-8 -*-
"""单篇笔记抽检：读 note_path / source_path，跑 content_signals 四类判据，产出结构化结果。

2026-09-18 下沉：此前 `scripts/resum_audit_batch.py` 与 `scripts/audit_gate_signals.py`
各有一份 `audit_one`（90% 重叠）与 `_evidence`。判据口径必须单一，故收敛到本模块，
两个脚本只保留各自的输出/排序逻辑。

只读模块：不写任何文件、不改笔记。
"""
from __future__ import annotations

import os
import re

from prompts import content_signals as CS


def evidence(item: dict, limit: int = 200) -> list:
    """摘出 A超长句证据（括号类信号已废弃，不再取证）。"""
    note_path = item.get("note_path") or ""
    if not note_path or not os.path.exists(note_path):
        return []
    note = CS.strip_note(open(note_path, encoding="utf-8").read())
    longest, sent = 0, ""
    for s in re.split(r"[。！？\n]", note):
        n = CS.count_words(s)
        if n > longest:
            longest, sent = n, s.strip()
    return [("A超长句", f"{longest}字: {sent[:limit]}")] if longest > CS.LONG_SENTENCE else []


def audit_one(item: dict, with_evidence: bool = False,
              passthrough_keys: tuple = (), evidence_limit: int = 200) -> dict:
    """对单条「笔记 vs 源」跑四类内容判据。

    Args:
        item: 至少含 `note_path` / `source_path`，可含 `note_type`、`title` 等。
        with_evidence: 是否附带 A超长句取证原文（抽检报告用）。
        passthrough_keys: 需要从 item 原样带出的字段（报表分组用）。
        evidence_limit: 证据句子截断长度。

    Returns:
        dict：note_words / source_words / ratio / anchors / form / structure /
        verbatim / flags（+ 可选 evidence、passthrough 字段）。
    """
    note_raw = open(item["note_path"], encoding="utf-8").read()
    src_raw = open(item["source_path"], encoding="utf-8").read()
    note = CS.strip_note(note_raw)
    source = CS.strip_source(src_raw)
    cf = CS.content_flags(note, source, item.get("note_type") or "")
    out = {
        "title": (item.get("title") or "")[:26],
        "note_words": CS.count_words(note),
        "source_words": CS.count_words(source),
        "ratio": round(CS.count_words(note) / max(1, CS.count_words(source)), 3),
        "anchors": cf["details"].get("anchors", {"total": 0, "matched": 0, "missed": [], "recall": None}),
        "form": cf["details"]["form"],
        "structure": cf["details"]["structure"],
        "verbatim": cf["details"].get("verbatim", 0.0),
        "flags": cf["flags"],
    }
    if passthrough_keys:
        # 放最后：允许调用方用原始字段（如未截断的 title）覆盖上面的摘要字段
        out.update({k: item[k] for k in passthrough_keys if k in item})
    if with_evidence:
        out["evidence"] = evidence(item, limit=evidence_limit)
    return out


def score_of(r: dict, min_anchors: int) -> int:
    """命中打分（与 resum_audit_batch 的判定口径一致）：锚点 2 分 / 形态信号 1 分 /
    结构缺失每项 2 分 / 照搬超阈值 2 分。"""
    s = 0
    a = r["anchors"]
    if a["recall"] is not None and a["total"] >= min_anchors and a["recall"] < CS.RECALL_THRESHOLD:
        s += 2
    s += len(r["form"]["signals"])
    s += 2 * len(r["structure"]["missing"])
    s += 2 if r["verbatim"] > CS.VERBATIM_THRESHOLD else 0
    return s
