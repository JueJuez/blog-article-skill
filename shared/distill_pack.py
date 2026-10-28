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

SCHEMA_VERSION = 4

# 枚举从 prompt 契约侧 import（单一真源），import 失败时兜底（防循环依赖场景）
try:
    from prompts.distill import (ITEM_TYPES, STANCE_KINDS, RELATION_KINDS,  # noqa: F401
                                 ITEM_TIERS, CONTRACT_VERSION)
except Exception:  # pragma: no cover
    ITEM_TYPES = ("argument", "viewpoint", "case", "step", "number", "resource",
                  "condition", "counterexample")
    STANCE_KINDS = ("quote", "paraphrase")
    RELATION_KINDS = ("supports", "depends", "limits", "contradicts")
    ITEM_TIERS = ("trunk", "support", "drop")
    CONTRACT_VERSION = 0

_WS_RE = re.compile(r"\s")
_MAINLINE_FIELDS = ("question", "progression", "conclusions")
# 一条读数的全部字段（v4「数字同段不混口径」的结构化载体）
READING_FIELDS = ("value", "object", "unit", "period", "basis", "certainty")


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


def _norm_readings(readings) -> list:
    """读数归一为固定字段的字符串列表（非法项丢弃，交由 `validate_pack` 报出来）。

    这里**不做语义合并**：不同期间/对象的两条读数即使数值相同也各自保留。
    """
    out = []
    for r in readings or []:
        if not isinstance(r, dict):
            continue
        out.append({f: str(r.get(f) or "") for f in READING_FIELDS})
    return out


def _readings_field(item: dict) -> dict:
    """条目里**只在有读数时**才带 `readings` 键——空数组不进 JSON，省材料包体积。"""
    readings = _norm_readings(item.get("readings"))
    return {"readings": readings} if readings else {}


def _norm_deferred(deferred) -> list:
    if isinstance(deferred, str):
        return [deferred] if deferred.strip() else []
    if not isinstance(deferred, (list, tuple)):
        return []
    return [str(x) for x in deferred if str(x or "").strip()]


# ---------------------------------------------------------------- v6 意图树

def _norm_node(node, source_text: str) -> dict:
    """单个树节点规范化（不抛错：形状不对就给空壳，交给 `hierarchy_issues` 报出来）。"""
    if not isinstance(node, dict):
        return {"id": "", "label": "", "intent": "", "source_spans": [], "children": []}
    spans = node.get("source_spans") or []
    if isinstance(spans, list) and all(isinstance(x, list) for x in spans) \
            and not source_span_issues(spans, source_text or ""):
        spans_norm = [[int(a), int(b)] for a, b in spans]
    else:
        spans_norm = []
    return {"id": str(node.get("id") or ""), "label": str(node.get("label") or ""),
            "intent": str(node.get("intent") or ""), "source_spans": spans_norm,
            "children": [_norm_node(c, source_text) for c in node.get("children") or []
                         if isinstance(c, dict)]}


def _norm_hierarchy(hier, source_text: str) -> dict:
    """树规范化：根 intent 可空；子节点递归。"""
    if not isinstance(hier, dict):
        return {"intent": "", "children": []}
    return {"intent": str(hier.get("intent") or ""),
            "children": [_norm_node(c, source_text)
                         for c in hier.get("children") or [] if isinstance(c, dict)]}


def iter_nodes(hier: dict):
    """先序遍历树节点（不含根）。"""
    stack = list((hier or {}).get("children") or [])
    while stack:
        node = stack.pop(0)
        yield node
        stack = list(node.get("children") or []) + stack


def _collect_node_ids(hier: dict) -> set:
    return {str(n.get("id") or "") for n in iter_nodes(hier) if n.get("id")}


def _prefix_node_ids(node: dict, tag: str) -> dict:
    """分块合并时给节点及子树 id 加块前缀（`n1` → `c2_n1`），与条目 id 同一口径。"""
    out = dict(node)
    if out.get("id"):
        out["id"] = f"{tag}_{out['id']}"
    out["children"] = [_prefix_node_ids(c, tag) for c in out.get("children") or []]
    return out


def _hierarchy_issues(pack: dict, source_text: str) -> list:
    """v6 树形机械校验：形状、节点 id 唯一、非根节点 intent 必填、节点 span 不越界。"""
    issues: list = []
    hier = pack.get("hierarchy")
    if not isinstance(hier, dict) or not isinstance(hier.get("children"), list):
        return ["hierarchy 缺失或形状非法（v6 必须给意图树；纯寒暄走 no_substance 出口）"]
    lines = (source_text or "").split("\n")
    seen = set()
    stack = [("根", {"children": hier.get("children") or []})]
    while stack:
        prefix, holder = stack.pop(0)
        for k, node in enumerate(holder.get("children") or []):
            if not isinstance(node, dict):
                issues.append(f"{prefix}children[{k}] 须为对象")
                continue
            nid = str(node.get("id") or "")
            name = f"{prefix}{nid or k + 1}"
            if not nid:
                issues.append(f"{name}: 节点缺 id")
            elif nid in seen:
                issues.append(f"{name}: 节点 id 重复")
            seen.add(nid)
            if not str(node.get("intent") or "").strip():
                issues.append(f"{name}: 节点缺 intent（作者用意一句话，必填；"
                              "填不出用意的不配成节点）")
            spans = node.get("source_spans") or []
            if not spans:
                issues.append(f"{name}: 节点缺 source_spans（该节点覆盖的源行区间）")
            else:
                issues += [f"{name}: {m}" for m in source_span_issues(spans, source_text)]
            stack.append((f"{name}.", node))
    return issues


