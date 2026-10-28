# -*- coding: utf-8 -*-
"""shared/distill_dispatch.py — S9 有限接线：提炼两阶段的派发协议与装配。

PLAN-20261026-low-cost-quality §8.4（S9 入口）/ §9.11-S9（协议）。**默认不启用**：
`NOTE_DISTILL_DISPATCH` 关闭时生产路径字节不变，本模块只在显式调用时生效。

职责边界（协议四条，与 §8.4 第 1–4 条一一对应）：
1. **路由从严**：只对长源（`source_chars_of` ≥ `LONG_SOURCE_MIN_CHARS`）且非数字密集
   （`digit_density` < `DENSE_DIGIT_DENSITY`）且非高数字量（源内不同数字串
   < `NUMERIC_HEAVY_UNIQUE_NUMS`）的源走两阶段；其余一律 `single`。
   误判方向必须落在「保守回单轮」——S8 已证短源无增益、数值密集源 P2 丢失多，
   S9 真实抽样又补了一类：**密度不高但数字串绝对量很大**的直播复盘（美债 B 篇密度
   仅 0.0373，却有 191 个不同数字串）同样命中该弱项。
2. **装配不改主体**：`assemble_write_prompt` = 生产 prompt 主体（逐字节）+ 材料包块；
   包块标记与文案与 S8 distill 臂逐字节同款，`DISTILL_MARKER`（提炼契约）不进写作 prompt。
3. **版本绑定**：源 hash 或提炼契约（prompt）hash 失配、pack 机械校验不通过 → 不消费，
   该篇降级单轮，不阻塞批次（§9.6：换源/改稿后旧提炼不能当有效交接）。
4. **留痕**：路由、回退原因、源/prompt hash 全部可写 JSONL；`log_path` 为空则不写
   （测试与只读核对不得污染生产目录）。
"""
from __future__ import annotations

import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from prompts.distill import prompt_hash, strip_fences as _strip_fences  # noqa: E402
from prompts.distill import CONTRACT_VERSION  # noqa: E402
from prompts.templates import source_chars_of  # noqa: E402
from shared import distill_pack as DP  # noqa: E402
from shared.source_metrics import unique_number_count  # noqa: E402

MATERIAL_PACK_BEGIN = "<<<DISTILL_MATERIAL_PACK>>>"
MATERIAL_PACK_END = "<<<END_DISTILL_MATERIAL_PACK>>>"

# 路由阈值
LONG_SOURCE_MIN_CHARS = 10000
SHORT_SOURCE_MAX_CHARS = 5000
# ① 密度闸（S8 五源校准：D2 密度 0.174，其余四源 ≤0.027 → 0.08 取中间偏保守）
DENSE_DIGIT_DENSITY = 0.08
# ② 绝对量闸（S9 真实抽样后新增，2026-10-27）：密度按全文均摊，长直播稿的大段口语论述会
#    稀释密度——美债 B 篇密度仅 0.0373 却含 191 个不同数字串，仍复现了「数值密集」弱项。
#    两个已知点的空隙：放行侧最大 95（S8-D3），拦截侧最小 191（S9-B）→ 取 130（偏保守侧，
#    全库 120 与 150 两档实测结果相同，该区间无样本）。口径 = `shared.source_metrics`
#    （去标题行与 `> 标题/时间` 元信息块后的不同数字串个数）。
NUMERIC_HEAVY_UNIQUE_NUMS = 130

# ④ 超长源分块阈值（S9 抽样 §5.1：B 篇 52.8k 字产出 105.6KB pack / 79,496 字符写作 prompt，
#    「单次任务能承载多大」没有边界结论 → >40k 字先分块提炼，再谈接线）。
CHUNK_MAX_CHARS = 40000
# 相邻块重叠行数：给跨段指代/条件留上下文（§9.3.5：分段不许切断指代、条件和跨段关系）。
# 重叠只进**下发范围**，不进核心区间——核心重复等于同一段被提炼两次。
CHUNK_OVERLAP_LINES = 40

# 句子结束符：无结构文本（字幕/口播）按句子边界切，容量兜底。
# 字幕换行**不算**完整语义边界，所以不按行切，按句群切。
_SENTENCE_END_RE = re.compile(r"[。！？；!?;…]\s*$")

PACK_BLOCK_HEADER = (
    "以下是**同版源**的独立提炼材料包（先行提炼步骤的产物），供取材与对账使用：\n"
    "- 材料包不是大纲，而是**段级论证单元**：正文仍按上面模板组织，不得按条目顺序扩写；\n"
    "- 材料包不是标准答案：它与源冲突时以源文本为准；条目外的源信息仍须覆盖；\n"
    "- 标为 quote 的内容是逐字原话，可直接引用；其余为摘意，用你的话重组时保持口径；\n"
    "- 多读数条目的 `readings` 已按对象/单位/期间/基准分列，引用时不得混口径或合并；\n"
    "- `deferred` 是提炼者留给全篇统稿的跨段事项，写作前先据此核对跨块指代与条件。"
)

_DIGIT_RE = re.compile(r"[0-9]")


class PrepareIneligible(Exception):
    """该篇不走两阶段（短源/数字密集/无有效 pack/源不可读）——降级单轮，不算失败。

    `stripped`：本次判定是否**真的改了条目**（剥掉过旧材料包块）。True 表示调用方
    必须把队列写回——否则失效包块会留在条目里被下一轮继续消费（§8.5 第 1 条，2026-10-27
    实抓：旧实现只在走到装配分支时才剥包，回退路径一律「原样返回」）。
    `log_status`：落 JSONL 的状态名，便于按回退原因分组复盘。
    """

    def __init__(self, reason: str, decision: dict | None = None,
                 stripped: bool = False, log_status: str = "ineligible"):
        super().__init__(reason)
        self.reason = reason
        self.decision = decision or {}
        self.stripped = stripped
        self.log_status = log_status


