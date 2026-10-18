"""scripts/run_async_review.py — 消费「落盘后异步审核队列」（T7 ②）

`shared/async_review.py` 只负责入队（免费）；本脚本负责**花钱那一步**，所以必须显式跑。

## 两种消费方式（按本项目的真实运行方式选）

本项目已废弃外部 AI Provider（`.env` `FORCE_AGENT_MODE=1`，所有生成交给执行模型），
所以默认走 **Agent 模式**：

| 模式 | 命令 | 干什么 |
|---|---|---|
| 看队列 | `--stats` / `--dry-run` | 只读：待审条数、体积、预估调用数 |
| 派单 | `--emit --limit 5` | 把待审笔记出成派单包（`_tmp/async_review/in/`），交子 Agent 审 |
| 回收 | `--ingest` | 读 `_tmp/async_review/out/<id>.json`，验 hash + 按 `parse_quality_gate` 收口 |
| 直调 | `--run --limit 5` | 有外部 provider（`FORCE_AGENT_MODE=0`）时直接跑 `review_fulltext` 全流程 |

Agent 模式刻意**不重写审核逻辑**：派单包里的口径就是 `templates.QUALITY_GATE_PROMPT`
＋既有 JSON 契约；回收时用同一个 `parse_quality_gate` 判定（`coverage=full` 才认、
P1 一票否决、`reviewer_role` 不得是 author/self_check）——**判据只有一份，不因异步而放松**。

## hash 是这条链路的命门

- 派单包记下 `note_sha256`；**回收时重新算一遍**，对不上就标 `stale` 并丢弃结论
  （后台审的时候稿可能已经被重做/手改过，旧结论不得冒充当前稿 —— PLAN-20261018 §10 第 4 项）。
- 派单包本身只含「当时的稿」与「当时的源」，两者都写进包内，回收时不再回读文件内容，
  避免「审的是 A、记到 B」。

## 产物

- 队列状态：`notes/_meta/async_review_queue.jsonl`（`pending → passed/failed/stale/skipped/error`）
- 审核全文：`notes/_meta/async_reviews/<id>.json`
- 汇总报告：`notes/_reports/async_review_<日期>.md`（`--report` 可改）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

IN_DIR = os.path.join(BASE_DIR, "_tmp", "async_review", "in")
OUT_DIR = os.path.join(BASE_DIR, "_tmp", "async_review", "out")


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


# ---------------------------------------------------------------- 派单（Agent 模式）

DISPATCH_TMPL = """# 异步审核派单 · {id}

> 这是**落盘之后**的独立复审，不决定这篇能否入库（已入库）。你可以判 fail —— 那正是它的价值。

## 你的角色

`reviewer_role = independent_review_call`：**独立会话审者**。不得以「作者这样说」为通过理由，
不得因为稿子读起来顺就放过；只对「源支持不支持」下判断。

## 记录（回填时校验，勿改）

- id: `{id}`
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