def _item_v6_issues(pack: dict) -> list:
    """v6 条目挂接校验：node 引用存在、tier 枚举、trunk 不为空。"""
    issues: list = []
    node_ids = _collect_node_ids(pack.get("hierarchy") or {})
    trunk_count = 0
    for it in pack.get("items") or []:
        iid = it.get("id") or "?"
        nid = str(it.get("node_id") or "")
        if not nid:
            issues.append(f"{iid}: 条目缺 node_id（v6 条目必须挂节点）")
        elif node_ids and nid not in node_ids:
            issues.append(f"{iid}: node_id 引用悬空：{nid}（不存在于树节点）")
        tier = it.get("tier")
        if tier not in ITEM_TIERS:
            issues.append(f"{iid}: tier 非法（{tier!r}，须 {ITEM_TIERS}）")
        elif tier == "trunk":
            trunk_count += 1
    if (pack.get("items") or []) and not trunk_count and not pack.get("no_substance"):
        issues.append("trunk 为空：没有任何 trunk 条目（主干内容必落点，"
                      "确无主干内容应走 no_substance 出口）")
    return issues


def _line_count(source_text: str) -> int:
    return len((source_text or "").split("\n"))


def _range_union(ranges: list) -> list:
    """把若干 [起,止] 合并成升序不重叠区间（空区间列表原样返回空）。"""
    valid = sorted((int(a), int(b)) for a, b in ranges if a <= b)
    out = []
    for a, b in valid:
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return [[a, b] for a, b in out]


def source_span_issues(spans, source_text: str) -> list:
    """位置必须明确且有序，不自动截断、排序或吞掉重叠。"""
    if not isinstance(spans, list) or not spans:
        return ["source_spans 缺失或为空，无法提取原文"]
    lines = (source_text or "").split("\n")
    issues, previous = [], 0
    for span in spans:
        if (not isinstance(span, list) or len(span) != 2
                or not all(type(x) is int for x in span)):
            issues.append("source_spans 须为原文 [起,止] 整数行区间")
            continue
        a, b = span
        if not 1 <= a <= b <= len(lines):
            issues.append(f"原文位置越界或倒序：{span}")
        elif a <= previous:
            issues.append(f"原文位置重复、重叠或乱序：{span}")
        elif not any(x.strip() for x in lines[a - 1:b]):
            issues.append(f"原文位置指向空行：{span}")
        previous = b
    return issues


SOURCE_GAP = "\n\n[…非连续原文片段…]\n\n"


def uncovered_ranges(pack: dict, source_text: str) -> list:
    """素材未覆盖的源行区间（升序合并）。用于让写作端只吃包也不缺原文。"""
    lines = (source_text or "").split("\n")
    covered = set()
    for it in (pack or {}).get("items") or []:
        for span in it.get("source_spans") or []:
            if (isinstance(span, list) and len(span) == 2
                    and all(type(x) is int for x in span) and 1 <= span[0] <= span[1]):
                covered.update(range(span[0], span[1] + 1))
    out = []
    for n in range(1, len(lines) + 1):
        if n in covered:
            continue
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return out


def uncovered_excerpt(pack: dict, source_text: str) -> str:
    """未选区间的原文（逐字、按源顺序），供写作端补全覆盖。"""
    lines = (source_text or "").split("\n")
    return "\n".join("\n".join(lines[a - 1:b]) for a, b in uncovered_ranges(pack, source_text))


def source_excerpt(spans, source_text: str) -> str:
    """只从同版源复制正文，不消费模型生成的 content/readings。"""
    if source_span_issues(spans, source_text):
        return ""
    lines = (source_text or "").split("\n")
    return SOURCE_GAP.join("\n".join(lines[a - 1:b]) for a, b in spans)


def _source_items(parsed: dict, source_text: str) -> list:
    items = []
    for it in parsed.get("items") or []:
        # source_lines 单区间兼容位置输入，绝不复用旧的模型正文。
        spans = it.get("source_spans") if "source_spans" in it else [it.get("source_lines") or []]
        valid = not source_span_issues(spans, source_text)
        envelope = [spans[0][0], spans[-1][1]] if valid else []
        items.append({"id": str(it.get("id") or ""), "type": str(it.get("type") or ""),
                      "stance": "source", "content": source_excerpt(spans, source_text),
                      "source_lines": envelope, "source_spans": spans,
                      "label": str(it.get("label") or ""),
                      "node_id": str(it.get("node_id") or ""),
                      "tier": str(it.get("tier") or ""),
                      "notes": str(it.get("notes") or "")})
    return items