def _text_hash(text: str) -> str:
    from prompts.provenance import text_hash
    return text_hash(text)


# ------------------------------------------------------------------ 路由

def route_for_source(source_text: str) -> dict:
    """是否走「提炼 → 按 pack 写作」两阶段。返回 {"route","reason","signals"}。

    保守方向：任何判不准的情形都回 `single`；`reason` 便于在报告里分组解释。
    """
    text = source_text or ""
    chars = source_chars_of(text)
    density = (len(_DIGIT_RE.findall(text)) / chars) if chars else 0.0
    uniq = unique_number_count(text)
    signals = {"chars": int(chars), "digit_density": round(density, 4),
               "unique_numbers": int(uniq)}
    if chars < LONG_SOURCE_MIN_CHARS:
        reason = "short_source" if chars < SHORT_SOURCE_MAX_CHARS else "below_long_threshold"
        return {"route": "single", "reason": reason, "signals": signals}
    if density >= DENSE_DIGIT_DENSITY:
        # S8 弱项：数值密集表格源 P2 丢失增多 → 保持单轮，不做条目粒度改造前不放开
        return {"route": "single", "reason": "numeric_dense", "signals": signals}
    if uniq >= NUMERIC_HEAVY_UNIQUE_NUMS:
        # S9 补录的同一弱项另一张脸：密度低但数字串绝对量大（直播复盘/行情串讲）
        return {"route": "single", "reason": "numeric_heavy", "signals": signals}
    return {"route": "distill", "reason": "long_source", "signals": signals}


# ------------------------------------------------------------------ 材料包块

def render_pack_block(pack: dict, style: str = "", dedupe: bool = False) -> str:
    """渲染材料包块。`style` 来自 `resolved_pack_style()`（`json` = 现役逐字节同款）。

    行式形态（lines）也要带上版本绑定字段：单独一张紧凑 JSON 放在代码围栏里，
    `block_json_field` 靠它读 `source_sha256` / `distill_prompt_sha256`——
    否则换序列化后 `[block_is_current]` 会全部误判为陈旧。
    """
    style = style or resolved_pack_style()
    if style == "lines":
        from shared.distill_pack import pack_meta_json
        head = "\n".join([
            "以下是**同版源**的独立提炼材料包（先行提炼步骤的产物），供取材与对账使用：",
            "- 材料包不是大纲，而是**段级论证单元**：正文仍按上面模板组织，不得按条目顺序扩写；",
            "- 材料包不是标准答案：它与源冲突时以源文本为准；条目外的源信息仍须覆盖；",
            "- 条目标 quote 的是逐字原话，可直接引用；其余为摘意，用你的话重组时保持口径；",
            "- 条目格式：`@i 编号‖类型‖quote/paraphrase‖起-止行‖内容`；`@q 编号‖读数‖对象‖单位‖期间‖基准‖确定性`"
            "为多口径读数（不得合并）；`@m/@p/@c` 为主线；"
            "`@r 源条目‖目标条目‖关系类型‖说明`；`@f` 为跨段待统稿事项；`@meta` 为版本字段。",
        ])
        meta = json.dumps(DP.pack_meta_json(pack), ensure_ascii=False, separators=(",", ":"))
        body = DP.serialize_pack(pack, style="lines", dedupe=dedupe)
        return (f"{MATERIAL_PACK_BEGIN}\n{head}\n```json\n{meta}\n```\n{body}\n"
                f"{MATERIAL_PACK_END}")
    body = DP.serialize_pack(pack, style=style, dedupe=dedupe)
    return (f"{MATERIAL_PACK_BEGIN}\n{PACK_BLOCK_HEADER}\n```json\n{body}\n```\n"
            f"{MATERIAL_PACK_END}")


def resolved_pack_style(override: str = "") -> str:
    """当前生效的材料包消费方式（`NOTE_DISTILL_PACK_STYLE`，默认 `json`）。

    默认是 `json` = **现役形态逐字节不变**；`compact`/`lines` 属 §8.5 第 2 条的待验证候选，
    只有在阵样对照里证明「内容不减、取材不下降」后才考虑改默认。
    """
    style = (override or os.environ.get("NOTE_DISTILL_PACK_STYLE") or "json").strip().lower()
    return style if style in DP.PACK_STYLES else "json"


def measure_assembly(base_prompt: str, pack: dict, style: str = "",
                     dedupe: bool = False) -> dict:
    """装配成本实测：返回字符数与相对 `json` 基准的压缩比（§8.5 第 2 条要求持续留痕）。

    只报**字符数**（不是 token）；token/耗时取不到就写 `unknown`——不拿估算充数。
    """
    style = style or resolved_pack_style()
    base_len = len(base_prompt or "")
    ref = len(render_pack_block(pack, style="json"))
    got = len(render_pack_block(pack, style=style, dedupe=dedupe))
    deduped, removed = DP.dedupe_pack_items(pack)
    cov = (pack or {}).get("coverage") or {}
    items = (pack or {}).get("items") or []
    return {"style": style, "dedupe": bool(dedupe),
            "schema_version": (pack or {}).get("schema_version"),
            "contract_version": (pack or {}).get("contract_version"),
            "base_chars": base_len, "pack_block_chars": got,
            "total_chars": base_len + got,
            "baseline_block_chars": ref,
            "savings_pct": round((1 - got / ref) * 100, 2) if ref else 0.0,
            "items": len(items),
            "arguments": sum(1 for it in items if it.get("type") == "argument"),
            "readings": sum(len(it.get("readings") or []) for it in items),
            "deferred": len((pack or {}).get("deferred") or []),
            "relations": len((pack or {}).get("relations") or []),
            "items_after_dedupe": len(deduped.get("items") or []),
            "duplicate_items": removed,
            "source_lines_total": cov.get("source_lines_total"),
            "processed_ranges": cov.get("processed_ranges") or [],
            "chunk_count": cov.get("chunk_count", 1),
            "token": "unknown", "latency": "unknown"}


