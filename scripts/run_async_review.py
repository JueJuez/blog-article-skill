"""scripts/run_async_review.py — 消费「落盘后异步审核队列」（T7 ② / D4·D5）

`shared/async_review.py` 只负责入队（免费）；本脚本负责**花钱那一步**，所以必须显式跑。

## 执行链（S1 起）

    选单 --plan  →  派单 --emit --batch ID  →  子 Agent 独立审稿  →  回收 --ingest --batch ID
                                                                   └→ 批次报告（处理量/问题/修订/消耗）
    裁定 --verdict（写既有 grounding 账本，不另起质量分）

## 为什么要有「选单」而不是直接 `--emit --limit 5`

`--limit` 只是「按文件顺序取前 5 条」，会把 48 条同源的 `author_unverified` 系统性噪音
排在前面，真正带具体行号的疑点反而永远轮不到。选单做三件事（`plan_batch`）：

1. **有界**：一轮默认最多 5 次独立审稿（含修订后重审），选单时先把重审名额留出来；
   至少留一个普通随机样本名额，样本不足如实记缺额。
2. **有理由**：系统性噪音降权、具体定位优先、旧账先审；同类风险有 per-tag 上限；
   同一篇稿只派一次。**选中与未选中都写原因**，落在 `notes/_meta/async_review_batches/<id>.json`。
3. **不重复花钱**：同一稿同一版本已经拿到有效结论（passed/failed）就不再派单、不再覆盖。

## 两种消费方式

本项目已废弃外部 AI Provider（`.env` `FORCE_AGENT_MODE=1`），所以默认走 **Agent 模式**：

| 模式 | 命令 | 干什么 |
|---|---|---|
| 看队列 | `--stats` / `--dry-run` | 只读：待审条数、体积、预估调用数 |
| 选单 | `--plan --rounds 5` | 生成有界批次（只读队列，不清积压） |
| 派单 | `--emit --batch <id>` | 出派单包 + HANDOFF 交接单，交子 Agent 审 |
| 回收 | `--ingest --batch <id>` | 校验 hash / partial / 自检角色 → 更新队列 → 批次报告 |
| 重解析 | `--ingest --retry-errors` | 回包已在但格式不合格：只重解析，**不产生新审稿轮次** |
| 裁定 | `--verdict true_error / false_positive / unconfirmed` | 回写既有 grounding 账本 |
| 直调 | `--run --limit 5` | 有外部 provider（`FORCE_AGENT_MODE=0`）时直接跑 |

Agent 模式刻意**不重写审核逻辑**：派单包里的口径就是 `templates.QUALITY_GATE_PROMPT`
＋既有 JSON 契约；回收时用同一个 `parse_quality_gate` 判定（`coverage=full` 才认、
P1 一票否决、`reviewer_role` 不得是 author/self_check）——**判据只有一份，不因异步而放松**。

## hash 是这条链路的命门

- 派单包记下 `note_sha256`；**回收时重新算一遍**，对不上就标 `stale` 并丢弃结论。
- 派单包本身只含「当时的稿」与「当时的源」，回收时不再回读文件内容。
- **缺源、改稿、解析失败、partial、自检角色、未回收** 一律不得登记为通过；
  `unrecovered` 保持 `pending`，可续跑，不算通过也不算失败。

## 产物

- 批次：`notes/_meta/async_review_batches/<batch_id>.json`（选中/未选中理由、预算、发包、回收、统计）
- 队列状态：`notes/_meta/async_review_queue.jsonl`
- 审核全文：`notes/_meta/async_reviews/<id>.json`
- 汇总报告：`notes/_reports/async_review_<batch_id>.md`
- 裁定账本：`notes/_meta/grounding/ledger.jsonl`（与接地审计共用同一份）
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

IN_DIR = os.path.join(BASE_DIR, "_tmp", "async_review", "in")
OUT_DIR = os.path.join(BASE_DIR, "_tmp", "async_review", "out")
BATCH_DIR = os.path.join(BASE_DIR, "notes", "_meta", "async_review_batches")
LEDGER = os.path.join(BASE_DIR, "notes", "_meta", "grounding", "ledger.jsonl")

# 选单默认：5 轮独立审稿 = 3 风险档 + 1 随机档 + 1 预留重审（PLAN §3.3）
DEFAULT_ROUNDS = 5
DEFAULT_RESERVED_RERUN = 1
DEFAULT_RANDOM_SLOTS = 1
DEFAULT_PER_TAG_CAP = 2

# 几乎每篇都命中、且多为系统性噪音的风险标签：降权，只留代表样本
GENERIC_TAGS = {"author_unverified"}

_VERDICT_ALIASES = {
    "true_error": "true_error", "真错": "true_error", "真实错误": "true_error",
    "error": "true_error", "confirmed": "true_error",
    "false_positive": "false_positive", "误报": "false_positive", "false": "false_positive",
    "unconfirmed": "unconfirmed", "未确认": "unconfirmed", "unknown": "unconfirmed",
    "pending": "unconfirmed",
}


# ---------------------------------------------------------------- 通用

def _rel(path: str) -> str:
    """项目内路径显示成相对路径；跨盘（例如临时目录）就原样返回，绝不因打印炸掉。"""
    try:
        return os.path.relpath(path, BASE_DIR)
    except ValueError:
        return path


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeError):
        return ""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _gate_prompt() -> str:
    from prompts.templates import QUALITY_GATE_PROMPT
    return QUALITY_GATE_PROMPT


def _verify_fresh(rec: dict, note_text: str) -> "tuple[bool, str]":
    """稿还是当初那版吗？（hash 对不上 = 别审，也别把结论记到新稿上）"""
    from shared.async_review import digest
    return digest(note_text) == rec.get("note_sha256"), "note_changed"


def _claim_block(note: str, source: str) -> str:
    """A5 论点级判据的机械前置（报告项）：把「该核哪些断言」变成具体条目交给审者裁定。

    只做机械半（零模型成本）；**机械结果不是结论** —— 同义改写会误报成未命中，须由审者逐条裁定。
    范围天然等于「评估集 + 15% 抽检」（只有入队的才走到这里，不另起队列）。
    """
    try:
        from prompts import claim_coverage as CC
        rep = CC.claim_coverage(note, source)
        return CC.render_review_block(rep)
    except Exception:                       # 观测零件绝不影响派单
        return ""


# ---------------------------------------------------------------- 选单（有界 / 去重 / 可审计）

def _risk_tags(rec: dict) -> "list[str]":
    """从入队原因里抽风险标签：`[numeric_context]` 这类方括号标签，或 `source_thin` 这类纯 token。"""
    tags = []
    for reason in rec.get("reasons") or []:
        found = re.findall(r"\[([A-Za-z_][A-Za-z0-9_]*)\]", str(reason))
        if found:
            tags.extend(found)
        elif re.fullmatch(r"[a-z_]{3,30}", str(reason).strip()):
            tags.append(str(reason).strip())
    out = []
    for t in tags:
        if t not in out:
            out.append(t)
    return out or ["unclassified"]


def _is_specific(rec: dict) -> bool:
    """带具体定位（行号）的疑点优先——它才是「可能真错」，纯标签多为系统性噪音。"""
    for reason in rec.get("reasons") or []:
        if re.search(r"第\s*\d+\s*行", str(reason)):
            return True
    return False


def _note_rel(rec: dict) -> str:
    rel = rec.get("note_path_rel") or os.path.basename(rec.get("note_path") or "")
    return rel[:-3] if rel.endswith(".md") else rel


def _concluded(all_records) -> set:
    """已有有效结论的 (稿路径, 稿 hash) 集合——派单/选单都靠它避免重复花钱。"""
    from shared.async_review import VALID_CONCLUSIONS
    return {(r.get("note_path") or "", r.get("note_sha256") or "")
            for r in all_records if r.get("status") in VALID_CONCLUSIONS}


def new_batch_id(prefix: str = "arv") -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}"


def _entry(rec: dict, tags, sample: str) -> dict:
    return {"id": rec.get("id"), "note_path": rec.get("note_path") or "",
            "note_rel": _note_rel(rec), "tags": list(tags), "group": (tags or ["unclassified"])[0],
            "specific": _is_specific(rec), "sample": sample,
            "ts": rec.get("ts") or "", "note_sha256": rec.get("note_sha256") or "",
            "reasons": list(rec.get("reasons") or [])}


def plan_batch(records, *, rounds: int = DEFAULT_ROUNDS,
               reserved_rerun: int = DEFAULT_RESERVED_RERUN,
               random_slots: int = DEFAULT_RANDOM_SLOTS,
               per_tag_cap: int = DEFAULT_PER_TAG_CAP,
               seed=None, limit: int = 0, all_records=None,
               batch_id: str = "") -> dict:
    """生成一批有界选单。**只读队列**：不清积压、不改任何记录状态。

    预算：`rounds` = 本批最多几次独立审稿（含修订后重审）；`reserved_rerun` 先扣掉预留；
    `random_slots` 是无条件保留的随机样本名额；余下才是风险档。

    返回的字典即批次账本（选中 / 未选中及原因 / 预算 / 空档说明）。
    """
    rounds = max(1, int(rounds))
    random_slots = max(0, int(random_slots))
    reserved_rerun = max(0, min(int(reserved_rerun), rounds - 1))
    total_cap = rounds
    if limit:
        total_cap = max(1, min(total_cap, int(limit)))
    random_slots = min(random_slots, max(0, total_cap - 1))   # 至少留 1 个风险/重审位
    risk_slots = max(0, total_cap - reserved_rerun - random_slots)

    all_records = list(all_records if all_records is not None else records)
    concluded = _concluded(all_records)

    eligible, not_selected = [], []
    for rec in records:
        note_path = rec.get("note_path") or ""
        if not note_path or not os.path.exists(note_path):
            not_selected.append({"id": rec.get("id"), "note_rel": _note_rel(rec),
                                 "why": "note_missing", "tags": _risk_tags(rec)})
            continue
        if (note_path, rec.get("note_sha256") or "") in concluded:
            not_selected.append({"id": rec.get("id"), "note_rel": _note_rel(rec),
                                 "why": "valid_conclusion_exists", "tags": _risk_tags(rec)})
            continue
        source_path = rec.get("source_path") or ""
        if not source_path or not os.path.exists(source_path):
            not_selected.append({"id": rec.get("id"), "note_rel": _note_rel(rec),
                                 "why": "source_missing", "tags": _risk_tags(rec)})
            continue
        eligible.append(rec)

    def _rank(rec):
        tags = _risk_tags(rec)
        generic = all(t in GENERIC_TAGS for t in tags)
        return (1 if generic else 0,               # 非系统性噪音优先
                0 if _is_specific(rec) else 1,     # 带具体行号的疑点优先
                rec.get("ts") or "",               # 旧账先审
                rec.get("id") or "")               # 稳定排序

    ordered = sorted(eligible, key=_rank)
    selected, used_notes, tag_count = [], {}, {}

    def _reject(rec, why):
        not_selected.append({"id": rec.get("id"), "note_rel": _note_rel(rec),
                             "why": why, "tags": _risk_tags(rec)})

    for rec in ordered:
        if len(selected) >= risk_slots:
            _reject(rec, "over_budget")
            continue
        path = rec.get("note_path") or ""
        if path in used_notes:
            _reject(rec, f"duplicate_note:{used_notes[path]}")
            continue
        tags = _risk_tags(rec)
        heavy = [t for t in tags if t not in GENERIC_TAGS
                 and tag_count.get(t, 0) >= per_tag_cap]
        if heavy:
            _reject(rec, f"tag_cap:{heavy[0]}")
            continue
        used_notes[path] = rec.get("id") or ""
        for t in tags:
            tag_count[t] = tag_count.get(t, 0) + 1
        selected.append(_entry(rec, tags, "risk"))

    rng = random.Random(seed if seed is not None else int(time.time()))
    shortfall = 0
    for _ in range(random_slots):
        pool = [r for r in eligible if (r.get("note_path") or "") not in used_notes]
        if not pool:
            shortfall += 1
            continue
        pick = rng.choice(pool)
        used_notes[pick.get("note_path") or ""] = pick.get("id") or ""
        entry = _entry(pick, _risk_tags(pick), "random")
        selected.append(entry)
        not_selected = [s for s in not_selected if s.get("id") != pick.get("id")]

    covered = {s.get("id") for s in selected} | {s.get("id") for s in not_selected}
    for rec in eligible:
        if rec.get("id") not in covered:
            _reject(rec, "over_budget")

    for rank, entry in enumerate(selected, 1):
        entry["rank"] = rank

    return {
        "batch_id": batch_id or new_batch_id(),
        "created_at": _now(),
        "state": "planned",
        "budget": {"rounds": rounds, "risk_slots": risk_slots,
                   "random_slots": random_slots, "reserved_rerun": reserved_rerun},
        "per_tag_cap": per_tag_cap,
        "seed": seed,
        "shortfall": {"random_missing": shortfall},
        "selected": selected,
        "not_selected": not_selected,
        "emitted": [],
        "skipped_at_emit": [],
        "results": {},
    }


def save_batch(plan: dict, batch_dir: str = "") -> str:
    d = batch_dir or BATCH_DIR
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{plan['batch_id']}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


def load_batch(batch_id: str, batch_dir: str = "") -> dict:
    path = os.path.join(batch_dir or BATCH_DIR, f"{batch_id}.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def cmd_plan(records, all_records, *, rounds: int, reserved_rerun: int, random_slots: int,
             per_tag_cap: int, seed, limit: int) -> int:
    plan = plan_batch(records, rounds=rounds, reserved_rerun=reserved_rerun,
                      random_slots=random_slots, per_tag_cap=per_tag_cap,
                      seed=seed, limit=limit, all_records=all_records)
    path = save_batch(plan)
    b = plan["budget"]
    print(f"批次 {plan['batch_id']}：预算 {b['rounds']} 轮"
          f"（风险 {b['risk_slots']} + 随机 {b['random_slots']} + 预留重审 {b['reserved_rerun']}）")
    print(f"选中 {len(plan['selected'])} 条，未选中 {len(plan['not_selected'])} 条 → {_rel(path)}")
    for s in plan["selected"]:
        print(f"  ✅ [{s['sample']}] {s['note_rel']}  {s['group']}"
              f"{'（具体定位）' if s['specific'] else ''}")
    for s in plan["not_selected"][:10]:
        print(f"  · 未选 {s['note_rel']}：{s['why']}")
    if len(plan["not_selected"]) > 10:
        print(f"  · …其余 {len(plan['not_selected']) - 10} 条原因见批次文件")
    if plan["shortfall"]["random_missing"]:
        print(f"  ⚠️ 随机样本缺额 {plan['shortfall']['random_missing']} 条（候选不足，如实记录）")
    if not plan["selected"]:
        print("  ⚠️ 本批没有可派单条目：缺源/稿已变/已有有效结论都会落到未选中，"
              "这不等于质量通过。")
        return 0
    print(f"\n下一步：python scripts/run_async_review.py --emit --batch {plan['batch_id']}")
    return 0


# ---------------------------------------------------------------- 派单（Agent 模式）

DISPATCH_TMPL = """# 异步审核派单 · {id}