def build_pack(*, title: str, url: str, source_text: str,
               distill_prompt_sha256: str, parsed: dict,
               material_key: str = "", notes: str = "",
               processed_ranges: "list | None" = None,
               complete: bool = True) -> dict:
    """把提炼模型输出的 JSON（parsed）装进带溯源的 pack 壳。

    - `source_sha256` 由本函数按 source_text 现算（不信 parsed 里自带的）。
    - `material_key`：S6 材料索引的 url_key；传入即 material_available=True，
      不传如实 False（不伪造结构可回查）。
    - `processed_ranges`：**实际处理范围**（1-based 行区间，来自派单块边界，不采信模型
      自报）。默认整篇 `[[1, 总行数]]`；`complete` 表示是否声称已覆盖全文——
      声称完整但区间有洞会被 `validate_pack` 拦下。
    """
    from prompts.provenance import text_hash
    # v6：意图树 + 三档选材权；`no_substance` 是唯一降级出口（纯寒暄/无实质内容）。
    # v5 的平面 `mainline` 已并入树根（根 intent），v6 pack 不再有 mainline 字段。
    no_substance = bool(parsed.get("no_substance"))
    items = [] if no_substance else _source_items(parsed, source_text)
    relations = [] if no_substance else (parsed.get("relations") or [])
    hierarchy = _norm_hierarchy(parsed.get("hierarchy"), source_text) \
        if not no_substance else {"intent": "", "children": []}
    n_lines = _line_count(source_text)
    ranges = processed_ranges if processed_ranges else [[1, n_lines]]
    pack = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "kind": "distill_pack",
        "recorded_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "url": url or "",
        "url_key": url_key_of(url, source_text),
        "title": title or "",
        "source_sha256": text_hash(source_text or ""),
        "distill_prompt_sha256": distill_prompt_sha256 or "unknown",
        "material_key": material_key or "",
        "material_available": bool(material_key),
        "hierarchy": hierarchy,
        "items": items,
        "relations": [{
            "from": str(r.get("from") or ""),
            "to": str(r.get("to") or ""),
            "kind": str(r.get("kind") or ""),
            "note": str(r.get("note") or ""),
        } for r in relations],
        "deferred": _norm_deferred(parsed.get("deferred")),
        "coverage": {
            "source_lines_total": n_lines,
            "processed_ranges": [[int(a), int(b)] for a, b in ranges],
            "complete": bool(complete),
            "chunk_count": 1,
            "item_count": len(items),
            "relation_count": len(relations),
        },
        "notes": notes or "",
    }
    if no_substance:
        pack["no_substance"] = True
        pack["no_substance_reason"] = str(parsed.get("reason") or "")
    return pack


def validate_pack(pack: dict, source_text: str) -> dict:
    """机械校验。返回 {"ok": bool, "issues": [str]}；ok 只代表机械边界通过。

    换源/改稿（hash 失配）直接 ok=False——旧提炼不能当有效交接（§9.6）。
    其余 issue 为报告项：交给编排方/独立复核处置，本模块不修改 pack。
    """
    issues: list = []
    if not isinstance(pack, dict):
        return {"ok": False, "issues": ["pack 不是对象"]}
    from prompts.provenance import text_hash

    # ⓪ 版本绑定（硬）：schema 与提炼契约都要对得上——旧形态不得冒充本轮结果
    sv = pack.get("schema_version")
    if sv != SCHEMA_VERSION:
        issues.append(f"schema_version 非当前版本（{sv!r} != {SCHEMA_VERSION}），"
                      "旧 schema 不能当本轮有效交接")
    cv = pack.get("contract_version")
    if cv in (None, ""):
        issues.append("contract_version 缺失，无法确认提炼契约版本")
    elif str(cv) != str(CONTRACT_VERSION):
        issues.append(f"契约版本不一致（{cv!r} != {CONTRACT_VERSION}），"
                      "旧契约 pack 不得冒充当前结果")

    # ① 同版绑定（硬）
    recorded = pack.get("source_sha256")
    if not recorded or recorded == "unknown":
        issues.append("source_sha256 缺失，无法同版对账")
    elif recorded != text_hash(source_text or ""):
        issues.append("source_sha256 与给定源文本失配（换源/改稿后旧提炼不能当有效交接）")

    # ② 意图树（v6）/主线地图（v5 旧形态）：版本门只认当前契约
    if pack.get("no_substance"):
        if not str(pack.get("no_substance_reason") or "").strip():
            issues.append("no_substance 出口缺 reason（必须一句话说明为何无实质内容）")
        if pack.get("items"):
            issues.append("no_substance=True 但仍有条目（两态互斥，不得硬造树）")
    elif "mainline" in pack and "hierarchy" not in pack:
        # v5 旧形态（已被版本门拦）：validate 仍按旧口径报主线地图，便于离线解读
        ml = pack.get("mainline") or {}
        if not any((ml.get(f) for f in _MAINLINE_FIELDS)):
            issues.append("主线地图缺失（question/progression/conclusions 全空）")
    else:
        issues += _hierarchy_issues(pack, source_text)
        issues += _item_v6_issues(pack)

    # ③ 条目
    items = pack.get("items") or []
    if not items and not pack.get("no_substance"):
        issues.append("信息条目为空（源再短也应有主干外条目；确无可提炼须在 notes 说明）")
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
        span_issues = source_span_issues(it.get("source_spans"), source_text)
        issues += [f"{iid}: {m}" for m in span_issues]
        if not span_issues:
            spans = it["source_spans"]
            if it.get("source_lines") != [spans[0][0], spans[-1][1]]:
                issues.append(f"{iid}: source_lines 与原文片段位置不一致")
            if it.get("content") != source_excerpt(spans, source_text):
                issues.append(f"{iid}: 原文原话与声明位置不一致，素材正文不得改写")
        if it.get("stance") != "source":
            issues.append(f"{iid}: 原文素材 stance 必须为 source")
        if it.get("readings"):
            issues.append(f"{iid}: 原文素材不接受二次生成的读数 readings，口径随原文保留")
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

    # ⑤ 跨段交接（deferred 须是字符串数组，交给统稿消费）
    deferred = pack.get("deferred")
    if deferred is not None:
        if not isinstance(deferred, list):
            issues.append("deferred 须为字符串数组（跨段待统稿事项）")
        else:
            for k, x in enumerate(deferred, 1):
                if not isinstance(x, str) or not x.strip():
                    issues.append(f"deferred[{k}] 须为非空字符串")

    # ⑥ 实际处理范围：形状/越界，以及「声称完整」时的空洞检查
    cov = pack.get("coverage") or {}
    pr = cov.get("processed_ranges")
    if not isinstance(pr, list) or not pr:
        issues.append("处理范围缺失（coverage.processed_ranges），无法确认实际读过哪些行")
    else:
        valid = []
        for k, rng in enumerate(pr, 1):
            if (not isinstance(rng, (list, tuple)) or len(rng) != 2
                    or not all(isinstance(x, int) for x in rng)
                    or rng[0] < 1 or rng[1] < rng[0]):
                issues.append(f"处理范围 processed_ranges[{k}] 须为 [起,止] 正整数区间")
                continue
            if rng[1] > n_lines:
                issues.append(f"处理范围越界：processed_ranges[{k}] "
                              f"[{rng[0]},{rng[1]}]（源共 {n_lines} 行）")
                continue
            valid.append((rng[0], rng[1]))
        if cov.get("complete") and valid:
            cur, holes = 0, []
            for a, b in sorted(valid):
                if a > cur + 1:
                    holes.append([cur + 1, a - 1])
                cur = max(cur, b)
            if cur < n_lines:
                holes.append([cur + 1, n_lines])
            if holes:
                issues.append(f"声称完整覆盖但存在空洞：{holes[:3]}"
                              "（complete=True 必须真的覆盖全文，否则如实写 False）")

    # ⑦ 如实声明
    if pack.get("material_available") and not pack.get("material_key"):
        issues.append("material_available=True 但 material_key 为空（不得伪造结构可回查）")

    # ⑧ 块序完整性：多块 pack 的块序号必须是 1..chunk_count 且不重复/不缺
    issues += chunk_set_issues(pack)[1]

    return {"ok": not issues, "issues": issues}