def assemble_write_prompt(base_prompt: str, pack: dict, style: str = "",
                          dedupe: bool = False) -> str:
    """生产 prompt 主体逐字节保留，材料包块追加在后（与 S8 distill 臂同款布局）。"""
    return (base_prompt or "").rstrip("\n") + "\n\n" + render_pack_block(
        pack, style=style, dedupe=dedupe)


def split_write_prompt(prompt: str) -> tuple:
    """拆回 (主体, 包块或 None)。用于幂等判断与「主体不得被包块污染」的核对。"""
    text = prompt or ""
    i = text.find(MATERIAL_PACK_BEGIN)
    j = text.find(MATERIAL_PACK_END)
    if i < 0 or j < i:
        return text, None
    return text[:i].rstrip("\n"), text[i:j + len(MATERIAL_PACK_END)]


def strip_pack_blocks(prompt: str) -> tuple:
    """剥掉 prompt 里**全部**材料包块，返回 (干净主体, 剥掉个数)。

    `split_write_prompt` 只切第一刀：历史产物里出现过同一篇挂两个包（隔离试验脚本
    double-assemble 的遗留），只切一次会把第二个包留下。这里循环剥离，无论重复几次
    都归一到「无包主体」，装配时只装一个（§8.5 第 1 条第 3 点）。
    """
    text = prompt or ""
    count = 0
    while True:
        i = text.find(MATERIAL_PACK_BEGIN)
        j = text.find(MATERIAL_PACK_END)
        if i < 0 or j < i:
            return text.rstrip("\n"), count
        tail = text[j + len(MATERIAL_PACK_END):]
        head = text[:i].rstrip("\n")
        text = (head + "\n\n" + tail.lstrip("\n")) if tail.strip() else head
        count += 1


def block_json_field(block: str, field: str) -> str:
    """取已装配包块里的某个顶层字段（读不到返回空串——按陈旧处理）。"""
    if not block:
        return ""
    i = block.find("```json")
    j = block.rfind("```")
    if i < 0 or j <= i:
        return ""
    try:
        return str(json.loads(block[i + len("```json"):j].strip()).get(field) or "")
    except Exception:
        return ""


def block_source_sha(block: str) -> str:
    """取已装配包块里的 `source_sha256`（读不到返回空串——按陈旧处理）。"""
    return block_json_field(block, "source_sha256")


def block_is_current(block: str, source_text: str, title: str = "") -> tuple:
    """已装配的包块是否还算「当前」——**源与契约两版都要对得上**。

    第二轮轮的教训（2026-10-27）：只比 `source_sha256` 时，提炼契约升级（v1→v2）
    后旧包块会被当成「已装配」长期不换，等于把旧契约的已知缺陷钉在队列里。
    返回 (bool, reason)：reason ∈ {"ok", "source_sha256_mismatch", "contract_upgraded"}。
    `contract_upgraded` 覆盖两种情况：提炼契约 hash 变了，或包块 meta 里记的
    `contract_version` 不是当前版本（v4 起显式落盘，不再只靠 hash 间接判断）。
    """
    if not block:
        return False, "source_sha256_mismatch"
    if block_source_sha(block) != _text_hash(source_text or ""):
        return False, "source_sha256_mismatch"
    recorded_cv = block_json_field(block, "contract_version")
    if str(recorded_cv) != str(CONTRACT_VERSION):
        return False, "contract_upgraded"
    if block_json_field(block, "distill_prompt_sha256") != prompt_hash(title or "",
                                                                       source_text or ""):
        return False, "contract_upgraded"
    return True, "ok"


# ------------------------------------------------------------------ 版本绑定

def load_valid_pack_for_source(url: str, source_text: str, title: str = "",
                               store_dir: str = "") -> tuple:
    """按 URL 读 pack 并做同版/契约对账。返回 (pack|None, reason)。

    不通过就返回 None —— 调用方降级单轮，不消费、不修补、不拿旧 pack 硬套新源。
    """
    pack = DP.load_pack(url, store_dir) if url else None
    if not pack:
        return None, "no_pack"
    if str(pack.get("contract_version")) != str(CONTRACT_VERSION):
        # 显式版本门：旧契约的 pack 连结构都对不上本轮，直接不消费（理由可分组复盘）
        return None, "contract_version_mismatch"
    recorded = pack.get("source_sha256") or ""
    actual = _text_hash(source_text or "")
    if recorded != actual:
        return None, "source_sha256_mismatch"
    expected_task = prompt_hash(title or "", source_text or "")
    if (pack.get("distill_prompt_sha256") or "") != expected_task:
        return None, "distill_prompt_mismatch"
    report = DP.validate_pack(pack, source_text)
    if not report["ok"]:
        return None, "pack_invalid: " + "；".join(report["issues"][:3])
    return pack, "ok"


# ------------------------------------------------------------------ 留痕