> 这是**落盘之后**的独立复审，不决定这篇能否入库（已入库）。你可以判 fail —— 那正是它的价值。

## 你的角色

`reviewer_role = independent_review_call`：**独立会话审者**。不得以「作者这样说」为通过理由，
不得因为稿子读起来顺就放过；只对「源支持不支持」下判断。

## 记录（回填时校验，勿改）

- id: `{id}`
- batch: `{batch}`
- note_path: `{note_path}`
- note_type: `{note_type}`
- note_sha256: `{note_sha256}`
- source_sha256: `{source_sha256}`
- 入队原因: {reasons}

## 审核口径（原文，逐条执行）

{gate_prompt}

{claim_block}
## 待审笔记（全文）

<<<NOTE
{note}
NOTE

## 原文（全文）

<<<SOURCE
{source}
SOURCE

## 交付

严格输出一个 JSON 对象（不要额外解释），字段：

```json
{{
  "score": 0,
  "pass": false,
  "status": "complete",
  "coverage": "full",
  "reviewer_role": "independent_review_call",
  "issues": ["问题1（含位置与改法）"],
  "findings": [{{"severity": "P1|P2", "note_excerpt": "", "note_lines": "", "source_excerpt": "",
                "source_lines": "", "reason": "", "suggestion": ""}}],
  "claims": [{{"claim": "", "support": "", "source_excerpt": ""}}]
}}
```