def chunk_set_issues(pack: dict) -> tuple:
    """多块 pack 的块序检查。返回 (声明块总数, issues)。

    「全块完整回收」的第二道机械门：只数回包份数不够——派单 manifest 被截断时
    份数依然凑得上，但块序号会缺。消费侧（`coverage_issues`）与本函数共用同一口径。
    """
    cov = (pack or {}).get("coverage") or {}
    try:
        total = int(cov.get("chunk_count") or 1)
    except (TypeError, ValueError):
        total = 1
    if total <= 1:
        return total, []
    indices = sorted(int((c or {}).get("index") or 0) for c in (pack or {}).get("chunks") or [])
    issues = []
    if indices != list(range(1, total + 1)):
        issues.append(f"块序不完整：实际块 {indices}，声明 {total} 块"
                      "（缺块或重复不能当完整回收）")
    declared = cov.get("chunk_total_declared")
    if isinstance(declared, list) and len(declared) > 1:
        issues.append(f"块总数自报不一致：{declared}")
    return total, issues


def coverage_issues(pack: dict) -> list:
    """整篇是否可进入**统稿材料**（消费侧硬门槛）。

    与 `validate_pack` 分工：validate 只判「pack 自身是否自洽」，`complete=False` 是
    如实声明，可以过；但**不完整覆盖的材料不能当统稿底座**——缺块时写作端会以为
    已经覆盖全文，把遗漏藏起来。返回空列表 = 可作为统稿材料。
    """
    cov = (pack or {}).get("coverage") or {}
    issues = []
    if cov.get("complete") is not True:
        issues.append("coverage.complete 非 True（分块未完整回收或部分读取），"
                      "不能当全篇统稿材料")
    total, chunk_issues = chunk_set_issues(pack)
    issues += chunk_issues
    ranges = cov.get("processed_ranges")
    if not isinstance(ranges, list) or not ranges:
        issues.append("处理范围缺失，无法确认实际读过哪些行")
    return issues


