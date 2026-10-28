# -*- coding: utf-8 -*-
"""prompts/synthesis.py — 材料包交接与全篇统稿契约（prompt 侧，N3）。

PLAN-20261028-long-source-summary §4-N3。**只拼进写作 prompt，不拼进提炼任务**。

三层边界（不要互相越界）：
- `prompts/distill.py`：块级取材契约。它的 `DISTILL_MARKER` **不得**出现在写作 prompt，
  由 `tests/test_distill_dispatch.py::test_template_isolation_marker_not_in_block` 守。
- 本模块：材料包**怎么被写作消费**——同版源行号回查、跨块对账（deferred / 块边界 /
  指代条件冲突）、不做二次压缩。它**必须**出现在写作 prompt 里，否则装配块只给了
  一堆条目却没说清怎么统稿。
- `shared/distill_pack.py`：机械校验与落盘；不做语义判断。

版本语义：`HANDOFF_MARKER` 随每个装配块下发，`shared.distill_dispatch.block_is_current`
用它判断「N3 之前装配的块没有统稿契约」。升级本契约只换标记版本，不动提炼契约版本
（换提炼契约会让 pack 全部失效，代价大得多）。
"""
from __future__ import annotations

SYNTHESIS_VERSION = 1
HANDOFF_MARKER = "<!-- DISTILL_HANDOFF v2 -->"

# 材料包使用契约（始终下发）：回答「这块材料能不能当标准答案、和源冲突听谁的」。
HANDOFF_RULES = (
    "- **材料包不是大纲、也不是成稿草案**：正文按上面模板重组，不按条目顺序扩写。\n"
    "- **材料包不是标准答案**：它与同版源冲突时以源为准；条目外的源信息仍须覆盖。\n"
    "- **数字口径不合并**：`readings` 已按对象/单位/期间/基准分列，不得合并、换算或替作者二选一。\n"
    "- **标为 quote 的是逐字原话**，可直接引用；其余为摘意，改写时保持口径。"
)


def needs_synthesis(pack: dict) -> bool:
    """这块材料是否需要**全篇统稿**：多块合并，或提炼者留下了跨段事项。

    单块且无 deferred 的 pack 仍走正常写作，只是不需要额外的跨块对账段。
    """
    cov = (pack or {}).get("coverage") or {}
    try:
        chunk_count = int(cov.get("chunk_count") or 1)
    except (TypeError, ValueError):
        chunk_count = 1
    return chunk_count > 1 or bool((pack or {}).get("deferred"))


def chunk_boundaries(pack: dict) -> list:
    """块边界（机械提取，不生成内容）。无 `chunks` 记录时退回 `processed_ranges`。"""
    out = []
    for c in (pack or {}).get("chunks") or []:
        if not isinstance(c, dict):
            continue
        start = c.get("core_start_line") or c.get("start_line")
        end = c.get("core_end_line") or c.get("end_line")
        if not start or not end:
            continue
        out.append({"index": int(c.get("index") or len(out) + 1),
                    "core_start_line": int(start), "core_end_line": int(end),
                    "context_lines": int(c.get("context_lines") or 0),
                    "continued": bool(c.get("continued")),
                    "continues": bool(c.get("continues"))})
    if out:
        return out
    cov = (pack or {}).get("coverage") or {}
    for k, rng in enumerate(cov.get("processed_ranges") or [], 1):
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            out.append({"index": k, "core_start_line": int(rng[0]),
                        "core_end_line": int(rng[1]), "context_lines": 0,
                        "continued": False, "continues": False})
    return out


def synthesis_targets(pack: dict) -> dict:
    """统稿要核对的对象：deferred 逐条 + 块边界 + 实际处理范围（全是机械提取）。"""
    cov = (pack or {}).get("coverage") or {}
    return {"deferred": [str(x) for x in (pack or {}).get("deferred") or []],
            "boundaries": chunk_boundaries(pack),
            "ranges": [list(r) for r in cov.get("processed_ranges") or []
                       if isinstance(r, (list, tuple)) and len(r) == 2]}


def render_source_ref(source_ref: "dict | None", total_lines: int = 0) -> str:
    """回源核对说明。读不到源就明说读不到——回源不可用不等于缺口可以补写。"""
    ref = source_ref or {}
    path = str(ref.get("path") or "").strip()
    lines = int(ref.get("lines") or total_lines or 0)
    sha = str(ref.get("sha256") or "")[:12]
    if path:
        tail = f"，共 {lines} 行" if lines else ""
        tail += f"，sha256 前 12 位 `{sha}`" if sha else ""
        return (f"同版源文件 `{path}`{tail}；材料条目的 `source_lines` 即该文件 1-based "
                "行号区间。需要核对原文时按该区间回读。")
    return ("未记录同版源文件路径 → 回源核对不可用；材料包没覆盖到的内容**如实保留缺口**，"
            "不得拿材料包里的二手概括补足或当成全文覆盖。")


def render_section(pack: dict, source_ref: "dict | None" = None) -> str:
    """渲染交接 + 统稿段（放在材料包块开头，标记行供版本判定）。"""
    cov = (pack or {}).get("coverage") or {}
    lines = [HANDOFF_MARKER, "**材料包使用契约**",
             "- 回源核对：" + render_source_ref(source_ref,
                                                total_lines=int(cov.get("source_lines_total") or 0)),
             HANDOFF_RULES]
    if not needs_synthesis(pack):
        return "\n".join(lines)

    targets = synthesis_targets(pack)
    total = int(cov.get("chunk_count") or len(targets["boundaries"]) or 1)
    heading = (f"## 全篇统稿（{total} 块分块提炼，成文前先统稿）" if total > 1
               else "## 全篇统稿（提炼者留下了跨段交接事项，成文前先统稿）")
    lines += ["", heading,
              "- **不对材料包做二次压缩**：块级内容已是摘意，把它再摘一遍不等于覆盖全文。",
              "- **跨块对账**：逐条处理下面 deferred 事项，核对跨块指代（「他/它/上面说的」）、"
              "条件、例外、相反读数与冲突；相邻块边界前后的同一论证要接起来，不能只取一块。",
              "- **块级主线不是全篇结论**：块级 `mainline` 只覆盖本块；全篇结论须由跨块核对后的源支持。"]
    boundaries = targets["boundaries"]
    if boundaries:
        parts = []
        for b in boundaries:
            s = f"第{b['index']}块 核心 {b['core_start_line']}–{b['core_end_line']} 行"
            if b["context_lines"]:
                s += f"（含前 {b['context_lines']} 行重叠上下文，只用于消解指代）"
            if b["continued"]:
                s += "，从同一结构单元接续"
            if b["continues"]:
                s += "，末尾结构单元未结束"
            parts.append(s)
        lines.append("- 块边界：" + "；".join(parts))
    deferred = targets["deferred"]
    if deferred:
        lines.append(f"- deferred（{len(deferred)} 条，逐条处理）：")
        lines += [f"  {k}. {x}" for k, x in enumerate(deferred, 1)]
    else:
        lines.append("- deferred：无（块边界处的指代与条件仍须自行核对）")
    return "\n".join(lines)


def block_has_handoff(block: str) -> bool:
    """已装配块是否带本版交接契约（旧块没有 → 需要重装）。"""
    return HANDOFF_MARKER in (block or "")