def log_dispatch(record: dict, log_path: str = "") -> None:
    """追加一行 JSONL。`log_path` 为空即不写——测试/只读核对不落生产目录。"""
    if not log_path:
        return
    d = os.path.dirname(log_path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _prefix_sums(lines: list) -> list:
    """去空白字数前缀和：`pref[k]` = 第 1..k 行的去空白字数。"""
    pref = [0]
    for ln in lines:
        pref.append(pref[-1] + len("".join(ln.split())))
    return pref


def _merge_atomic(ranges: list) -> list:
    """合并/相邻的表格、代码块区间（同一 kind 才合并，避免把相邻两类混成一个单元）。"""
    out = []
    for a, b, kind in sorted(ranges):
        if out and a <= out[-1][1] + 1 and kind == out[-1][2]:
            out[-1] = (out[-1][0], max(out[-1][1], b), kind)
        else:
            out.append((a, b, kind))
    return out


def _structural_units(lines: list, st: dict) -> list:
    """把源切成**不可再切的结构单元**（表格/代码块/段落/标题块），返回升序行区间。

    边界来源与 `shared/source_material.scan_structure` 同一口径（单一真源）：
    - 表格与代码块是原子单元，内部不许下刀；
    - 段落边界、标题行都可作为单元边界（标题与它的正文分成两个单元，切点自然落在节首）。
    """
    n = len(lines)
    if n == 0:
        return []
    atomic = [(int(t["line_start"]), int(t["line_end"]), "table") for t in st["tables"]]
    atomic += [(int(c["line_start"]), int(c["line_end"]), "code") for c in st["code_blocks"]]
    atomic = _merge_atomic(atomic)

    boundaries = {1, n + 1}
    for a, b, _kind in atomic:
        boundaries.add(a)
        boundaries.add(b + 1)
    for p in st["paragraphs"]["bounds"]:
        boundaries.add(int(p[0]))
    heading_lines = {int(h["line"]) for h in st["headings"]}
    boundaries |= heading_lines
    # 原子块内部不得出现边界（否则表格/代码会被拦腰切开）
    for a, b, _kind in atomic:
        boundaries = {x for x in boundaries if not (a < x <= b)}

    bounds = sorted(x for x in boundaries if 1 <= x <= n + 1)
    units = []
    for i in range(len(bounds) - 1):
        s, e = bounds[i], bounds[i + 1] - 1
        if e < s:
            continue
        kind = "paragraph"
        for a, b, k in atomic:
            if s >= a and e <= b:
                kind = k
                break
        if kind == "paragraph" and s in heading_lines:
            kind = "heading"
        units.append({"start": s, "end": e, "kind": kind})
    return units


def _split_oversized(unit: dict, lines: list, pref: list, max_chars: int) -> list:
    """超过容量的单元切成若干片，并带上**续接元信息**（不许静默拦腰切）。

    - 表格/代码块：按行容量切，明确标 `{"unit","part","of"}`；
    - 散文/标题：优先切在句子结束行（句群），没有句子边界才按容量硬切（标 `hard`）。
    返回 [(unit, continuation|None)]。
    """
    total = pref[unit["end"]] - pref[unit["start"] - 1]
    if total <= max_chars:
        return [(unit, None)]

    kind = unit["kind"]
    parts = []          # (start, end, boundary_kind)
    if kind in ("table", "code"):
        s, acc = unit["start"], 0
        for j in range(unit["start"], unit["end"] + 1):
            w = pref[j] - pref[j - 1]
            if acc and acc + w > max_chars:
                parts.append((s, j - 1, kind))
                s, acc = j, 0
            acc += w
        parts.append((s, unit["end"], kind))
    else:
        s, acc, last_sent, j = unit["start"], 0, None, unit["start"]
        while j <= unit["end"]:
            acc += pref[j] - pref[j - 1]
            if _SENTENCE_END_RE.search(lines[j - 1]):
                last_sent = j
            if acc >= max_chars:
                if (last_sent is not None and last_sent >= s
                        and pref[last_sent] - pref[s - 1] >= max_chars * 0.4):
                    cut, cut_kind = last_sent, "sentence"
                else:
                    cut, cut_kind = j, "hard"
                parts.append((s, cut, cut_kind))
                s = cut + 1
                last_sent = None if (last_sent is None or last_sent <= cut) else last_sent
                acc = pref[j] - pref[s - 1] if s <= j else 0
            j += 1
        if s <= unit["end"]:
            parts.append((s, unit["end"], "sentence" if last_sent else "hard"))

    out, n_parts = [], len(parts)
    for k, (a, b, bkind) in enumerate(parts, 1):
        out.append(({"start": a, "end": b, "kind": bkind,
                     "chars": pref[b] - pref[a - 1]},
                    {"unit": kind, "part": k, "of": n_parts} if n_parts > 1 else None))
    return out


def _pack_units(flat: list, max_chars: int) -> list:
    """按容量把结构单元贪心装块；切点永远落在单元边界（续接片自带元信息）。"""
    chunks, cur = [], None
    for unit, cont in flat:
        if cur is None:
            cur = {"start": unit["start"], "end": unit["end"], "chars": unit["chars"],
                   "first_cont": cont, "last_cont": cont, "last_kind": unit["kind"]}
        elif cur["chars"] + unit["chars"] <= max_chars:
            cur["end"] = unit["end"]
            cur["chars"] += unit["chars"]
            cur["last_cont"] = cont
            cur["last_kind"] = unit["kind"]
        else:
            chunks.append(cur)
            cur = {"start": unit["start"], "end": unit["end"], "chars": unit["chars"],
                   "first_cont": cont, "last_cont": cont, "last_kind": unit["kind"]}
    if cur is not None:
        chunks.append(cur)
    return chunks


def plan_chunks(source_text: str, max_chars: int = CHUNK_MAX_CHARS,
                overlap_lines: int = CHUNK_OVERLAP_LINES) -> list:
    """把长源切成带重叠上下文的块（1-based 闭区间），返回 chunk 描述列表。

    **结构感知**（N2）：先按标题/段落/表格/代码块的边界切单元，再按容量装块；
    无结构文本（字幕/口播）按句子边界与容量兜底。全程纯机械，不调用模型分段。

    每块同时给出两套行区间：
    - `start_line`–`end_line`：**下发范围**（含前一块尾部 `context_lines` 行重叠上下文），
      字数为 `delivered_chars`（真实输入成本）；
    - `core_start_line`–`core_end_line`：**核心区间**（本块负责提炼的范围），
      字数为 `chars` / `core_chars`（切分容量口径）。

    invariants：核心区间首尾相接覆盖 1..总行数（无空洞、不重复）；重叠只出现在下发范围。
    超过容量的结构单元会被切分，并在块上标 `continued` / `continues` / `continuation`，
    派单文案据此说明「接续/尚未结束」，不静默拦腰切。
    """
    lines = (source_text or "").split("\n")
    if not lines or not (source_text or "").strip():
        return []
    pref = _prefix_sums(lines)
    n = len(lines)
    total_chars = pref[n]
    if total_chars <= max_chars:
        return [{"index": 1, "total": 1, "start_line": 1, "end_line": n,
                 "core_start_line": 1, "core_end_line": n,
                 "chars": total_chars, "core_chars": total_chars,
                 "delivered_chars": total_chars,
                 "context_lines": 0, "boundary": "paragraph",
                 "continued": False, "continues": False, "continuation": None}]

    from shared.source_material import scan_structure
    st = scan_structure(source_text)
    flat = []
    for unit in _structural_units(lines, st):
        unit["chars"] = pref[unit["end"]] - pref[unit["start"] - 1]
        flat.extend(_split_oversized(unit, lines, pref, max_chars))

    cores = _pack_units(flat, max_chars)
    total = len(cores)
    out = []
    for k, c in enumerate(cores, 1):
        cs, ce = c["start"], c["end"]
        ctx = min(overlap_lines, cs - 1) if k > 1 else 0
        first, last = c["first_cont"], c["last_cont"]
        out.append({
            "index": k, "total": total,
            "start_line": cs - ctx, "end_line": ce,
            "core_start_line": cs, "core_end_line": ce,
            "chars": pref[ce] - pref[cs - 1],
            "core_chars": pref[ce] - pref[cs - 1],
            "delivered_chars": pref[ce] - pref[cs - ctx - 1],
            "context_lines": ctx,
            "boundary": c["last_kind"],
            "continued": bool(first and first["part"] > 1),
            "continues": bool(last and last["part"] < last["of"]),
            "continuation": first or last,
        })
    return out


def chunk_source_text(source_text: str, start_line: int, end_line: int) -> str:
    """取该块的行片段（1-based 闭区间）。"""
    lines = (source_text or "").split("\n")
    return "\n".join(lines[max(0, start_line - 1):max(0, end_line)])


def render_chunk_prompt(title: str, source_text: str, chunk: dict) -> str:
    """按 chunk 区间渲染提炼任务。

    分块任务必须让提炼者分清三件事（N2）：**核心区间**是本块负责的范围；下发范围里
    前面的重叠行只是上下文，不得为其建条目；块边界落在结构单元中间时要显式说明接续，
    且不得替全篇下结论。行号仍用整篇的全局行号（`NNN| ` 前缀下发）。
    """
    from prompts.distill import render_distill_prompt
    total = int((chunk or {}).get("total", 1) or 1)
    if not chunk or total <= 1:
        n = len((source_text or "").split("\n"))
        return render_distill_prompt(
            title, source_text,
            core_start_line=int(chunk.get("core_start_line") or 1) if chunk else 1,
            core_end_line=int(chunk.get("core_end_line") or n) if chunk else n)

    start, end = int(chunk["start_line"]), int(chunk["end_line"])
    cs = int(chunk.get("core_start_line") or start)
    ce = int(chunk.get("core_end_line") or end)
    frag = chunk_source_text(source_text, start, end)
    label = (f"第 {chunk['index']}/{total} 块（源第 {start}–{end} 行；"
             f"核心第 {cs}–{ce} 行）")
    notes = []
    if cs > start:
        notes.append(f"第 {start}–{cs - 1} 行是上一块尾部的重叠上下文，"
                     "只用于消解指代与条件，不要重复提炼")
    if chunk.get("continued"):
        notes.append("本块从上一块的同一结构单元接续（表格/代码/长段的后半），"
                     "不要重复已提炼部分")
    if chunk.get("continues"):
        notes.append("本块末尾的结构单元尚未结束，续接块会处理余下部分，"
                     "不要替它下结论")
    return render_distill_prompt(title, frag, chunk_label=label, start_line=start,
                                 core_start_line=cs, core_end_line=ce,
                                 handoff="；".join(notes))


def chunk_scope_issues(chunk: dict, parsed: dict) -> list:
    """回包条目是否越出本块**核心区间**（越界=为重叠上下文重复建条目）。

    只报结构问题，不做语义判断；行号形状/越界仍由 `validate_pack` 兜底。
    """
    cs = int(chunk.get("core_start_line") or chunk.get("start_line") or 0)
    ce = int(chunk.get("core_end_line") or chunk.get("end_line") or 0)
    if not cs or not ce:
        return []
    issues = []
    for it in (parsed or {}).get("items") or []:
        sl = it.get("source_lines") or []
        if (not isinstance(sl, list) or len(sl) != 2
                or not all(isinstance(x, int) for x in sl)):
            continue      # 形状问题交给 validate_pack，不在这里重复报
        if sl[0] < cs or sl[1] > ce:
            issues.append(f"{it.get('id') or '条目'}: source_lines {sl} 越出本块核心区间 "
                          f"[{cs},{ce}]——重叠上下文只用于理解，不得为其建条目")
    return issues


def chunk_filename(url_key: str, chunk: dict) -> str:
    return f"{url_key}_distill.txt" if chunk.get("total", 1) == 1 else (
        f"{url_key}_c{chunk['index']}of{chunk['total']}_distill.txt")


def _read_source(entry: dict) -> "str | None":
    path = entry.get("raw_file") or entry.get("output")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeError):
        return None