def merge_chunk_packs(*, title: str, url: str, source_text: str,
                      distill_prompt_sha256: str, parts: list,
                      notes: str = "") -> dict:
    """把同一篇源的分块提炼回包合并成**一个整篇 pack**（>40k 超长源，S9 第 8 条③）。

    `parts`: [{"chunk": {"index","total","start_line","end_line"}, "parsed": {...}}]，按块序给。

    合并口径（如实，不臆造）：
    - 条目 id 加块前缀（`i1` → `c2_i1`）避免跨块撞号；**`source_lines` 不动**——提炼者收到的是
      `prompts.distill.numbered_source` 带全局行号前缀的文本，报的就是全局行号。
    - `relations` 的 from/to 同步加块前缀；**跨块关系提炼者看不到，不臆造**，缺失事实写进 notes。
    - 相邻块重叠区会产生重复条目：按（type, stance, 去空白 content, readings）去重，
      保留块序靠前那条。**去重键不得只看 content**——同一句在相邻块里可能挂着不同口径的
      读数，只看 content 会把第二块的读数静默丢掉（N3：合并不得丢独有事实与相反读数）。
    - **块序**：`coverage.chunk_count` 记实际合并数，`coverage.chunk_indices` 记块序号，
      `coverage.chunk_total_declared` 记各块自报的总块数；三者不一致由调用方拒收
      （全块完整回收才允许形成可统稿材料）。
    - `mainline` 按块顺序拼接（question 取首块，progression/conclusions 逐块追加），
      并在 notes 注明「主线为分块拼接」——这与整篇提炼得到的主线不等价，如实标注。
    - `distill_prompt_sha256` 记**整篇**契约 hash（`prompts.distill.prompt_hash`），
      各块自身 hash 另记 `chunk_prompt_sha256s`，供版本绑定逐块对账。
    """
    from prompts.provenance import text_hash
    parts = [p for p in (parts or []) if isinstance(p, dict)]
    order = sorted(parts, key=lambda p: int((p.get("chunk") or {}).get("index") or 0))

    question, prog, concl, notes_out = "", [], [], []
    root_children, items, relations, seen = [], [], [], set()
    chunk_meta, dup_count = [], 0
    chunk_totals, deferred, deferred_seen, ranges = set(), [], set(), []
    for p in order:
        chunk = p.get("chunk") or {}
        parsed = p.get("parsed") or {}
        tag = f"c{int(chunk.get('index') or 0)}"
        ml = parsed.get("mainline") or {}
        if not question and ml.get("question"):
            question = str(ml.get("question") or "")
        prog += [str(x) for x in (ml.get("progression") or [])]
        concl += [str(x) for x in (ml.get("conclusions") or [])]
        # v6：树按块拼接，节点 id 加块前缀防跨块撞号（与条目 id 同一口径）
        hier = _norm_hierarchy(parsed.get("hierarchy"), source_text)
        for node in hier.get("children") or []:
            root_children.append(_prefix_node_ids(node, tag))
        for x in _norm_deferred(parsed.get("deferred")):
            if x not in deferred_seen:
                deferred_seen.add(x)
                deferred.append(x)
        if chunk.get("start_line") and chunk.get("end_line"):
            # 实际处理范围用**核心区间**：下发范围含重叠上下文，重复计入会把「读过的行」虚增
            ranges.append((int(chunk.get("core_start_line") or chunk["start_line"]),
                           int(chunk.get("core_end_line") or chunk["end_line"])))

        if chunk.get("total"):
            chunk_totals.add(int(chunk.get("total")))
        local_ids = set()
        n_before = len(items)
        for it in _source_items(parsed, source_text):
            cid = str(it.get("id") or "")
            new_id = f"{tag}_{cid}" if cid else ""
            # v6：条目挂接的节点 id 同步加块前缀
            new_nid = f"{tag}_{it.get('node_id')}" if it.get("node_id") else ""
            readings = _norm_readings(it.get("readings"))
            # 去重键必须带 type/stance/readings：只比 content 会把「同一句、不同读数口径」
            # 的第二块条目当成重复丢掉，正好丢掉 N3 要保住的相反读数与独有条件。
            key = (str(it.get("type") or ""), str(it.get("stance") or ""),
                   _norm(str(it.get("content") or "")),
                   tuple(tuple(str(r.get(f) or "") for f in READING_FIELDS)
                         for r in readings),
                   json.dumps(it.get("source_spans"), ensure_ascii=False),
                   str(it.get("label") or ""), str(it.get("notes") or ""))
            if key[2] and key in seen:
                dup_count += 1
                continue
            if key[2]:
                seen.add(key)
            local_ids.add(cid)
            items.append({
                "id": new_id,
                "type": str(it.get("type") or ""),
                "stance": str(it.get("stance") or ""),
                "content": str(it.get("content") or ""),
                "source_lines": list(it.get("source_lines") or []),
                "source_spans": it.get("source_spans"),
                "label": it.get("label") or "",
                "node_id": new_nid,
                "tier": str(it.get("tier") or ""),
                "notes": it.get("notes") or "",
                **({"readings": readings} if readings else {}),
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
                           "start_line": chunk.get("start_line"),
                           "end_line": chunk.get("end_line"),
                           "core_start_line": chunk.get("core_start_line"),
                           "core_end_line": chunk.get("core_end_line"),
                           "context_lines": chunk.get("context_lines", 0),
                           "continued": bool(chunk.get("continued")),
                           "continues": bool(chunk.get("continues")),
                           "items": len(items) - n_before})

    if len(order) > 1:
        notes_out.append(f"分块提炼：{len(order)} 块合并（主线为逐块拼接，与整篇提炼不等价）")
        notes_out.append("跨块关系未生成：提炼者一次只见一块，不做跨块臆测；"
                         "跨块指代/条件见 deferred，由全篇统稿处理")
    if dup_count:
        notes_out.append(f"重叠区去重 {dup_count} 条重复条目（保留块序靠前那条）")
    if deferred:
        notes_out.append(f"跨段交接 {len(deferred)} 条（deferred）")
    if notes:
        notes_out.append(notes)

    n_lines = _line_count(source_text)
    union = _range_union(ranges)
    # `_range_union` 会把重叠/相邻区间并成一段：合并后仍是单段且首尾贴边 = 真覆盖全文
    complete = bool(union) and len(union) == 1 and union[0][0] == 1 and union[0][1] == n_lines
    if not ranges:
        notes_out.append("未记录分块行区间，无法声明完整覆盖")
    declared = sorted(chunk_totals)
    if len(declared) > 1:
        notes_out.append(f"块总数自报不一致：{declared}（不能当完整回收）")

    return {
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
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
        "hierarchy": {"intent": "", "children": root_children},
        "items": items,
        "relations": relations,
        "deferred": deferred,
        "coverage": {
            "source_lines_total": n_lines,
            "processed_ranges": union,
            "complete": complete,
            "item_count": len(items),
            "relation_count": len(relations),
            "chunk_count": len(chunk_meta),
            "chunk_indices": [c["index"] for c in chunk_meta],
            "chunk_total_declared": declared,
        },
        "notes": "；".join(notes_out),
    }


PACK_STYLES = ("json", "compact", "lines")


def dedupe_pack_items(pack: dict) -> tuple:
    """合并**完全同且同位置**的重复条目，返回 (pack, 去掉条数)。

    §8.5 第 2 条「先剔除重复」。判据收紧到 (type, stance, content, source_lines) 四元组全等——
    只去掉真正同一条信息，不同 `source_lines`/不同类型即使内容同也不动
    （「独有信息不得为省输入被静默删掉」）。指向被合并条目的关系重映射到保留的那条，
    去重数写进 `notes`，未得消灭痕迹。多块合并时此项幂等。
    """
    p = dict(pack or {})
    items = list(p.get("items") or [])
    by_key: dict = {}
    keep: list = []
    alias: dict = {}
    for it in items:
        key = (str(it.get("type") or ""), str(it.get("stance") or ""),
               str(it.get("content") or ""), tuple(it.get("source_lines") or []),
               tuple(tuple(str(r.get(f) or "") for f in READING_FIELDS)
                     for r in (it.get("readings") or [])),
               json.dumps(it.get("source_spans"), ensure_ascii=False),
               str(it.get("label") or ""), str(it.get("notes") or ""))
        winner = by_key.get(key)
        if winner is None:
            by_key[key] = it.get("id") or ""
            keep.append(it)
        else:
            alias[str(it.get("id") or "")] = winner
    removed = len(items) - len(keep)
    if not removed:
        return p, 0
    rels = []
    for r in (p.get("relations") or []):
        r = dict(r)
        for side in ("from", "to"):
            ref = str(r.get(side) or "")
            if ref in alias:
                r[side] = alias[ref]
        if r.get("from") and r.get("to") and r["from"] != r["to"]:
            rels.append(r)
    p["items"] = keep
    p["relations"] = rels
    p["coverage"] = {**(p.get("coverage") or {}),
                     "item_count": len(keep), "relation_count": len(rels)}
    note = f"条目去重 {removed} 条（四元组全等，含归因与行号一致的重复）"
    p["notes"] = ("；".join(x for x in [p.get("notes") or "", note] if x))
    return p, removed


