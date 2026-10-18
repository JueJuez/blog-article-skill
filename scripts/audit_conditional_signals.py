# -*- coding: utf-8 -*-
"""A6 / T9 条件必备结构的**信号离线测量**（只读，不改生产）

PLAN-20261018 §6 A6 / §12.3 #6 要求：机械信号升级为硬拦前，必须先看分布与误报率。
本脚本拿存量「源/笔记」配对（复用 `scripts/audit_gate_signals.build_corpus`）跑两件事：

1. **信号命中率**：每个信号在多少篇源上触发（触发太普遍 = 等于无条件必备，没有区分度）；
2. **误报率**：信号触发了、但该小节在笔记里**不存在** —— 要么信号判错（误报），要么该笔记
   确实漏了这一节（真缺陷）。两者无法机械分开，所以完整输出**篇目清单**供人工/A1 口径核对。

用法：
    python scripts/audit_conditional_signals.py --corpus all --limit 0
产物：`notes/_meta/eval/conditional_signal_audit.md`（只读报告，不入库）。
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


def main() -> int:
    ap = argparse.ArgumentParser(description="A6 条件必备信号离线测量（只读）")
    ap.add_argument("--corpus", default="all", choices=["all", "batch", "xiapeng"])
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 篇（0 = 全量）")
    ap.add_argument("--out", default=os.path.join(
        ROOT, "notes", "_meta", "eval", "conditional_signal_audit.md"))
    args = ap.parse_args()

    from audit_gate_signals import build_corpus
    from prompts import content_signals as CS
    from prompts.templates import SECTION_CONTRACTS

    corpus = build_corpus(args.corpus)
    if args.limit:
        corpus = corpus[:args.limit]
    if not corpus:
        print("语料为空（检查 OBSIDIAN_VAULT_PATH / summary_registry）")
        return 2

    # 每个 (类型, 小节, 信号) 三元组的命中与「触发但笔记里没有」
    stat = collections.defaultdict(lambda: {"fired": 0, "fired_missing": 0, "samples": []})
    per_signal = collections.Counter()
    read_err = 0
    for item in corpus:
        try:
            source = open(item["source_path"], encoding="utf-8").read()
            note = open(item["note_path"], encoding="utf-8").read()
        except (OSError, UnicodeError):
            read_err += 1
            continue
        nt = item.get("note_type") or ""
        spec = (SECTION_CONTRACTS.get(nt) or {}).get("conditional") or {}
        for section, sig in spec.items():
            res = CS.CONDITIONAL_SIGNALS[sig](source)
            if not res.get("fired"):
                continue
            key = (nt or "(未标类型)", section, sig)
            stat[key]["fired"] += 1
            per_signal[sig] += 1
            keys = CS.SECTION_ALIAS.get(section) or []
            if not any(k in note for k in keys):
                stat[key]["fired_missing"] += 1
                if len(stat[key]["samples"]) < 5:
                    stat[key]["samples"].append(item.get("title") or os.path.basename(item["note_path"]))

    L = [f"# A6 条件必备信号离线测量（corpus={args.corpus}，{len(corpus)} 篇，读失败 {read_err}）", "",
         "口径：`fired` ＝ 源侧信号触发；`fired_missing` ＝ 触发但笔记里没有该小节（**误报**与**真漏**"
         "混在一起，需人工核对 samples）。信号若几乎全篇触发，说明它没有区分度、不该当条件必备。", "",
         "## 各信号触发篇数", "", "| 信号 | 触发篇数 | 占语料 |", "|---|---|---|"]
    for sig, n in per_signal.most_common():
        L.append(f"| {sig} | {n} | {n / len(corpus):.1%} |")
    L += ["", "## 按 (类型, 小节, 信号) 三元组", "",
          "| 类型 | 小节 | 信号 | 触发 | 触发但缺该节 | 缺节率 |", "|---|---|---|---|---|---|"]
    for (nt, section, sig), v in sorted(stat.items()):
        rate = v["fired_missing"] / v["fired"]
        L.append(f"| {nt} | {section} | {sig} | {v['fired']} | {v['fired_missing']} | {rate:.1%} |")
    L += ["", "## 缺节样本（每个三元组最多 5 条，供人工核对是误报还是真漏）", ""]
    for (nt, section, sig), v in sorted(stat.items()):
        if not v["samples"]:
            continue
        L.append(f"- **{nt} / {section} / {sig}**：")
        L += [f"  - {t}" for t in v["samples"]]

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print(f"语料 {len(corpus)} 篇；信号触发统计：{dict(per_signal)}")
    print("报告 →", args.out)
    for (nt, section, sig), v in sorted(stat.items()):
        print(f"  {nt}/{section}/{sig}: fired={v['fired']} missing={v['fired_missing']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
