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

from prompts.distill import prompt_hash  # noqa: E402
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
CHUNK_OVERLAP_LINES = 40

PACK_BLOCK_HEADER = (
    "以下是**同版源**的独立提炼材料包（先行提炼步骤的产物），供取材与对账使用：\n"
    "- 材料包不是大纲：正文仍按上面模板组织，不得按条目顺序扩写；\n"
    "- 材料包不是标准答案：它与源冲突时以源文本为准；条目外的源信息仍须覆盖；\n"
    "- 标为 quote 的内容是逐字原话，可直接引用；其余为摘意，用你的话重组时保持口径。"
)

_DIGIT_RE = re.compile(r"[0-9]")


class PrepareIneligible(Exception):
    """该篇不走两阶段（短源/数字密集/无有效 pack/源不可读）——降级单轮，不算失败。"""

    def __init__(self, reason: str, decision: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.decision = decision or {}


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

def render_pack_block(pack: dict) -> str:
    body = json.dumps(pack, ensure_ascii=False, indent=1)
    return (f"{MATERIAL_PACK_BEGIN}\n{PACK_BLOCK_HEADER}\n```json\n{body}\n```\n"
            f"{MATERIAL_PACK_END}")


def assemble_write_prompt(base_prompt: str, pack: dict) -> str:
    """生产 prompt 主体逐字节保留，材料包块追加在后（与 S8 distill 臂同款布局）。"""
    return (base_prompt or "").rstrip("\n") + "\n\n" + render_pack_block(pack)


def split_write_prompt(prompt: str) -> tuple:
    """拆回 (主体, 包块或 None)。用于幂等判断与「主体不得被包块污染」的核对。"""
    text = prompt or ""
    i = text.find(MATERIAL_PACK_BEGIN)
    j = text.find(MATERIAL_PACK_END)
    if i < 0 or j < i:
        return text, None
    return text[:i].rstrip("\n"), text[i:j + len(MATERIAL_PACK_END)]


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
    """
    if not block:
        return False, "source_sha256_mismatch"
    if block_source_sha(block) != _text_hash(source_text or ""):
        return False, "source_sha256_mismatch"
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


def plan_chunks(source_text: str, max_chars: int = CHUNK_MAX_CHARS,
                overlap_lines: int = CHUNK_OVERLAP_LINES) -> list:
    """把长源切成带重叠的**行区间**（1-based 闭区间），返回 chunk 描述列表。

    - 整篇字数 ≤ `max_chars` → 单块覆盖全文（与旧行为一致，只是带上 chunk 元信息）；
    - 超过则顺序累计到 `max_chars` 切一刀，下一块回退 `overlap_lines` 行作为上下文重叠；
    -  invariants：首块 start=1、末块 end=总行数、相邻块 `next.start <= cur.end + 1`
      （不留空洞，重叠只会过量不会漏行）。
    """
    lines = (source_text or "").split("\n")
    total_lines = len(lines)
    if total_lines == 0 or not (source_text or "").strip():
        return []
    weights = [len("".join(ln.split())) for ln in lines]
    if sum(weights) <= max_chars:
        return [{"index": 1, "total": 1, "start_line": 1, "end_line": total_lines,
                 "chars": sum(weights)}]
    spans, start, acc = [], 1, 0
    for i, w in enumerate(weights, 1):
        acc += w
        if acc >= max_chars and i < total_lines:
            spans.append((start, i))
            # 回退 overlap 行给下一块做上下文，但必须严格前进（否则单行超长会卡死）
            start = max(start + 1, i + 1 - overlap_lines)
            acc = sum(weights[start - 1:i]) if start <= i else 0
    spans.append((start, total_lines))
    total = len(spans)
    return [{"index": k, "total": total, "start_line": a, "end_line": b,
             "chars": sum(weights[a - 1:b])} for k, (a, b) in enumerate(spans, 1)]


def chunk_source_text(source_text: str, start_line: int, end_line: int) -> str:
    """取该块的行片段（1-based 闭区间）。"""
    lines = (source_text or "").split("\n")
    return "\n".join(lines[max(0, start_line - 1):max(0, end_line)])


def render_chunk_prompt(title: str, source_text: str, chunk: dict) -> str:
    """按 chunk 区间渲染提炼任务：只给**该块的源片段**，但行号仍是整篇里的全局行号。"""
    from prompts.distill import render_distill_prompt
    if not chunk or int(chunk.get("total", 1) or 1) <= 1:
        return render_distill_prompt(title, source_text)
    frag = chunk_source_text(source_text, int(chunk["start_line"]), int(chunk["end_line"]))
    label = (f"第 {chunk['index']}/{chunk['total']} 块"
             f"（源第 {chunk['start_line']}–{chunk['end_line']} 行）")
    return render_distill_prompt(title, frag, chunk_label=label,
                                 start_line=int(chunk["start_line"]))


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
                  source_text: "str | None" = None, dry_run: bool = False) -> dict:
    """给单条队列条目装配材料包块；不适用时抛 `PrepareIneligible`（队列条目不变）。

    幂等：已装配**且仍是当前版本**（源 hash 与提炼契约 hash 都对得上）→ `already_prepared`，
    不二次拼接；契约升级后旧包块会被判 `contract_upgraded` 并换新（只比源 hash 会把旧契约
    的已知缺陷钉在队列里，2026-10-27 实抓）。
    """
    url = entry.get("url", "") or ""
    title = entry.get("title", "") or ""
    src = source_text if source_text is not None else _read_source(entry)
    decision = route_for_source(src or "")
    base_record = {"url": url, "title": title, "route": decision["route"],
                   "reason": decision["reason"], "signals": decision["signals"]}

    if src is None:
        exc = PrepareIneligible("source_unavailable", decision)
        log_dispatch({**base_record, "status": "ineligible", "fallback_reason": exc.reason},
                     log_path)
        raise exc
    if decision["route"] != "distill":
        exc = PrepareIneligible(decision["reason"], decision)
        log_dispatch({**base_record, "status": "ineligible", "fallback_reason": exc.reason},
                     log_path)
        raise exc

    prompt = entry.get("prompt", "") or ""
    base_prompt, block = split_write_prompt(prompt)
    block_reason = ""
    if block is not None:
        current, why = block_is_current(block, src, title=title)
        if current:
            log_dispatch({**base_record, "status": "already_prepared",
                          "source_sha256": _text_hash(src),
                          "write_prompt_sha256": _text_hash(prompt)}, log_path)
            return {"status": "already_prepared", "route": "distill", "entry": entry,
                    "decision": decision}
        # 已装配的包块属于旧源（换源/改稿）或旧提炼契约（升级）→ 不能当有效交接
        prompt = base_prompt
        block_reason = why

    pack, reason = load_valid_pack_for_source(url, src, title=title, store_dir=store_dir)
    if pack is None:
        if block is not None:
            # 旧包块失效且没有同版新包：剥掉回单轮（留痕；条目确实变了，调用方需写回）
            if not dry_run:
                entry["prompt"] = base_prompt
                gc = dict(entry.get("generation_context") or {})
                gc["prompt_sha256"] = _text_hash(base_prompt)
                for k in ("distill_pack_key", "distill_pack_source_sha256",
                          "distill_prompt_sha256"):
                    gc.pop(k, None)
                entry["generation_context"] = gc
            log_dispatch({**base_record, "status": "stale_stripped",
                          "fallback_reason": reason, "source_sha256": _text_hash(src),
                          "block_stale_reason": block_reason}, log_path)
            return {"status": "stale_stripped", "route": "distill", "entry": entry,
                    "decision": decision, "reason": reason}
        exc = PrepareIneligible(reason, decision)
        log_dispatch({**base_record, "status": "ineligible", "fallback_reason": reason,
                      "source_sha256": _text_hash(src)}, log_path)
        raise exc

    assembled = assemble_write_prompt(prompt, pack)
    if not dry_run:
        entry["prompt"] = assembled
        gc = dict(entry.get("generation_context") or {})
        gc["prompt_sha256"] = _text_hash(assembled)
        gc["distill_pack_key"] = pack.get("url_key") or ""
        gc["distill_pack_source_sha256"] = pack.get("source_sha256") or ""
        gc["distill_prompt_sha256"] = pack.get("distill_prompt_sha256") or ""
        entry["generation_context"] = gc
    log_dispatch({**base_record, "status": "prepared" if not dry_run else "would_prepare",
                  "source_sha256": _text_hash(src),
                  "pack_key": pack.get("url_key") or "",
                  "write_prompt_sha256": _text_hash(assembled)}, log_path)
    return {"status": "prepared" if not dry_run else "would_prepare", "route": "distill",
            "entry": entry, "decision": decision, "pack": pack}


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
    rep = prepare_entry(entries[idx], store_dir=store_dir, log_path=log_path,
                        source_text=source_text_override, dry_run=dry_run)
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
        if src is None:
            # 源读不到不等于短源：如实分开报，避免把「缺源」混进 short_source 统计
            rows.append({"url": url, "title": e.get("title", ""), "route": "single",
                         "reason": "source_unavailable", "signals": {},
                         "note_type": e.get("note_type", "")})
            continue
        d = route_for_source(src)
        row = {"url": url, "title": e.get("title", ""), "route": d["route"],
               "reason": d["reason"], "signals": d["signals"], "note_type": e.get("note_type", "")}
        if d["route"] == "distill":
            row["chunks"] = len(plan_chunks(src))  # >40k 的源会分块提炼
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
        for chunk in chunks:
            name = chunk_filename(key, chunk)
            path = os.path.join(out_dir, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(render_chunk_prompt(title, src, chunk))
            tasks.append({"url": url, "url_key": key, "title": title,
                          "source_path": e.get("raw_file") or e.get("output") or "",
                          "source_sha256": _text_hash(src),
                          "distill_prompt_sha256": _text_hash(render_chunk_prompt(title, src, chunk)),
                          "source_chars": d["signals"]["chars"],
                          "task_file": name, "chunk": chunk})
    manifest = {"queue": queue_path, "store_dir": store_dir, "tasks": tasks,
                "skipped": skipped}
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    return manifest


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


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
            pack = DP.build_pack(title=grp["title"], url=grp["url"], source_text=src,
                                 distill_prompt_sha256=prompt_hash(grp["title"], src),
                                 parsed=parts[0]["parsed"])
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
            result["fallback"].append({"url": e.get("url", ""), "reason": exc.reason})
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