写到 `_tmp/async_review/out/{id}.json`，然后跑 `python scripts/run_async_review.py --ingest --batch {batch}` 收口。
"""

HANDOFF_TMPL = """# 复审批次交接单 · {batch_id}

生成：{created_at}

## 预算（不得突破）

- 独立审稿轮次上限：**{rounds}**（本批已派 {emitted_n}）
- 其中预留修订后重审：**{reserved}** 轮
- 随机样本名额：**{random_slots}**（本批实际 {random_used}）

## 派单包（{emitted_n} 份）

{dispatch_list}

## 编排纪律

1. 每份派单交**独立子 Agent**：它只拿派单包，不看生成过程、不看作者自检结论。
2. 子 Agent 必须把 JSON 写到 `_tmp/async_review/out/<id>.json`，禁止直接改队列、禁止改稿。
3. **发包不算已审**：没有回包就是 `unrecovered`，保持 pending，可以续跑。
4. 回收一次后跑 `--ingest --batch {batch_id}`，报告落在 `notes/_reports/`。
5. 回包被判 invalid/partial/self_check 时状态是 `error`，**不是通过**；只重解析用
   `--ingest --retry-errors`（不产生新审稿轮次），要重审才重新入队。
6. 确认的问题按 PIPELINES §3 的正式入口修订（重做走 `resum_save_batch`，in-place 覆盖），
   改写后稿 hash 变化 → 需重新入队才能复审；旧结论不得冒充新稿。
