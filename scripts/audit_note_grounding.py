#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scripts/audit_note_grounding.py — 两类语义层只读审计（不拦盘、不改稿）

对应 2026-10-19 留出轮暴露的两个检查盲区（见
`notes/_meta/eval/20261019-holdout-5/ROOT_CAUSE_REGRESSIONS.md`）：

1. **分析层数字可溯源**：`笔记者分析 / 延伸思考 / 自测 / 讲出来` 里的数字
   （含「三年」这类中文数字）能否在源里指认；列表编号、问句、标了估算/推断的跳过。
2. **表格数字归属**：多人源里，表格行的主体是否与源内讲者段落的术语/数字一致。

用法：
    python scripts/audit_note_grounding.py --note <稿.md> --source <源.md>
    python scripts/audit_note_grounding.py --dir notes/_meta/eval/<round>
        （读 eval_set.json 的 source_path + notes/<eid>.md）
    python scripts/audit_note_grounding.py --manifest <list.json>
        （生产批次用：[{"id","note","source"}]；相对路径按清单目录解析）
    python scripts/audit_note_grounding.py --recent-hours 6
        （跑完监控后扫最近 N 小时落盘的库内笔记；按 note mtime）
    python scripts/audit_note_grounding.py --dir <round> --report <out.md> --json <out.json>

退出码：0 = 正常（有发现也是 0，因为这是**报告项**）；2 = 输入缺失。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared.note_grounding import audit  # noqa: E402


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _one(eid: str, note_path: str, source_path: str) -> dict:
    res = audit(_read(note_path), _read(source_path))
    res.update({"eval_id": eid, "note": note_path, "source": source_path})
    return res


def _report(items: list) -> str:
    L = ["# 语义层接地审计（只读）", "",
         "> 报告项，不拦盘。分析层数字 = 源里找不到、且未标估算/推断的数字（含中文数字）；",
         "> 表格归属 = 表格行主体与源内讲者段落的术语不一致。", "",
         "| # | 分析层数字 | 表格归属 | 明细 |", "|---|---|---|---|"]
    for r in items:
        an = r["analysis_numbers"]
        at = r["table_attribution"]
        detail = []
        for x in an[:3]:
            detail.append(f"分析数字 `{x['number']}`（{x['section']}）")
        for x in at[:3]:
            detail.append(f"归属 `{x['subject']}` → `{x['term']}`（应为 {'/'.join(x['owners'])}）")
        L.append(f"| `{r['eval_id']}` | {len(an)} | {len(at)} | {'；'.join(detail) or '—'} |")
    L += ["", "## 明细", ""]
    for r in items:
        if not r["analysis_numbers"] and not r["table_attribution"]:
            continue
        L += [f"### {r['eval_id']}", ""]
        for x in r["analysis_numbers"]:
            L.append(f"- 分析层数字 `{x['number']}`｜{x['section']}｜…{x['context']}…")
        for x in r["table_attribution"]:
            L.append(f"- 表格归属 `{x['subject']}` → `{x['term']}`｜源内出现在 "
                     f"{'/'.join(x['owners'])}｜{x['cell']}")
        L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="分析层数字 + 表格归属（只读）")
    ap.add_argument("--note")
    ap.add_argument("--source")
    ap.add_argument("--dir", help="对照轮目录（含 eval_set.json 与 notes/）")
    ap.add_argument("--manifest", help='批量清单 JSON：[{"id","note","source"}] 或 {"items":[...]}；'
                                      "相对路径按清单所在目录解析")
    ap.add_argument("--recent-hours", type=float, default=0,
                    help="扫最近 N 小时内落盘的库内笔记（按 note mtime；默认 0=不用）")
    ap.add_argument("--json", default="")
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    items = []
    if args.dir:
        ev = os.path.join(args.dir, "eval_set.json")
        if not os.path.exists(ev):
            print(f"[ERR] 找不到 {ev}")
            return 2
        rows = json.load(open(ev, encoding="utf-8")).get("items", [])
        for r in sorted(rows, key=lambda x: x.get("eval_id", "")):
            eid = r.get("eval_id") or ""
            note = os.path.join(args.dir, "notes", f"{eid}.md")
            if not os.path.exists(note) or not r.get("source_path"):
                continue
            items.append(_one(eid, note, r["source_path"]))
    elif args.manifest:
        base = os.path.dirname(os.path.abspath(args.manifest))
        data = json.load(open(args.manifest, encoding="utf-8"))
        rows = data.get("items", []) if isinstance(data, dict) else data
        for i, r in enumerate(rows, 1):
            note = r.get("note") or ""
            source = r.get("source") or ""
            if not note or not source:
                continue

            def _abs(p):
                if os.path.isabs(p):
                    return p
                for base_dir in (base, ROOT):
                    cand = os.path.join(base_dir, p)
                    if os.path.exists(cand):
                        return cand
                return os.path.join(base, p)

            note, source = _abs(note), _abs(source)
            if not (os.path.exists(note) and os.path.exists(source)):
                print(f"[WARN] 跳过第 {i} 条（文件不存在）：{note} / {source}")
                continue
            items.append(_one(r.get("id") or os.path.basename(note)[:-3], note, source))
    elif args.recent_hours and args.recent_hours > 0:
        from scripts.audit_gate_signals import build_corpus
        cutoff = time.time() - args.recent_hours * 3600
        fresh = []
        for c in build_corpus("all"):
            try:
                mt = os.path.getmtime(c["note_path"])
            except OSError:
                continue
            if mt >= cutoff:
                fresh.append((mt, c))
        for mt, c in sorted(fresh, key=lambda x: x[0], reverse=True):
            items.append(_one(os.path.basename(c["note_path"])[:-3], c["note_path"], c["source_path"]))
    elif args.note and args.source:
        items.append(_one(os.path.basename(args.note)[:-3], args.note, args.source))
    else:
        print("[ERR] 需要 --note/--source、--dir 或 --manifest")
        return 2

    for r in items:
        print(f"{r['eval_id']}: 分析层数字 {len(r['analysis_numbers'])}｜表格归属 "
              f"{len(r['table_attribution'])}")
        for x in r["analysis_numbers"]:
            print(f"  AN {x['number']}（{x['section']}）{x['context'][:80]}")
        for x in r["table_attribution"]:
            print(f"  AT {x['subject']} → {x['term']}（源内 {'/'.join(x['owners'])}）")

    if args.json:
        json.dump(items, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"明细 → {args.json}")
    if args.report:
        open(args.report, "w", encoding="utf-8").write(_report(items))
        print(f"报告 → {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