def _escape_field(s: str, delim: str) -> str:
    return str(s or "").replace("\\", "\\\\").replace(delim, "\\" + delim).replace("\n", "\\n")


def _unescape_field(s: str, delim: str) -> str:
    """配合 `_escape_field` 还原（`\\<分隔符>` → 分隔符，`\\n` → 换行）。"""
    out, i, n = [], 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt == delim or nxt == "\\":
                out.append(nxt)
                i += 2
                continue
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def serialize_pack(pack: dict, style: str = "json", dedupe: bool = False) -> str:
    """把 pack 序列化成**写进写作 prompt 的正文**（不含包块标记与代码围栏）。

    `style`（§8.5 第 2 条的三种候选，压缩比逐步提高，**字段一律不删**）：
    - `json`：现役形态（`indent=1`），与 S8 distill 臂逐字节同款，作为体积基准不得改动；
    - `compact`：`separators=(",",":")` 的紧凑 JSON（只去缩进空格）；
    - `lines`：行式紧凑文本（去重复键名），实现见 `serialize_pack_lines`。

    `dedupe=True` 时先过一遍 `dedupe_pack_items`。派单侧铁的规矩：
    **只删语法噪音，不删内容**——条件、反例、数字归属、关系、独有信息一个都不能丢。
    """
    p, _n = dedupe_pack_items(pack) if dedupe else (pack, 0)
    if style == "json":
        return json.dumps(p, ensure_ascii=False, indent=1)
    if style == "compact":
        return json.dumps(p, ensure_ascii=False, separators=(",", ":"))
    if style == "lines":
        return serialize_pack_lines(p)
    raise ValueError(f"未知 pack style：{style!r}（可选 {PACK_STYLES}）")


def _pick_line_delim(texts: "list[str]") -> str:
    """挑一个**不在任何条目正文里出现**的分隔符，避免压缩时把正文切坏。"""
    for cand in ("‖", "❙", "␟", "\u0001"):
        if not any(cand in t for t in texts):
            return cand
    return "\u0001"


def serialize_pack_lines(pack: dict) -> str:
    """行式紧凑序列化：保留全部语义字段，只砍掉重复的键名与缩进。

    形态（`@` 开头为行类型，便于肉眼校对与事后 grep 定位某一类信息）：
        @meta  版本绑定要读的字段（同 `pack_meta_json`，另有一张紧凑 JSON 在代码围栏里）
        @m/@p/@c  主线 question / progression / conclusions
        @i        id<分隔符>type<分隔符>stance<分隔符>起-止行<分隔符>content
        @q        id<分隔符>value<分隔符>object<分隔符>unit<分隔符>period<分隔符>basis<分隔符>certainty
                  （该条目的结构化读数；v4「数字不混口径」的载体，不能压掉）
        @r        from<分隔符>to<分隔符>kind<分隔符>note
        @f        跨段待统稿事项（deferred）
        @n        notes
    分隔符由 `_pick_line_delim` 动态挑一个正文里不存在的字符，杜绝「压缩把正文切坏」。
    """
    ml = pack.get("mainline") or {}
    hier = pack.get("hierarchy") or {}
    items = list(pack.get("items") or [])
    readings_values = [str(r.get(f) or "") for it in items
                       for r in (it.get("readings") or [])
                       for f in READING_FIELDS]
    delim = _pick_line_delim([str(it.get("content") or "") for it in items]
                             + [str(r.get("note") or "") for r in (pack.get("relations") or [])]
                             + readings_values
                             + [str(x) for x in (pack.get("deferred") or [])])
    out = []
    # 版本字段已在代码围栏的 meta JSON 里，不重复写成 @meta 文本（白白占固定开销）
    cov = pack.get("coverage") or {}
    out.append("@meta contract=v"
               f"{pack.get('contract_version') or CONTRACT_VERSION} "
               f"complete={1 if cov.get('complete') is True else 0} "
               f"coverage={cov.get('source_lines_total')}行/{cov.get('item_count')}条目/"
               f"{cov.get('relation_count')}关系"
               + (f"/{cov.get('chunk_count')}块" if cov.get("chunk_count", 1) != 1 else ""))
    if str(ml.get("question") or "").strip():
        out.append("@m " + str(ml.get("question") or "").replace("\n", " "))
    for x in ml.get("progression") or []:
        out.append("@p " + str(x).replace("\n", " "))
    for x in ml.get("conclusions") or []:
        out.append("@c " + str(x).replace("\n", " "))
    if pack.get("no_substance"):
        out.append("@ns " + str(pack.get("no_substance_reason") or "").replace("\n", " "))
    if hier:
        # v6 树：根 intent + 先序节点行（JSON 单行），parent 为空 = 根
        out.append("@h " + json.dumps({"id": "", "parent": None,
                                       "intent": hier.get("intent") or ""},
                                      ensure_ascii=False, separators=(",", ":")))
        stack = [(n, "") for n in hier.get("children") or []]
        while stack:
            node, parent = stack.pop(0)
            out.append("@h " + json.dumps(
                {"id": node.get("id") or "", "parent": parent,
                 "label": node.get("label") or "", "intent": node.get("intent") or "",
                 "source_spans": node.get("source_spans") or []},
                ensure_ascii=False, separators=(",", ":")))
            stack += [(c, node.get("id") or "") for c in node.get("children") or []]
    for it in items:
        sl = it.get("source_lines") or [0, 0]
        out.append("@i " + delim.join([
            str(it.get("id") or ""), str(it.get("type") or ""),
            str(it.get("stance") or ""),
            f"{sl[0]}-{sl[1]}" if len(sl) == 2 else str(sl),
            _escape_field(it.get("content"), delim)]))
        if "source_spans" in it or it.get("node_id") or it.get("tier"):
            out.append("@x " + json.dumps({f: it.get(f) for f in
                                          ("id", "source_spans", "label", "notes",
                                           "node_id", "tier")},
                                         ensure_ascii=False, separators=(",", ":")))
        for r in it.get("readings") or []:
            out.append("@q " + delim.join(
                [str(it.get("id") or "")] +
                [_escape_field(r.get(f), delim) for f in READING_FIELDS]))
    for r in pack.get("relations") or []:
        out.append("@r " + delim.join([str(r.get("from") or ""), str(r.get("to") or ""),
                                       str(r.get("kind") or ""),
                                       _escape_field(r.get("note"), delim)]))
    for x in pack.get("deferred") or []:
        out.append("@f " + str(x).replace("\n", " "))
    if str(pack.get("notes") or "").strip():
        out.append("@n " + str(pack.get("notes") or "").replace("\n", " "))
    return "\n".join(out)