7. 裁定（真错 / 误报 / 未确认）用 `--verdict` 写既有 grounding 账本，不另起质量分。
"""


def _est_calls(note: str, source: str) -> int:
    """按语义审核的分块规则估算调用数（不是计费口径，只用于预算预警）。"""
    try:
        from prompts.semantic_review import blocks
        sb, nb = len(blocks(source)), len(blocks(note))
        est = max(1, sb) * max(1, nb)
        return est + (1 if est > 1 else 0)
    except Exception:
        return 1


def cmd_emit(limit: int, records: list, batch: dict = None, all_records: list = None) -> int:
    from shared import async_review as AR

    if batch:
        # 批次派单要看**全量**队列：选中条目可能已经不 pending（例如刚被判过），
        # 那也要落在 skipped_at_emit 里说明为什么没发。
        all_records = list(all_records if all_records is not None else AR.load())
        ids = [s.get("id") for s in batch.get("selected") or []]
        if limit:
            ids = ids[:limit]
        by_id = {r.get("id"): r for r in all_records}
        selected_records = [by_id[i] for i in ids if i in by_id]
        concluded = _concluded(all_records)
        missing = [i for i in ids if i not in by_id]
        batch.setdefault("skipped_at_emit", [])
        for rid in missing:
            batch["skipped_at_emit"].append({"id": rid, "why": "record_missing"})
        records = selected_records
    else:
        concluded = set()

    os.makedirs(IN_DIR, exist_ok=True)
    made, skipped = 0, []
    for rec in records:
        rid = rec.get("id")
        note_path = rec.get("note_path") or ""
        status = rec.get("status")
        if status in AR.VALID_CONCLUSIONS:
            skipped.append((rid, "already_concluded"))
            continue
        if status not in (None, "", "pending"):
            # error / stale / skipped 都不是「待派单」：重审请重新入队，别悄悄再花一次钱
            skipped.append((rid, f"status_{status}"))
            continue
        if (note_path, rec.get("note_sha256") or "") in concluded:
            skipped.append((rid, "valid_conclusion_exists"))
            continue
        note = _read(note_path)
        if not note:
            skipped.append((rid, "note_missing"))
            continue
        ok, why = _verify_fresh(rec, note)
        if not ok:
            AR.update(rid, status="stale", result={"reason": why})
            skipped.append((rid, why))
            continue
        source = _read(rec.get("source_path") or "")
        if not source:
            AR.update(rid, status="skipped", result={"reason": "source_missing"})
            skipped.append((rid, "source_missing"))
            continue
        body = DISPATCH_TMPL.format(
            id=rid, batch=(batch or {}).get("batch_id", "-"), note_path=note_path,
            note_type=rec.get("note_type") or "unknown",
            note_sha256=rec.get("note_sha256"), source_sha256=rec.get("source_sha256"),
            reasons="、".join(rec.get("reasons") or []) or "抽样", gate_prompt=_gate_prompt(),
            claim_block=_claim_block(note, source), note=note, source=source)
        path = os.path.join(IN_DIR, f"{rid}.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        made += 1
        if batch:
            batch["emitted"].append({"id": rid, "at": _now(),
                                     "note_rel": _note_rel(rec),
                                     "note_chars": len(note), "source_chars": len(source),
                                     "est_calls": _est_calls(note, source)})
        print(f"  ➜ 派单 {rid}  {_rel(path)}  "
              f"(稿 {len(note)} / 源 {len(source)} 字)")

    for rid, why in skipped:
        print(f"  · 跳过 {rid}：{why}")
        if batch:
            batch["skipped_at_emit"].append({"id": rid, "why": why})

    print(f"\n派单 {made} 份 → {_rel(IN_DIR)}")
    if batch:
        batch["state"] = "emitted" if made else batch.get("state", "planned")
        batch["emitted_at"] = _now() if made else batch.get("emitted_at", "")
        save_batch(batch)
        if made:
            _write_handoff(batch)
    if made:
        next_cmd = (f"python scripts/run_async_review.py --ingest --batch {batch['batch_id']}"
                    if batch else "python scripts/run_async_review.py --ingest")
        print("\n下一步：子 Agent 读派单包 → 写 JSON 到 _tmp/async_review/out/<id>.json → " + next_cmd)
    # stale / skipped 的条目状态已就地更新，不算执行失败：没有可派单的就正常收工
    return 0


def _write_handoff(batch: dict) -> str:
    b = batch["budget"]
    dispatch_list = "\n".join(
        f"- `{e['id']}`  {e.get('note_rel', '')}  "
        f"（稿 {e.get('note_chars', 0)} / 源 {e.get('source_chars', 0)} 字，"
        f"估算 {e.get('est_calls', 1)} 次调用）"
        for e in batch.get("emitted") or [])
    random_used = sum(1 for s in batch.get("selected") or [] if s.get("sample") == "random")
    body = HANDOFF_TMPL.format(
        batch_id=batch["batch_id"], created_at=batch.get("created_at", ""),
        rounds=b["rounds"], reserved=b["reserved_rerun"], random_slots=b["random_slots"],
        random_used=random_used, emitted_n=len(batch.get("emitted") or []),
        dispatch_list=dispatch_list or "（无）")
    path = os.path.join(IN_DIR, f"HANDOFF-{batch['batch_id']}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


# ---------------------------------------------------------------- 回收

def _classify_reject(raw: str) -> str:
    """回包不合格的原因码（**只用于记账**，判定仍由 parse_quality_gate 做）。"""
    text = (raw or "").strip()
    if not text:
        return "empty_output"
    body = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    body = re.sub(r"\s*```$", "", body)
    try:
        data = json.loads(body)
    except ValueError:
        return "invalid_json"
    if not isinstance(data, dict):
        return "not_object"
    coverage = data.get("coverage", "full")
    if coverage != "full":
        return f"coverage_{coverage}"
    status = data.get("status", "")
    if status in ("partial", "unknown", "needs_review", "timeout"):
        return f"status_{status}"
    if data.get("reviewer_role") in ("author", "self_check", "unknown"):
        return "reviewer_self_check"
    if type(data.get("score")) is not int:
        return "bad_score"
    if type(data.get("pass")) is not bool:
        return "bad_pass"
    if not isinstance(data.get("issues"), list):
        return "bad_issues"
    if not isinstance(data.get("findings", []), list):
        return "bad_findings"
    return "schema_invalid"


def cmd_ingest(records: list, batch: dict = None, retry_errors: bool = False) -> dict:
    from prompts.templates import parse_quality_gate
    from shared import async_review as AR

    counters = {"selected": len((batch or {}).get("selected") or []) if batch else len(records),
                "emitted": len((batch or {}).get("emitted") or []) if batch else 0,
                "recovered": 0, "passed": 0, "failed": 0, "findings": 0,
                "unrecovered": [], "stale": [], "skipped": [], "error": [],
                "already_concluded": [], "retried": [],
                "rounds_used": len((batch or {}).get("emitted") or []) if batch else 0}
    results = {}

    for rec in records:
        rid = rec.get("id")
        status = rec.get("status")
        if status in AR.VALID_CONCLUSIONS:
            counters["already_concluded"].append(rid)      # 不覆盖已有有效结论、不重复计消耗
            continue
        if status == "stale":
            counters["stale"].append(rid)
            continue
        if status == "skipped":
            counters["skipped"].append(rid)
            continue
        if status == "error" and not retry_errors:
            counters["error"].append(rid)
            continue
        payload = os.path.join(OUT_DIR, f"{rid}.json")
        if not os.path.exists(payload):
            counters["unrecovered"].append(rid)            # 仍是 pending，可续跑
            continue
        note = _read(rec.get("note_path") or "")
        if not note:
            AR.update(rid, status="error", result={"reason": "note_missing"})
            counters["error"].append(rid)
            continue
        ok, why = _verify_fresh(rec, note)
        if not ok:
            AR.update(rid, status="stale", result={"reason": why})
            counters["stale"].append(rid)
            print(f"  ⏭ {rid} 稿已变（{why}），结论丢弃")
            continue
        raw = _read(payload)
        parsed = parse_quality_gate(raw)
        if parsed.get("status") in ("needs_review",) or not isinstance(parsed.get("score"), int):
            reason = _classify_reject(raw)
            AR.update(rid, status="error",
                      result={"reason": reason, "issues": parsed.get("issues", [])[:8]})
            counters["error"].append(rid)
            results[rid] = {"status": "error", "reason": reason}
            print(f"  ⚠️ {rid} 回包不合格：{reason}")
            continue
        new_status = "passed" if parsed["passed"] else "failed"
        summary = {"score": parsed["score"], "status": parsed["status"],
                   "issues": parsed["issues"][:8], "findings": len(parsed.get("findings") or []),
                   "reason": "retried" if status == "error" else ""}
        AR.update(rid, status=new_status, result=summary)
        AR.save_result(rec, {"parsed": parsed, "raw": raw, "record": rec})
        results[rid] = {"status": new_status, "score": parsed["score"],
                        "issues": parsed["issues"][:3]}
        counters["recovered"] += 1
        counters[new_status] += 1
        counters["findings"] += len(parsed.get("findings") or [])
        if status == "error":
            counters["retried"].append(rid)
        print(f"  {'✅' if new_status == 'passed' else '❌'} {rid} {parsed['score']}/100 "
              f"{(rec.get('title') or '')[:26]}")

    print(f"\n回收 {counters['recovered']} 条：passed {counters['passed']} / "
          f"failed {counters['failed']}；未回收 {len(counters['unrecovered'])}；"
          f"error {len(counters['error'])}；已有结论跳过 {len(counters['already_concluded'])}")
    if counters["unrecovered"]:
        print("  " + "、".join(counters["unrecovered"][:10])
              + ("…" if len(counters["unrecovered"]) > 10 else "")
              + "  （仍为 pending，未审 ≠ 通过）")
    if batch:
        batch["state"] = "ingested"
        batch["ingested_at"] = _now()
        batch["results"] = results
        batch["counters"] = counters
        save_batch(batch)
    return counters


# ---------------------------------------------------------------- 裁定 → 既有 grounding 账本

def append_verdict(batch_id: str, rec_id: str, *, verdict: str, check: str = "",
                   finding: str = "", action: str = "none", revisions: int = 0,
                   minutes=None, notes: str = "", note: str = "", source: str = "",
                   note_sha256_before: str = "", note_sha256_after: str = "",
                   ledger: str = "") -> dict:
    """把一条裁定追加到 `notes/_meta/grounding/ledger.jsonl`（与接地审计同一份账本）。

    误报率的分母是「已裁定」而非「已命中」：未确认项单独记，既不冒充真错也不算误报。
    未知的人工分钟保留 unknown，不写 0。
    """
    key = _VERDICT_ALIASES.get(str(verdict).strip().lower()) or _VERDICT_ALIASES.get(str(verdict).strip())
    if not key:
        raise ValueError(f"未知裁定: {verdict}（可用 true_error / false_positive / unconfirmed）")
    if isinstance(minutes, str):
        minutes = None if minutes.strip().lower() in ("", "unknown", "none") else minutes
    row = {
        "date": time.strftime("%Y-%m-%d"),
        "batch": batch_id,
        "id": rec_id,
        "note": note,
        "source": source,
        "check": check,
        "finding": finding,
        "verdict": key,
        "action": action,
        "revisions": int(revisions or 0),
        "note_sha256_before": note_sha256_before,
        "note_sha256_after": note_sha256_after,
        "reviewer": "async_review",
        "minutes": "unknown" if minutes is None else minutes,
        "notes": notes,
    }
    path = ledger or LEDGER
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def _read_ledger(batch_id: str, ledger: str = "") -> list:
    path = ledger or LEDGER
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("batch") == batch_id:
                out.append(row)
    return out


def _ledger_counts(rows: list) -> dict:
    counts = {"true_error": 0, "false_positive": 0, "unconfirmed": 0, "other": 0,
              "revisions": 0, "minutes_known": 0, "minutes_unknown": 0}
    for row in rows:
        key = _VERDICT_ALIASES.get(str(row.get("verdict", "")).strip().lower())
        if key in ("true_error", "false_positive", "unconfirmed"):
            counts[key] += 1
        else:
            counts["other"] += 1
        try:
            counts["revisions"] += int(row.get("revisions") or 0)
        except (TypeError, ValueError):
            pass
        minutes = row.get("minutes")
        if isinstance(minutes, (int, float)):
            counts["minutes_known"] += minutes
        else:
            counts["minutes_unknown"] += 1
    return counts


# ---------------------------------------------------------------- 直调（有 provider 时）

def cmd_run(limit: int, records: list, dry_run: bool) -> int:
    from shared import async_review as AR

    if os.environ.get("FORCE_AGENT_MODE", "1") == "1":
        print("[ERR] 当前 FORCE_AGENT_MODE=1（无外部 provider），无法直调。")
        print("      改用：--plan → --emit --batch <id> → 子 Agent 审 → --ingest --batch <id>。")
        return 2

    from prompts.semantic_review import blocks, MAX_REVIEW_CALLS
    from prompts.templates import QUALITY_GATE_PROMPT, parse_quality_gate

    def caller(prompt, content, **kwargs):
        from articles.main import call_ai_summary_with_meta
        return call_ai_summary_with_meta(prompt, content, **kwargs)

    todo, stale, skipped = [], 0, 0
    for rec in records:
        note = _read(rec.get("note_path") or "")
        if not note:
            AR.update(rec["id"], status="error", result={"reason": "note_missing"})
            skipped += 1
            continue
        ok, why = _verify_fresh(rec, note)
        if not ok:
            AR.update(rec["id"], status="stale", result={"reason": why})
            stale += 1
            continue
        source = _read(rec.get("source_path") or "")
        if not source:
            AR.update(rec["id"], status="skipped", result={"reason": "source_missing"})
            skipped += 1
            continue
        est = len(blocks(source)) * len(blocks(note))
        est = est + (1 if est > 1 else 0)
        if est > MAX_REVIEW_CALLS:
            AR.update(rec["id"], status="skipped",
                      result={"reason": f"超审核预算（{est} > {MAX_REVIEW_CALLS} 次调用）"})
            skipped += 1
            continue
        todo.append((rec, note, source, est))

    print(f"待审 {len(todo)} 条｜过期 {stale}｜跳过 {skipped}｜预估调用 "
          f"{sum(t[3] for t in todo)} 次")
    if dry_run or not todo:
        return 0

    passed = failed = 0
    for rec, note, source, _est in todo:
        from prompts.semantic_review import review_fulltext
        report = review_fulltext(note, source, rec.get("note_type") or "", QUALITY_GATE_PROMPT,
                                 caller, parse_quality_gate)
        status = "passed" if report.get("passed") else (
            "error" if report.get("status") == "needs_review" else "failed")
        AR.update(rec["id"], status=status,
                  result={"score": report.get("score"), "status": report.get("status"),
                          "issues": (report.get("issues") or [])[:8],
                          "calls": len(report.get("calls") or [])})
        AR.save_result(rec, report)
        passed += status == "passed"
        failed += status == "failed"
        print(f"  {status} {rec.get('id')} {report.get('score')}/100 "
              f"{(rec.get('title') or '')[:26]}")
    print(f"\n审完 {len(todo)}：passed {passed} / failed {failed}")
    return 0


# ---------------------------------------------------------------- 报告

def _fmt_duration(batch: dict) -> str:
    try:
        t0 = time.mktime(time.strptime(batch.get("emitted_at", ""), "%Y-%m-%dT%H:%M:%S"))
        t1 = time.mktime(time.strptime(batch.get("ingested_at", ""), "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return "unknown"
    if t1 < t0:
        return "unknown"
    return f"{int((t1 - t0) // 60)} 分钟"


def write_report(path: str, stats: dict, rows: list, batch: dict = None,
                 counters: dict = None, ledger_rows: list = None) -> str:
    L = []
    A = L.append
    if batch is None:
        A("# 落盘后异步审核（T7 ②）· 汇总")
        A("")
        A(f"- 生成：`python scripts/run_async_review.py --ingest`（{time.strftime('%Y-%m-%d %H:%M')}）")
        A("- 队列：`notes/_meta/async_review_queue.jsonl`")
        A("")
        A("## 一、状态分布")
        A("")
        A("| 状态 | 条数 |")
        A("|---|---|")
        for k in ("pending", "passed", "failed", "stale", "skipped", "error", "total"):
            A(f"| `{k}` | {stats.get(k, 0)} |")
        A("")
        done = [r for r in rows if r.get("status") in ("passed", "failed")]
        if done:
            A("## 二、已审条目")
            A("")
            A("| 状态 | 分数 | 标题 | 首条问题 |")
            A("|---|---|---|---|")
            for r in done:
                res = r.get("result") or {}
                issue = (res.get("issues") or [""])[0]
                A(f"| `{r.get('status')}` | {res.get('score', '')} | "
                  f"{(r.get('title') or r.get('note_path') or '')[:40]} | {str(issue)[:60]} |")
            A("")
        A("> `stale` = 审的时候稿已被改过，结论作废（hash 不匹配），需重新入队。")
        A("> `skipped` = 源缺失或超审核预算，未审；不要当成通过。")
        A("> `pending` = 尚未审；报告生成成功不等于质量通过。")
    else:
        A(f"# 异步审核批次报告 · {batch.get('batch_id')}")
        A("")
        A(f"- 生成时间：{_now()}")
        A(f"- 队列：`{_rel(os.path.join(BASE_DIR, 'notes', '_meta', 'async_review_queue.jsonl'))}`")
        A(f"- 批次账本：`notes/_meta/async_review_batches/{batch.get('batch_id')}.json`")
        b = batch.get("budget") or {}
        A(f"- 预算：{b.get('rounds', '?')} 轮 = 风险 {b.get('risk_slots', '?')} + "
          f"随机 {b.get('random_slots', '?')} + 预留重审 {b.get('reserved_rerun', '?')}")
        A("")
        counters = counters or batch.get("counters") or {}
        selected = batch.get("selected") or []
        emitted = batch.get("emitted") or []
        ledger_rows = ledger_rows if ledger_rows is not None else _read_ledger(batch.get("batch_id", ""))
        lc = _ledger_counts(ledger_rows)
        recovered = counters.get("recovered")
        if recovered is None:
            recovered = sum(1 for r in rows if r.get("id") in
                            {s.get("id") for s in selected} and r.get("status") in ("passed", "failed"))

        A("## 一、处理量（本批 / 全库分开）")
        A("")
        A("| 项 | 本批 | 说明 |")
        A("|---|---|---|")
        A(f"| 选中 | {len(selected)} | 见批次账本 `selected` |")
        A(f"| 发包 | {len(emitted)} | 发包 ≠ 已审 |")
        A(f"| 有效回收 | {recovered} | passed + failed |")
        A(f"| 未回收 | {len(counters.get('unrecovered') or [])} | 仍是 pending，可续跑 |")
        A(f"| error | {len(counters.get('error') or [])} | 回包不合格，不是通过 |")
        A(f"| stale | {len(counters.get('stale') or [])} | 稿已变，结论作废 |")
        A(f"| skipped | {len(counters.get('skipped') or [])} | 缺源/超预算，未审 |")
        A(f"| 已有结论跳过 | {len(counters.get('already_concluded') or [])} | 同版不重复付费 |")
        A(f"| 未选中 | {len(batch.get('not_selected') or [])} | 原因逐条记录 |")
        A(f"| 全库剩余 pending | {stats.get('pending', 0)} | 历史累计，不是本批失败数 |")
        A("")

        A("## 二、问题（分母分身份）")
        A("")
        id_set = {s.get("id") for s in selected}
        mech = set()
        for r in rows:
            if r.get("id") in id_set:
                for reason in r.get("reasons") or []:
                    mech.add(str(reason))
        A("| 项 | 数值 | 口径 |")
        A("|---|---|---|")
        A(f"| 机械疑点（选中条目去重） | {len(mech)} | 只提供核查线索，不是结论 |")
        A(f"| 独立审核 findings | {counters.get('findings', 0) or 0} | 回包 findings 计数 |")
        A(f"| 经回源确认真错 | {lc['true_error']} | 账本裁定 |")
        A(f"| 误报 | {lc['false_positive']} | 账本裁定 |")
        A(f"| 未确认 | {lc['unconfirmed']} | 单独记，**不计入误报** |")
        A(f"| 其他/未知裁定 | {lc['other']} | 不计入任何分母 |")
        A("")
        judged = lc["true_error"] + lc["false_positive"]
        if judged:
            A(f"误报率 = {lc['false_positive']} / {judged}（已裁定 = 真错 + 误报）= "
              f"{lc['false_positive'] / judged:.0%}；"
              f"未确认 {lc['unconfirmed']} 项**不计入分母**，也不算真错。")
        else:
            A(f"误报率：本批尚无已裁定疑点（未确认 {lc['unconfirmed']} 项待核）"
              f"——**不是 0% 误报**，也不是全部通过。")
        A("")

        A("## 三、修订")
        A("")
        revised = [r for r in ledger_rows if (r.get("revisions") or 0) or r.get("action") == "revised"]
        pending_fix = [r for r in ledger_rows
                       if _VERDICT_ALIASES.get(str(r.get("verdict", "")).strip().lower()) == "true_error"
                       and r not in revised
                       and str(r.get("action") or "none") not in ("revised", "fixed")]
        if revised:
            A("| id | 裁定 | 检查项 | 修订次数 | 稿 hash 前→后 | 动作 | 仍待处理 |")
            A("|---|---|---|---|---|---|---|")
            for r in revised:
                before = str(r.get("note_sha256_before") or "")[:8] or "unknown"
                after = str(r.get("note_sha256_after") or "")[:8] or "unknown"
                A(f"| `{r.get('id')}` | `{r.get('verdict')}` | {r.get('check') or ''} | "
                  f"{r.get('revisions') or 0} | {before}→{after} | {r.get('action') or 'none'} | "
                  f"{r.get('notes') or ''} |")
        else:
            A("本批尚无修订记录。若确有真错未修，记未解决项，不要为了验收制造修订。")
        if pending_fix:
            A("")
            A(f"**已确认真错但本批未修订：{len(pending_fix)} 项**")
            A("")
            A("| id | 检查项 | 发现 | 为什么本批没改 |")
            A("|---|---|---|---|")
            for r in pending_fix:
                A(f"| `{r.get('id')}` | {r.get('check') or ''} | "
                  f"{str(r.get('finding') or '')[:52]} | 定向修订需走 PIPELINES §3 重做入口整篇重生成，"
                  f"成本高于本次收益；留待下一批，不为验收制造修订 |")
        A("")

        A("## 四、消耗")
        A("")
        est_calls = sum(int(e.get("est_calls") or 1) for e in emitted)
        A("| 项 | 数值 | 说明 |")
        A("|---|---|---|")
        A(f"| 独立审稿轮次 | {counters.get('rounds_used', len(emitted))}/{b.get('rounds', '?')} | "
          f"含重审预留；重解析不计新轮次（本批重解析 {len(counters.get('retried') or [])}） |")
        A(f"| 预估模型调用 | {est_calls} | 按分块估算，非计费口径 |")
        A("| token / usage | unknown（not_exposed） | 不把未知写成 0 |")
        A(f"| 审核耗时 | {_fmt_duration(batch)} | 发包 → 回收 |")
        if lc["minutes_known"]:
            A(f"| 人工分钟 | {lc['minutes_known']} | 账本记录 {len(ledger_rows)} 条 |")
        else:
            A(f"| 人工分钟 | unknown | 账本未记录（{lc['minutes_unknown']} 条未填），不写 0 |")
        A("")

        unresolved = []
        if counters.get("unrecovered"):
            unresolved.append(f"未回收 {len(counters['unrecovered'])} 条（仍 pending，可续跑）")
        if counters.get("error"):
            unresolved.append(f"回包不合格 {len(counters['error'])} 条（可 --retry-errors 重解析）")
        if counters.get("stale"):
            unresolved.append(f"稿已变作废 {len(counters['stale'])} 条（需重新入队）")
        if batch.get("shortfall", {}).get("random_missing"):
            unresolved.append(f"随机样本缺额 {batch['shortfall']['random_missing']} 条")
        if pending_fix:
            by_id = {}
            for r in pending_fix:
                by_id[r.get("id")] = by_id.get(r.get("id"), 0) + 1
            unresolved.append("已确认真错未修订 {} 项：{}".format(
                len(pending_fix),
                "、".join(f"`{k}`" + (f"×{v}" if v > 1 else "") for k, v in by_id.items())))
        if lc["unconfirmed"]:
            unresolved.append(f"未回源核对（仍待确认）{lc['unconfirmed']} 项——"
                              f"不计入误报，也不当已通过")
        A("## 五、未解决项")
        A("")
        A("- " + "\n- ".join(unresolved) if unresolved else "- 无（仅指本批；不代表全库通过）")
        A("")
        A("## 边界")
        A("")
        A("> 样本只代表被选批次：风险优先样本不能估算全库错误率。")
        A("> 队列为空、命中为零、报告生成成功，都不能单独称为质量通过。")
        A("> 机械覆盖率/关键词命中率不是逐篇语义分数；保留率只在固定必留清单的配对样本内计算。")
        A("> 原始失败记录保留在队列 `result`、批次账本与 `notes/_meta/async_reviews/`，可追溯。")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    return path


# ---------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser(description="落盘后异步审核队列（有界选单 / 派单 / 回收 / 统计）")
    ap.add_argument("--stats", action="store_true", help="只看队列状态分布")
    ap.add_argument("--plan", action="store_true", help="生成有界批次选单（只读队列）")
    ap.add_argument("--emit", action="store_true", help="出派单包（Agent 模式）")
    ap.add_argument("--ingest", action="store_true", help="回收子 Agent 结果并更新队列")
    ap.add_argument("--run", action="store_true", help="直调 provider 跑 review_fulltext")
    ap.add_argument("--dry-run", action="store_true", help="只估算，不调用、不改队列")
    ap.add_argument("--retry-errors", action="store_true",
                    help="回包已存在但被判 error 的，只重解析（不加审稿轮次）")
    ap.add_argument("--verdict", default="", help="写裁定到 grounding 账本：true_error/false_positive/unconfirmed")
    ap.add_argument("--check", default="", help="--verdict 的检查项（如 numeric_context）")
    ap.add_argument("--finding", default="", help="--verdict 的具体发现")
    ap.add_argument("--action", default="none", help="--verdict 的动作：none/revised/pending")
    ap.add_argument("--revisions", type=int, default=0, help="--verdict 的修订次数")
    ap.add_argument("--minutes", default=None, help="--verdict 的人工分钟（未知留空）")
    ap.add_argument("--notes", default="", help="--verdict 备注")
    ap.add_argument("--id", default="", help="--verdict 的目标队列 id")
    ap.add_argument("--batch", default="", help="批次 id（--emit/--ingest/--verdict 用）")
    ap.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS, help="本批最多几次独立审稿（默认 5）")
    ap.add_argument("--reserved-rerun", type=int, default=DEFAULT_RESERVED_RERUN,
                    help="为修订后重审预留的轮次（默认 1）")
    ap.add_argument("--random-slots", type=int, default=DEFAULT_RANDOM_SLOTS,
                    help="随机样本名额（默认 1）")
    ap.add_argument("--per-tag-cap", type=int, default=DEFAULT_PER_TAG_CAP,
                    help="同类风险最多占几个名额（默认 2）")
    ap.add_argument("--seed", type=int, default=None, help="选单随机种子（可复现）")
    ap.add_argument("--limit", type=int, default=0, help="本次最多处理几条（0 = 按预算）")
    ap.add_argument("--queue", default="", help="队列路径（默认 notes/_meta/async_review_queue.jsonl）")
    ap.add_argument("--report", default="", help="汇总报告路径（默认 notes/_reports/async_review_<日期|批次>.md）")
    ap.add_argument("--report-only", action="store_true",
                    help="只按批次账本 + grounding 账本重出报告（补完裁定后刷新用，不动队列）")
    args = ap.parse_args()

    from shared.env import load_env
    load_env(force_obsidian=True)
    from shared import async_review as AR

    queue = args.queue or AR.QUEUE
    if args.queue:
        AR.QUEUE = queue

    stats = AR.stats(queue)
    if args.stats:
        out = dict(stats)
        if args.batch:
            try:
                plan = load_batch(args.batch)
                out["batch"] = {"state": plan.get("state"),
                                "selected": len(plan.get("selected") or []),
                                "emitted": len(plan.get("emitted") or []),
                                "budget": plan.get("budget")}
            except OSError:
                out["batch"] = "not_found"
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    if args.verdict:
        if not args.id:
            print("[ERR] --verdict 需要 --id")
            return 2
        rec = next((r for r in AR.load(queue) if r.get("id") == args.id), None)
        try:
            row = append_verdict(args.batch or "manual", args.id, verdict=args.verdict,
                                 check=args.check, finding=args.finding, action=args.action,
                                 revisions=args.revisions, minutes=args.minutes,
                                 notes=args.notes,
                                 note=(rec or {}).get("note_path_rel") or "",
                                 source=(rec or {}).get("source_path") or "",
                                 note_sha256_before=(rec or {}).get("note_sha256") or "")
        except ValueError as exc:
            print(f"[ERR] {exc}")
            return 2
        print(f"裁定已入账：{json.dumps(row, ensure_ascii=False)}")
        return 0

    all_records = AR.load(queue)
    pending_records = AR.pending(limit=args.limit or 0, queue=queue)

    if args.plan:
        return cmd_plan(pending_records, all_records, rounds=args.rounds,
                        reserved_rerun=args.reserved_rerun, random_slots=args.random_slots,
                        per_tag_cap=args.per_tag_cap, seed=args.seed, limit=args.limit)

    batch = None
    if args.batch:
        try:
            batch = load_batch(args.batch)
        except OSError:
            print(f"[ERR] 找不到批次 {args.batch}（{_rel(BATCH_DIR)}）：先跑 --plan")
            return 2

    if args.report_only:
        if not batch:
            print("[ERR] --report-only 需要 --batch")
            return 2
        report = args.report or os.path.join(
            BASE_DIR, "notes", "_reports", f"async_review_{batch['batch_id']}.md")
        path = write_report(report, AR.stats(queue), AR.load(queue), batch=batch,
                            counters=batch.get("counters") or {})
        print(f"报告 → {_rel(path)}")
        return 0

    if args.emit:
        if batch:
            rc = cmd_emit(args.limit, [], batch=batch, all_records=all_records)
        else:
            rc = cmd_emit(args.limit or 5, pending_records)
    elif args.ingest:
        if batch:
            ids = [s.get("id") for s in batch.get("selected") or []]
            by_id = {r.get("id"): r for r in all_records}
            records = [by_id[i] for i in ids if i in by_id]
        else:
            records = AR.pending(limit=0, queue=queue)
        counters = cmd_ingest(records, batch=batch, retry_errors=args.retry_errors)
        if not records:
            print(f"队列没有待审条目（{_rel(queue)}）：{json.dumps(stats, ensure_ascii=False)}")
            return 0
        rc = 0
        report = args.report or os.path.join(
            BASE_DIR, "notes", "_reports",
            f"async_review_{batch['batch_id'] if batch else time.strftime('%Y%m%d')}.md")
        path = write_report(report, AR.stats(queue), AR.load(queue), batch=batch,
                            counters=counters)
        print(f"报告 → {_rel(path)}")
        return rc
    elif args.run or args.dry_run:
        rc = cmd_run(len(pending_records), pending_records, args.dry_run)
    else:
        # 默认：只看一眼，不动队列（防误跑花钱）
        print(f"待审 {len(pending_records)} 条（队列共 {stats.get('pending', 0)}）；"
              f"用 --plan / --emit / --ingest / --run 之一执行。")
        for r in pending_records[:args.limit or 10]:
            print(f"  · {r.get('id')}  {(r.get('title') or '')[:30]}  "
                  f"{r.get('note_type')}  {', '.join(r.get('reasons') or [])[:60] or '抽样'}")
        return 0

    if batch:
        report = args.report or os.path.join(
            BASE_DIR, "notes", "_reports", f"async_review_{batch['batch_id']}.md")
        path = write_report(report, AR.stats(queue), AR.load(queue), batch=batch,
                            counters=batch.get("counters") or {})
        print(f"报告 → {_rel(path)}")
    elif args.emit:
        report = args.report or os.path.join(
            BASE_DIR, "notes", "_reports", f"async_review_{time.strftime('%Y%m%d')}.md")
        path = write_report(report, AR.stats(queue), AR.load(queue))
        print(f"报告 → {_rel(path)}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