# ------------------------------------------------------------------ 单条装配

def prepare_entry(entry: dict, store_dir: str = "", log_path: str = "",
                  source_text: "str | None" = None, dry_run: bool = False,
                  pack_style: str = "", dedupe_items: bool = False) -> dict:
    """给单条队列条目装配材料包块；不适用时抛 `PrepareIneligible`（见 `exc.stripped`）。

    **回退语义（§8.5 第 1 条，2026-10-27 修正）**：
    - **先判路由/源可读性，再决定旧包去留**。旧实现只在走到装配分支时才剥包，于是
      「曾经装配过、现在源变短 / 变数字密集 / 源不可读」的条目会带着旧包块**原样返回**，
      等于继续消费一个既不同源也不该走两阶段的材料包。
    - 不可装配的三种情形（源不可读 / 路由回单轮 / 无有效 pack）统一先把历史包块归一，
      该剥就剥并同步清 `distill_*` provenance；`exc.stripped=True` 表示条目已变，
      调用方必须写回队列（`dry_run` 只报告不写）。
    - 归一是保守方向：拿不到同版新 pack 就整篇降级单轮，绝不留下多个包块，
      也不留下「已剥包但 provenance 还指着旧 pack」的半同步状态。

    幂等：已装配**且仍是当前版本**（源 hash 与提炼契约 hash 都对得上）且**只有一个**
    包块 → `already_prepared`，不二次拼接；契约升级后旧包块判 `contract_upgraded` 换新；
    重复装配的历史产物（n>1 个包块）会被拆到重装为一个。
    """
    url = entry.get("url", "") or ""
    title = entry.get("title", "") or ""
    prompt = entry.get("prompt", "") or ""
    base_prompt, n_blocks = strip_pack_blocks(prompt)
    src = source_text if source_text is not None else _read_source(entry)
    decision = route_for_source(src or "")
    base_record = {"url": url, "title": title, "route": decision["route"],
                   "reason": decision["reason"], "signals": decision["signals"]}

    def _bail(reason: str, log_status: str) -> "PrepareIneligible":
        """该篇不装配：剥掉残留包块（留痕），并让调用方知道条目已改写。"""
        stripped = bool(n_blocks)
        if stripped and not dry_run:
            _apply_prompt(entry, base_prompt, drop_distill=True)
        log_dispatch({**base_record, "status": log_status, "fallback_reason": reason,
                      "stripped": stripped, "blocks_removed": n_blocks,
                      "source_sha256": _text_hash(src) if src is not None else ""},
                     log_path)
        return PrepareIneligible(reason, decision, stripped=stripped,
                                 log_status=log_status)

    # ① 源不可读：明确回退状态，绝不拿读不到的源继续沿用旧包
    if src is None:
        raise _bail("source_unavailable", "source_unavailable")
    # ② 路由判回单轮（短源/数字密集/数字总量大）：旧包一律剥掉
    if decision["route"] != "distill":
        raise _bail(decision["reason"], "route_fallback")

    block_reason = ""
    if n_blocks:
        _, block = split_write_prompt(prompt)
        current, why = block_is_current(block, src, title=title)
        if current and n_blocks == 1:
            log_dispatch({**base_record, "status": "already_prepared",
                          "source_sha256": _text_hash(src),
                          "write_prompt_sha256": _text_hash(prompt)}, log_path)
            return {"status": "already_prepared", "route": "distill", "entry": entry,
                    "decision": decision}
        # 已装配的包块属于旧源（换源/改稿）、旧提炼契约（升级），或本身就是重复装配
        block_reason = why if not current else f"duplicate_blocks:{n_blocks}"

    pack, reason = load_valid_pack_for_source(url, src, title=title, store_dir=store_dir)
    if pack is None:
        if n_blocks:
            # 旧包块失效且没有同版新包：剥掉回单轮（留痕；条目确实变了，调用方需写回）
            if not dry_run:
                _apply_prompt(entry, base_prompt, drop_distill=True)
            log_dispatch({**base_record, "status": "stale_stripped",
                          "fallback_reason": reason, "source_sha256": _text_hash(src),
                          "block_stale_reason": block_reason}, log_path)
            return {"status": "stale_stripped", "route": "distill", "entry": entry,
                    "decision": decision, "reason": reason}
        raise _bail(reason, "no_valid_pack")

    cost = measure_assembly(base_prompt, pack, style=pack_style, dedupe=dedupe_items)
    assembled = assemble_write_prompt(base_prompt, pack, style=pack_style,
                                      dedupe=dedupe_items)
    _apply_prompt(entry, assembled, updates={
        "distill_pack_key": pack.get("url_key") or "",
        "distill_pack_source_sha256": pack.get("source_sha256") or "",
        "distill_prompt_sha256": pack.get("distill_prompt_sha256") or "",
        "distill_pack_style": cost["style"],
    }, dry_run=dry_run)
    log_dispatch({**base_record, "status": "prepared" if not dry_run else "would_prepare",
                  "source_sha256": _text_hash(src),
                  "pack_key": pack.get("url_key") or "",
                  "blocks_removed": n_blocks,
                  "blocks_after": assembled.count(MATERIAL_PACK_BEGIN),  # 必须恒为 1
                  "cost": cost,
                  "write_prompt_sha256": _text_hash(assembled)}, log_path)
    return {"status": "prepared" if not dry_run else "would_prepare", "route": "distill",
            "entry": entry, "decision": decision, "pack": pack}