写到 `_tmp/async_review/out/{id}.json`，然后跑 `python scripts/run_async_review.py --ingest` 收口。
"""


def cmd_emit(limit: int, records: list) -> int:
    os.makedirs(IN_DIR, exist_ok=True)
    made, skipped = 0, []
    for rec in records:
        note_path = rec.get("note_path") or ""
        note = _read(note_path)
        if not note:
            skipped.append((rec.get("id"), "note_missing"))
            continue
        ok, why = _verify_fresh(rec, note)
        if not ok:
            from shared import async_review as AR
            AR.update(rec["id"], status="stale", result={"reason": why})
            skipped.append((rec.get("id"), why))
            continue
        source = _read(rec.get("source_path") or "")
        if not source:
            from shared import async_review as AR
            AR.update(rec["id"], status="skipped", result={"reason": "source_missing"})
            skipped.append((rec.get("id"), "source_missing"))
            continue
        body = DISPATCH_TMPL.format(
            id=rec.get("id"), note_path=note_path, note_type=rec.get("note_type") or "unknown",
            note_sha256=rec.get("note_sha256"), source_sha256=rec.get("source_sha256"),
            reasons="、".join(rec.get("reasons") or []) or "抽样", gate_prompt=_gate_prompt(),
            claim_block=_claim_block(note, source), note=note, source=source)
        path = os.path.join(IN_DIR, f"{rec.get('id')}.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        made += 1
        print(f"  ➜ 派单 {rec.get('id')}  {_rel(path)}  "
              f"(稿 {len(note)} / 源 {len(source)} 字)")
    print(f"\n派单 {made} 份 → {_rel(IN_DIR)}")
    for rid, why in skipped:
        print(f"  · 跳过 {rid}：{why}")
    if made:
        print("\n下一步：子 Agent 读派单包 → 写 JSON 到 _tmp/async_review/out/<id>.json → "
              "python scripts/run_async_review.py --ingest")
    # stale / skipped 的条目状态已就地更新，不算执行失败：没有可派单的就正常收工
    return 0


# ---------------------------------------------------------------- 回收

def cmd_ingest(records: list) -> int:
    from prompts.templates import parse_quality_gate
    from shared import async_review as AR

    done = passed = failed = 0
    missing = []
    for rec in records:
        rid = rec.get("id")
        payload = os.path.join(OUT_DIR, f"{rid}.json")
        if not os.path.exists(payload):
            missing.append(rid)
            continue
        note = _read(rec.get("note_path") or "")
        if not note:
            AR.update(rid, status="error", result={"reason": "note_missing"})
            done += 1
            continue
        ok, why = _verify_fresh(rec, note)
        if not ok:
            AR.update(rid, status="stale", result={"reason": why})
            print(f"  ⏭ {rid} 稿已变（{why}），结论丢弃")
            done += 1
            continue
        raw = _read(payload)
        parsed = parse_quality_gate(raw)
        if parsed.get("status") in ("needs_review",) or not isinstance(parsed.get("score"), int):
            AR.update(rid, status="error",
                      result={"reason": "审核结果不合格", "issues": parsed.get("issues", [])[:8]})
            print(f"  ⚠️ {rid} 审核结果不合规：{parsed.get('issues', [])[:1]}")
            done += 1
            continue
        status = "passed" if parsed["passed"] else "failed"
        summary = {"score": parsed["score"], "status": parsed["status"],
                   "issues": parsed["issues"][:8], "findings": len(parsed.get("findings") or [])}
        AR.update(rid, status=status, result=summary)
        AR.save_result(rec, {"parsed": parsed, "raw": raw, "record": rec})
        done += 1
        passed += status == "passed"
        failed += status == "failed"
        print(f"  {'✅' if status == 'passed' else '❌'} {rid} {parsed['score']}/100 "
              f"{(rec.get('title') or '')[:26]}")
    print(f"\n回收 {done} 条：passed {passed} / failed {failed}；仍无结果 {len(missing)}")
    if missing:
        print("  " + "、".join(missing[:10]) + ("…" if len(missing) > 10 else ""))
    return 0


# ---------------------------------------------------------------- 直调（有 provider 时）

def cmd_run(limit: int, records: list, dry_run: bool) -> int:
    from shared import async_review as AR

    if os.environ.get("FORCE_AGENT_MODE", "1") == "1":
        print("[ERR] 当前 FORCE_AGENT_MODE=1（无外部 provider），无法直调。")
        print("      改用：--emit 出派单包 → 子 Agent 审 → --ingest 回收。")
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
        return 0 if not dry_run else 0

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

def write_report(path: str, stats: dict, rows: list) -> str:
    L = ["# 落盘后异步审核（T7 ②）· 汇总", ""]
    A = L.append
    A(f"- 生成：`python scripts/run_async_review.py --ingest`（{time.strftime('%Y-%m-%d %H:%M')}）")
    A(f"- 队列：`notes/_meta/async_review_queue.jsonl`")
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
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="落盘后异步审核队列（消费端）")
    ap.add_argument("--stats", action="store_true", help="只看队列状态分布")
    ap.add_argument("--emit", action="store_true", help="出派单包（Agent 模式）")
    ap.add_argument("--ingest", action="store_true", help="回收子 Agent 结果并更新队列")
    ap.add_argument("--run", action="store_true", help="直调 provider 跑 review_fulltext")
    ap.add_argument("--dry-run", action="store_true", help="只估算，不调用、不改队列")
    ap.add_argument("--limit", type=int, default=5, help="本次最多处理几条（默认 5）")
    ap.add_argument("--queue", default="", help="队列路径（默认 notes/_meta/async_review_queue.jsonl）")
    ap.add_argument("--report", default="", help="汇总报告路径（默认 notes/_reports/async_review_<日期>.md）")
    args = ap.parse_args()

    from shared.env import load_env
    load_env(force_obsidian=True)
    from shared import async_review as AR

    queue = args.queue or AR.QUEUE
    if args.queue:
        AR.QUEUE = queue

    stats = AR.stats(queue)
    if args.stats:
        print(json.dumps(stats, ensure_ascii=False, indent=1))
        return 0

    records = AR.pending(limit=args.limit, queue=queue)
    if not records:
        print(f"队列没有待审条目（{_rel(queue)}）：{json.dumps(stats, ensure_ascii=False)}")
        return 0

    if args.run or args.dry_run:
        rc = cmd_run(len(records), records, args.dry_run)
    elif args.emit:
        rc = cmd_emit(len(records), records)
    elif args.ingest:
        all_pending = AR.pending(limit=0, queue=queue)
        rc = cmd_ingest(all_pending)
    else:
        # 默认：只看一眼，不动队列（防误跑花钱）
        print(f"待审 {len(records)} 条（队列共 {stats.get('pending', 0)}）；"
              f"用 --emit / --ingest / --run 之一执行。")
        for r in records:
            print(f"  · {r.get('id')}  {(r.get('title') or '')[:30]}  "
                  f"{r.get('note_type')}  {', '.join(r.get('reasons') or []) or '抽样'}")
        return 0

    report = args.report or os.path.join(
        BASE_DIR, "notes", "_reports", f"async_review_{time.strftime('%Y%m%d')}.md")
    path = write_report(report, AR.stats(queue), AR.load(queue))
    print(f"报告 → {_rel(path)}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