def parse_pack_lines(text: str) -> dict:
    """把 `serialize_pack_lines` 的输出解析回 pack 片段（**只用于校验**，生产消费它）。

    存在的意义：DS lab 压到一半体积不算本事，压缩要能原样还原——解析后逐条比对
    id/type/stance/content/source_lines/关系/主线，少一条都算内容丢失。
    """
    out = {"mainline": {"question": "", "progression": [], "conclusions": []},
           "items": [], "relations": [], "meta": {}, "deferred": [], "notes": "",
           "hierarchy": {"intent": "", "children": []}}
    raw = (text or "").split("\n")
    delim = ""
    node_index: dict = {}
    for ln in raw:
        if ln.startswith("@h "):
            try:
                node = json.loads(ln[3:])
            except Exception:
                continue
            if node.get("parent") is None:
                out["hierarchy"]["intent"] = str(node.get("intent") or "")
                node_index[""] = out["hierarchy"]
            else:
                parent = node_index.get(str(node.get("parent") or ""))
                if parent is None:
                    continue
                child = {"id": str(node.get("id") or ""),
                         "label": str(node.get("label") or ""),
                         "intent": str(node.get("intent") or ""),
                         "source_spans": node.get("source_spans") or [],
                         "children": []}
                parent.setdefault("children", []).append(child)
                node_index[child["id"]] = child
            continue
        if ln.startswith("@ns "):
            out["no_substance"] = True
            out["no_substance_reason"] = ln[4:]
            continue
        if ln.startswith("@x "):
            extra = json.loads(ln[3:])
            item = next((it for it in out["items"] if it["id"] == extra.get("id")), None)
            if item is not None:
                item.update({f: extra[f] for f in ("source_spans", "label", "notes",
                                                   "node_id", "tier") if f in extra})
            continue
        if ln.startswith("@meta "):
            body = ln[len("@meta "):]
            for piece in body.split(" "):
                if "=" in piece:
                    k, v = piece.split("=", 1)
                    out["meta"][k.strip()] = v.strip()
            continue
        if ln.startswith(("@i ", "@r ", "@q ")):
            # 分隔符 = 行类型标记后的第一个字符（§：动态挑选，见 `_pick_line_delim`）
            if not delim:
                rest = ln[3:]
                need = {"i": 4, "r": 3, "q": 6}[ln[1]]
                for cand in ("‖", "❙", "␟", "\u0001"):
                    if rest.count(cand) >= need:
                        delim = cand
                        break
            if not delim:
                continue
            parts = _split_escaped(ln[3:], delim)
            if ln.startswith("@i "):
                if len(parts) < 5:
                    continue
                iid, typ, stance, span, content = parts[0], parts[1], parts[2], parts[3], parts[4]
                nums = [int(x) for x in span.split("-") if x.isdigit()]
                out["items"].append({"id": iid, "type": typ, "stance": stance,
                                     "content": content,
                                     "source_lines": nums if len(nums) == 2 else []})
            elif ln.startswith("@q "):
                if len(parts) < 1 + len(READING_FIELDS):
                    continue
                item = next((it for it in reversed(out["items"]) if it.get("id") == parts[0]),
                            None)
                if item is None:
                    continue
                item.setdefault("readings", []).append(
                    {f: parts[1 + k] for k, f in enumerate(READING_FIELDS)})
            else:
                if len(parts) < 4:
                    continue
                out["relations"].append({"from": parts[0], "to": parts[1],
                                         "kind": parts[2], "note": parts[3]})
            continue
        if ln.startswith("@m "):
            out["mainline"]["question"] = ln[3:]
        elif ln.startswith("@p "):
            out["mainline"]["progression"].append(ln[3:])
        elif ln.startswith("@c "):
            out["mainline"]["conclusions"].append(ln[3:])
        elif ln.startswith("@f "):
            out["deferred"].append(ln[3:])
        elif ln.startswith("@n "):
            out["notes"] = ln[3:]
    return out