def _apply_prompt(entry: dict, new_prompt: str, updates: "dict | None" = None,
                  drop_distill: bool = False, dry_run: bool = False) -> None:
    """写回条目的 prompt 与 provenance（`dry_run` 不写）——剥包与装配共用一处，避免
    「剥了包却忘了清 `distill_*`」这种半同步 bug（§8.5 第 1 条）。"""
    if dry_run:
        return
    entry["prompt"] = new_prompt
    gc = dict(entry.get("generation_context") or {})
    gc["prompt_sha256"] = _text_hash(new_prompt)
    if drop_distill:
        for k in ("distill_pack_key", "distill_pack_source_sha256", "distill_prompt_sha256"):
            gc.pop(k, None)
    gc.update(updates or {})
    entry["generation_context"] = gc


def prepare_queue_entry(queue_path: str, url: str, store_dir: str = "", log_path: str = "",
                        source_text_override: "str | None" = None,
                        dry_run: bool = False) -> dict:
    """按 URL 定位队列条目并装配；写回队列（`dry_run` 不写）。"""
    entries = json.load(open(queue_path, encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("队列不是列表")
    idx = next((i for i, e in enumerate(entries) if e.get("url") == url), None)
    if idx is None:
        raise KeyError(f"队列里没有该 url：{url}")
    try:
        rep = prepare_entry(entries[idx], store_dir=store_dir, log_path=log_path,
                            source_text=source_text_override, dry_run=dry_run)
    except PrepareIneligible as exc:
        # 剥过包就得分条目已变写回，否则失效包块会留到下一轮被继续消费
        if exc.stripped and not dry_run:
            with open(queue_path, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False, indent=2)
        raise
    if rep["status"] in ("prepared", "stale_stripped") and not dry_run:
        with open(queue_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
    return rep


# ------------------------------------------------------------------ 批次编排

def plan_queue(queue_path: str, store_dir: str = "") -> dict:
    """只读：逐条路由判定 + pack 状态。不写队列、不写日志。"""
    entries = json.load(open(queue_path, encoding="utf-8"))
    rows = []
    for e in entries if isinstance(entries, list) else []:
        url = e.get("url", "") or ""
        src = _read_source(e)
        prompt = e.get("prompt", "") or ""
        _, n_blocks = strip_pack_blocks(prompt)
        if src is None:
            rows.append({"url": url, "title": e.get("title", ""), "route": "single",
                         "reason": "source_unavailable", "signals": {},
                         "pending_strip": n_blocks > 0,
                         "note_type": e.get("note_type", "")})
            continue
        d = route_for_source(src)
        row = {"url": url, "title": e.get("title", ""), "route": d["route"],
               "reason": d["reason"], "signals": d["signals"],
               "note_type": e.get("note_type", "")}
        if d["route"] != "distill":
            # 曾装配过但现在已不适用 → prepare 会剥掉这段（cleanup 前先在只读报告里露出来）
            row["pending_strip"] = n_blocks > 0
            rows.append(row)
            continue
        row["pending_strip"] = n_blocks > 1        # 重复装配的历史产物会被归一为一个包
        row["chunks"] = len(plan_chunks(src))      # >40k 的源会分块提炼
        pack, why = load_valid_pack_for_source(url, src, title=e.get("title", ""),
                                               store_dir=store_dir)
        row["pack"] = why
        row["pack_items"] = len((pack or {}).get("items") or []) if pack else 0
        rows.append(row)
    counts = {}
    for r in rows:
        key = f"{r['route']}/{r['reason']}"
        counts[key] = counts.get(key, 0) + 1
    return {"total": len(rows), "counts": counts, "rows": rows}


def emit_distill_tasks(queue_path: str, out_dir: str, store_dir: str = "") -> dict:
    """为「该走两阶段但还没有有效 pack」的条目写自包含提炼任务文件 + manifest。

    manifest 记 url/url_key/title/source_sha256/文件路径；>40k 的源按 `plan_chunks`
    切成多块，每块一份任务文件（`chunk` 字段记行区间与序号）。
    """
    entries = json.load(open(queue_path, encoding="utf-8"))
    os.makedirs(out_dir, exist_ok=True)
    tasks, skipped = [], []
    for e in entries if isinstance(entries, list) else []:
        url = e.get("url", "") or ""
        title = e.get("title", "") or ""
        src = _read_source(e)
        d = route_for_source(src or "")
        if d["route"] != "distill":
            skipped.append({"url": url, "reason": d["reason"]})
            continue
        pack, why = load_valid_pack_for_source(url, src, title=title, store_dir=store_dir)
        if pack is not None:
            skipped.append({"url": url, "reason": "pack_reused"})
            continue
        chunks = plan_chunks(src or "")
        key = DP.url_key_of(url, src or "")   # 旧实现此处变量未定义 → 派单整步 NameError
        for chunk in chunks:
            name = chunk_filename(key, chunk)
            path = os.path.join(out_dir, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(render_chunk_prompt(title, src, chunk))
            tasks.append({"url": url, "url_key": key, "title": title,
                          "source_path": e.get("raw_file") or e.get("output") or "",
                          "source_sha256": _text_hash(src),
                          "contract_version": CONTRACT_VERSION,
                          "schema_version": DP.SCHEMA_VERSION,
                          "distill_prompt_sha256": _text_hash(render_chunk_prompt(title, src, chunk)),
                          "source_chars": d["signals"]["chars"],
                          "task_file": name, "chunk": chunk})
    manifest = {"queue": queue_path, "store_dir": store_dir,
                "contract_version": CONTRACT_VERSION,
                "schema_version": DP.SCHEMA_VERSION,
                "tasks": tasks, "skipped": skipped}
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    return manifest


def _task_stem(task: dict) -> str:
    """任务文件 → 回包 stem（`<key>_distill.txt` → `<key>`；`<key>_c2of3_distill.txt` → `<key>_c2of3`）。"""
    name = task.get("task_file") or f"{task.get('url_key') or ''}_distill.txt"
    return name[:-len("_distill.txt")] if name.endswith("_distill.txt") else name.rsplit(".", 1)[0]


def ingest_distill_results(in_dir: str, store_dir: str = "") -> dict:
    """回收提炼回包 → build/validate → 通过才落盘。

    - 单块源：回包 `<url_key>.json`（沿用旧约定）；
    - >40k 超长源：回包 `<url_key>_c<k>of<N>.json` 每块一份，**全部到齐且各自通过
      机械校验才合并落一个整篇 pack**；缺一块或任一块不过 → 整篇不落盘
      （保守方向：半份 pack 会让写作端误以为已全覆盖，等于把遗漏藏起来）。
      校验不通过的细节进 `failed`，供编排方修正重跑。
    """
    manifest_path = os.path.join(in_dir, "manifest.json")
    manifest = json.load(open(manifest_path, encoding="utf-8"))

    # 按 url_key 归组（同一篇的所有块）
    groups: dict = {}
    order: list = []
    for t in manifest.get("tasks") or []:
        key = t.get("url_key") or ""
        if key not in groups:
            groups[key] = {"tasks": [], "source_path": t.get("source_path") or "",
                           "source_sha256": t.get("source_sha256") or "",
                           "title": t.get("title", ""), "url": t.get("url", "")}
            order.append(key)
        groups[key]["tasks"].append(t)

    saved, failed, missing = [], [], []
    for key in order:
        grp = groups[key]
        tasks = grp["tasks"]
        try:
            src = open(grp["source_path"], encoding="utf-8").read()
        except (OSError, UnicodeError):
            failed.append({"url": grp["url"], "url_key": key, "issues": ["源不可读，版本绑定失败"]})
            continue
        if _text_hash(src) != grp["source_sha256"]:
            failed.append({"url": grp["url"], "url_key": key,
                           "issues": ["源 hash 与派单时不一致（换源/改稿），拒绝消费"]})
            continue

        parts, block_issues = [], []
        for t in sorted(tasks, key=lambda x: int((x.get("chunk") or {}).get("index") or 0)):
            # 派单时的提炼契约必须与当前一致：旧任务书的回包结构对不上本轮粒度
            if str(t.get("contract_version")) != str(CONTRACT_VERSION):
                block_issues.append(
                    f"{t.get('task_file')}: 派单契约版本不一致"
                    f"（{t.get('contract_version')!r} != {CONTRACT_VERSION}），拒绝消费")
                continue
            path = os.path.join(in_dir, _task_stem(t) + ".json")
            if not os.path.exists(path):
                missing.append({"url": grp["url"], "url_key": key,
                                "task_file": t.get("task_file")})
                block_issues.append(f"缺 {t.get('task_file')} 的回包")
                continue
            try:
                parsed = json.loads(_strip_fences(open(path, encoding="utf-8").read()))
            except Exception as exc:
                block_issues.append(f"{t.get('task_file')}: JSON 解析失败: {exc}")
                continue
            # 条目不得越出本块核心区间（重叠上下文不得重复建条目）
            scope = chunk_scope_issues(t.get("chunk") or {}, parsed)
            if scope:
                block_issues += [f"块 {(t.get('chunk') or {}).get('index', 1)}: {m}"
                                 for m in scope[:3]]
                continue
            # 每块先单独过一遍机械校验：只在合并后校验会丢掉「坏在第几块」的位置信息
            probe = DP.build_pack(title=grp["title"], url=grp["url"], source_text=src,
                                  distill_prompt_sha256=t.get("distill_prompt_sha256", ""),
                                  parsed=parsed)
            rep = DP.validate_pack(probe, src)
            if not rep["ok"]:
                idx = (t.get("chunk") or {}).get("index", 1)
                block_issues += [f"块 {idx}: {m}" for m in rep["issues"][:3]]
                continue
            parts.append({"chunk": t.get("chunk") or {"index": 1, "total": 1},
                          "parsed": parsed, "prompt_sha256": t.get("distill_prompt_sha256", "")})

        if block_issues:
            failed.append({"url": grp["url"], "url_key": key, "issues": block_issues})
            continue
        if len(parts) != len(tasks):
            failed.append({"url": grp["url"], "url_key": key,
                           "issues": [f"回包块数不足（{len(parts)}/{len(tasks)}），整篇不落盘"]})
            continue

        if len(parts) == 1:
            chunk = parts[0]["chunk"] or {}
            start = chunk.get("core_start_line") or chunk.get("start_line")
            end = chunk.get("core_end_line") or chunk.get("end_line")
            ranges = [[int(start), int(end)]] if start and end else None
            n_lines = len(src.split("\n"))
            complete = bool(ranges) and ranges[0][0] == 1 and ranges[0][1] == n_lines
            pack = DP.build_pack(title=grp["title"], url=grp["url"], source_text=src,
                                 distill_prompt_sha256=prompt_hash(grp["title"], src),
                                 parsed=parts[0]["parsed"],
                                 processed_ranges=ranges, complete=complete)
        else:
            pack = DP.merge_chunk_packs(title=grp["title"], url=grp["url"], source_text=src,
                                        distill_prompt_sha256=prompt_hash(grp["title"], src),
                                        parts=parts)
        report = DP.validate_pack(pack, src)
        if not report["ok"]:
            failed.append({"url": grp["url"], "url_key": key, "issues": report["issues"]})
            continue
        path_saved = DP.save_pack(pack, store_dir)
        saved.append({"url": grp["url"], "url_key": key, "pack": path_saved,
                      "items": len(pack["items"]), "relations": len(pack["relations"]),
                      "chunks": len(parts)})
    return {"saved": saved, "failed": failed, "missing": missing}


def prepare_queue(queue_path: str, store_dir: str = "", log_path: str = "",
                  dry_run: bool = False) -> dict:
    """批量装配：逐条尝试，不适用的降级单轮并留痕，不阻塞。"""
    entries = json.load(open(queue_path, encoding="utf-8"))
    result = {"prepared": [], "already": [], "stale": [], "fallback": []}
    changed = False
    for e in entries if isinstance(entries, list) else []:
        try:
            rep = prepare_entry(e, store_dir=store_dir, log_path=log_path, dry_run=dry_run)
        except PrepareIneligible as exc:
            result["fallback"].append({"url": e.get("url", ""), "reason": exc.reason,
                                       "stripped": exc.stripped})
            # 回退也可能改写条目（剥掉失效/越界的旧包块）→ 必须写回，不能只看成「跳过」
            if exc.stripped:
                changed = True
            continue
        if rep["status"] == "already_prepared":
            bucket = "already"
        elif rep["status"] == "stale_stripped":
            bucket = "stale"      # 旧包块失效被剥掉 → 该篇回单轮，队列需要写回
            changed = True
        else:
            bucket = "prepared"
            changed = True
        result[bucket].append({"url": e.get("url", "")})
    if changed and not dry_run:
        with open(queue_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
    return result
