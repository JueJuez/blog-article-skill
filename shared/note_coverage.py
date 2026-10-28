# -*- coding: utf-8 -*-
"""shared/note_coverage.py — 成稿覆盖机械自查 + 补稿只插不改核验（写作端两环，F7 接线）。

定位（VERDICT §六/§七 + check_calibration.json）：**机械层 = 高召回可疑块提示器**，
不做语义判断、接受误报——误报由补稿环节吸收（补稿 Agent 对每个可疑条目先核对
「改写也算含」，缺了才补，纯插入不改原稿）。

口径冻结（校准后不再调参）：
- 拦截主力 = 素材条目 label 的字符 3-gram 命中率 < `LABEL_3GRAM_THRESHOLD`（0.2）。
  校准：真整章缺失 4/4 召回，每稿误报 3–8 条由补稿环节消化；
- 标题逐字比对与正文 shingle 二级验证经校准**否决废弃**（优稿也误报 24–41/44、6/76），
  只保留为 info 报告项；
- 数字覆盖沿用 `shared.source_metrics` 口径，报告项不拦。

本模块由评估脚本 `notes/_meta/eval/20261029-pack-only-wiring/check_coverage.py`（F5c
校准载体，冻结不改）提升而来，逻辑与其一致；阈值变更必须重新走校准批次。
"""
from __future__ import annotations

import difflib
import re

# 冻结阈值（2026-10-30 校准，check_calibration.json；改这里必须重走校准）
LABEL_3GRAM_THRESHOLD = 0.2
CALIBRATION_REF = ("notes/_meta/eval/20261029-pack-only-wiring/"
                   "check_calibration.json")

_HEADING_RE = re.compile(
    r"^(?:#{1,4}\s*)?(?:[一二三四五六七八九十]+、|第[一二三四五六七八九十]+[章节部分]|\d{1,2}[.、])\s*\S+")
_HEADING_PREFIX = re.compile(
    r"^(?:#{1,4}\s*|[一二三四五六七八九十]+、|第[一二三四五六七八九十]+[章节部分]|\d{1,2}[.、])\s*")


def _grams(s: str, n: int = 3) -> set:
    s = "".join(s.split())
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def label_hit_rate(label: str, note_text: str) -> float:
    g = _grams(label)
    if not g:
        return 1.0
    return sum(1 for x in g if x in note_text) / len(g)


def structural_headings(source_text: str) -> list:
    """源中的章/节标题行 → [(行号, 原行, 关键短语)]（仅 info 用）。"""
    out = []
    for i, ln in enumerate((source_text or "").split("\n"), 1):
        s = ln.strip()
        if not s or len(s) > 40:
            continue
        if _HEADING_RE.match(s):
            key = _HEADING_PREFIX.sub("", s)[:12]
            if len(key) >= 4:
                out.append((i, s, key))
    return out


def item_tier(item: dict) -> str:
    """条目档位：v6 有 tier 三档；缺 tier（v5 旧包）按 trunk 保守拦截。"""
    tier = str(item.get("tier") or "")
    return tier if tier in ("trunk", "support", "drop") else "trunk"


def coverage_issues(pack: dict, note_text: str,
                    threshold: float = LABEL_3GRAM_THRESHOLD) -> list:
    """可疑块清单（v6 起**仅 trunk 条目进拦截**；阈值冻结不变）。

    support 按主线取舍、drop 默认丢——它们的落点缺失不算缺陷，只在 info 报告观察
    （`coverage_report` 的 `nonsupport_suspects`）。缺 tier 的旧包按 trunk 处理（保守方向）。
    """
    issues = []
    for it in (pack.get("items") or []):
        label = str(it.get("label") or "")
        if not label:
            continue
        rate = label_hit_rate(label, note_text)
        if rate < threshold and item_tier(it) == "trunk":
            issues.append({"kind": "item_label", "ref": it.get("id"),
                           "tier": "trunk",
                           "what": label, "rate": round(rate, 3),
                           "why": f"label 3-gram 命中率 {rate:.2f} < {threshold}"})
    return issues


def nonsupport_suspects(pack: dict, note_text: str,
                        threshold: float = LABEL_3GRAM_THRESHOLD) -> list:
    """support/drop 条目中命中率低于阈值者（info 观察，不拦截）。

    drop 未进稿是**预期行为**（默认丢）；support 缺落点供审计观察取舍是否过宽。
    """
    out = []
    for it in (pack.get("items") or []):
        label = str(it.get("label") or "")
        if not label:
            continue
        rate = label_hit_rate(label, note_text)
        tier = item_tier(it)
        if rate < threshold and tier in ("support", "drop"):
            out.append({"ref": it.get("id"), "tier": tier, "what": label,
                        "rate": round(rate, 3)})
    return out


def tier_counts(pack: dict) -> dict:
    """条目三档计数（覆盖报告用）。"""
    counts = {"trunk": 0, "support": 0, "drop": 0, "unlabeled": 0}
    for it in (pack.get("items") or []):
        counts[item_tier(it) if it.get("tier") else "unlabeled"] += 1
    return counts


def coverage_report(source_text: str, pack: dict, note_text: str,
                    threshold: float = LABEL_3GRAM_THRESHOLD) -> dict:
    """完整报告：issues（仅 trunk 拦截项）+ info（标题/数字覆盖 + support/drop 观察项）。"""
    from shared.source_metrics import source_numbers
    info = {}
    heads = structural_headings(source_text)
    info["headings_total"] = len(heads)
    info["headings_missed"] = [{"line": ln, "text": s}
                               for ln, s, key in heads if key not in note_text]
    nums = source_numbers(source_text)
    hit = [x for x in nums if x in note_text]
    info["numbers_total"] = len(nums)
    info["numbers_hit"] = len(hit)
    info["numbers_missed"] = sorted(nums - set(hit))
    info["tiers"] = tier_counts(pack)
    info["nonsupport_suspects"] = nonsupport_suspects(pack, note_text, threshold)
    return {"issues": coverage_issues(pack, note_text, threshold), "info": info}


def insert_only_violations(original: str, patched: str) -> list:
    """补稿「只插不改」核验：原稿行不得被替换或删除。返回空 = 纯插入通过。

    任何 replace/delete 操作码都算补稿引入的确定性改动（停止条件，F7 边界），
    返回项含操作类型与改动行预览，供定位。
    """
    orig_lines = original.splitlines()
    new_lines = patched.splitlines()
    sm = difflib.SequenceMatcher(a=orig_lines, b=new_lines, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("equal", "insert"):
            continue
        out.append({"op": tag, "orig_lines": [i1 + 1, i2],
                    "preview": " / ".join(orig_lines[i1:i2])[:160]})
    return out