def _split_escaped(s: str, delim: str) -> list:
    """按未被转义的分隔符切分（`\\<分隔符>` 视为正文里的普通字符）。"""
    parts, cur, i, n = [], [], 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n and s[i + 1] in (delim, "\\"):
            cur.append(s[i + 1])
            i += 2
            continue
        if ch == "\\" and i + 1 < n and s[i + 1] == "n":
            cur.append("\n")
            i += 2
            continue
        if ch == delim:
            parts.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return parts


def lines_roundtrip_issues(pack: dict) -> list:
    """行式序列化的**内容保真校验**：还原后与原 pack 逐条比对，返回差异清单（空=无损）。"""
    issues: list = []
    got = parse_pack_lines(serialize_pack_lines(pack))
    src_items = list(pack.get("items") or [])
    if len(got["items"]) != len(src_items):
        issues.append(f"条目数不一致：原 {len(src_items)} vs 还原 {len(got['items'])}")
    for a, b in zip(src_items, got["items"]):
        for f in ("source_spans", "label", "notes"):
            if a.get(f) != b.get(f):
                issues.append(f"{a.get('id')}: {f} 不一致（原文素材定位或注解丢失）")
        for f in ("id", "type", "stance", "content"):
            if str(a.get(f) or "") != str(b.get(f) or ""):
                issues.append(f"{a.get('id')}: {f} 不一致（{str(a.get(f))[:30]!r} → "
                              f"{str(b.get(f))[:30]!r}）")
        for f in ("node_id", "tier"):
            if str(a.get(f) or "") != str(b.get(f) or ""):
                issues.append(f"{a.get('id')}: {f} 不一致（v6 挂接/分档不得在压缩中丢失）")
        sa = [int(x) for x in (a.get("source_lines") or []) if str(x).isdigit()]
        if sa != list(b.get("source_lines") or []):
            issues.append(f"{a.get('id')}: source_lines 不一致 {sa} → {b.get('source_lines')}")
        ra = [{f: str(r.get(f) or "") for f in READING_FIELDS}
              for r in (a.get("readings") or [])]
        rb = [{f: str(r.get(f) or "") for f in READING_FIELDS}
              for r in (b.get("readings") or [])]
        if ra != rb:
            issues.append(f"{a.get('id')}: readings 不一致（{len(ra)} 条 → {len(rb)} 条）")
    src_rels = list(pack.get("relations") or [])
    if len(got["relations"]) != len(src_rels):
        issues.append(f"关系数不一致：原 {len(src_rels)} vs 还原 {len(got['relations'])}")
    for a, b in zip(src_rels, got["relations"]):
        for f in ("from", "to", "kind", "note"):
            if str(a.get(f) or "") != str(b.get(f) or ""):
                issues.append(f"关系 {a.get('from')}->{a.get('to')}: {f} 不一致")
    ml = pack.get("mainline") or {}
    gm = got["mainline"]
    if str(ml.get("question") or "") != gm["question"]:
        issues.append("主线 question 不一致")
    if [str(x) for x in (ml.get("progression") or [])] != gm["progression"]:
        issues.append("主线 progression 不一致")
    if [str(x) for x in (ml.get("conclusions") or [])] != gm["conclusions"]:
        issues.append("主线 conclusions 不一致")
    # v6 树：先序展开逐节点比对（id/label/intent/spans/父子关系）
    def _flatten(hier):
        flat = []
        stack = [(n, "") for n in (hier or {}).get("children") or []]
        while stack:
            node, parent = stack.pop(0)
            flat.append((node.get("id") or "", parent,
                         node.get("label") or "", node.get("intent") or "",
                         json.dumps(node.get("source_spans") or [], ensure_ascii=False)))
            stack = [(c, node.get("id") or "") for c in node.get("children") or []] + stack
        return flat
    if _flatten(pack.get("hierarchy")) != _flatten(got.get("hierarchy")):
        issues.append("hierarchy 不一致（意图树不得在压缩中丢失或变形）")
    if bool(pack.get("no_substance")) != bool(got.get("no_substance")):
        issues.append("no_substance 标记不一致")
    if str(pack.get("no_substance_reason") or "") != str(got.get("no_substance_reason") or ""):
        issues.append("no_substance_reason 不一致")
    if [str(x) for x in (pack.get("deferred") or [])] != got["deferred"]:
        issues.append("deferred 不一致（跨段交接事项不得在压缩中丢失）")
    return issues


def pack_meta_json(pack: dict) -> dict:
    """版本绑定需要读的字段单独给一张小 JSON——换 serialization 后仍能回读。

    `coverage_complete`/`chunk_count` 是装配侧判定「这块材料能不能当统稿底座」要用的
    字段：行式块只有这张 meta 能被机械解析，整包 coverage 不在里面。
    """
    cov = (pack or {}).get("coverage") or {}
    return {
        "kind": "distill_pack",
        "schema_version": pack.get("schema_version"),
        "contract_version": pack.get("contract_version"),
        "url_key": pack.get("url_key") or "",
        "source_sha256": pack.get("source_sha256") or "",
        "distill_prompt_sha256": pack.get("distill_prompt_sha256") or "",
        "material_key": pack.get("material_key") or "",
        "material_available": bool(pack.get("material_available")),
        "coverage_complete": cov.get("complete") is True,
        "chunk_count": int(cov.get("chunk_count") or 1),
    }


def style_of_pack_block(block: str) -> str:
    """已装配包块用的消费方式（`lines`/`json`）——版本绑定与事后核对要先知道看的是哪种形态。"""
    return "lines" if ("\n@i " in (block or "") or "\n@meta " in (block or "")) else "json"


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
