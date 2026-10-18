"""scripts/score_eval_set.py — 按人工必留清单算保留率（T3 配套判分）

用法:
    python scripts/score_eval_set.py --dir notes/_meta/eval/20261018-knowledge-layer
    python scripts/score_eval_set.py --dir <dir> --json out.json --report out.md

口径（`PLAN-20261014` §10.6 → `PLAN-20261018-knowledge-layer` §6 A1）：
保留率 = 笔记里留住的条数 / 人工必留清单条数，目标 ≥ 90%，且无曲解/无编造。
**分数只来自人工勾选**，脚本不做任何语义猜测 —— 锚点召回等机械指标在报告里作为对照列出现，
不能替代也不会被拿来凑数。

不足 MIN_ITEMS_PER_FORM 条的表单单独列「未填」，不进统计（防「标 1 条就 100%」）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def _report_md(res: dict, dir_path: str) -> str:
    agg = res["aggregate"]
    L = []
    P = L.append
    P("# 保留度评分报告（固定评估集 T3）")
    P("")
    P(f"- 目录：`{dir_path}`")
    P(f"- 已填表单 **{agg['filled_forms']}** / 未填 {agg['pending_forms']}")
    P(f"- 清单条目 {agg['total_items']} 条，留住 {agg['kept_items']} 条")
    P(f"- **整体保留率 {agg['mean_retention']}**（最低 {agg['min_retention']}），"
      f"目标 ≥ {agg['retention_target']:.0%}")
    P(f"- 低于目标的表单：{agg['forms_below_target']} 篇")
    P("")
    P("## 一、按源长桶")
    P("")
    P("| 桶 | n | 平均保留率 | 最低 | 低于目标 |")
    P("|---|---|---|---|---|")
    for k, v in agg["by_bucket"].items():
        P(f"| `{k}` | {v['n']} | {v['mean_retention']} | {v['min_retention']} | {v['below_target']} |")
    P("")
    P("## 二、按笔记类型")
    P("")
    P("| 类型 | n | 平均保留率 | 最低 | 低于目标 |")
    P("|---|---|---|---|---|")
    for k, v in agg["by_type"].items():
        P(f"| `{k}` | {v['n']} | {v['mean_retention']} | {v['min_retention']} | {v['below_target']} |")
    P("")
    P("## 三、逐篇明细")
    P("")
    P("| # | 桶 | 类型 | 源/稿 | 保留率 | 锚点召回 | 丢失条目 |")
    P("|---|---|---|---|---|---|---|")
    for r in res["items"]:
        rec = f"{r['anchor_recall']:.0%}" if isinstance(r["anchor_recall"], float) else "不适用"
        lost = "；".join(r["missed_texts"][:3]) or "—"
        if len(r["missed_texts"]) > 3:
            lost += f" …(+{len(r['missed_texts']) - 3})"
        P(f"| `{r['eval_id']}` | {r['bucket']} | `{r['note_type']}` | "
          f"{r['source_words']}/{r['note_words']} | {r['retention']:.0%} | {rec} | {lost[:120]} |")
    P("")
    if res["pending"]:
        P("## 四、未填 / 未达统计门槛的表单")
        P("")
        P("| # | 文件 | 已列条数 |")
        P("|---|---|---|")
        for r in res["pending"]:
            P(f"| `{r['eval_id']}` | `{r['file']}` | {r['total']} |")
        P("")
    P("> 锚点召回与人工保留率是两回事：前者机械可判但只覆盖约 37% 语料，")
    P("> 后者是人判的高层信息清单。两者不一致时**以人工清单为准**。")
    P("")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="固定评估集保留率判分")
    ap.add_argument("--dir", required=True, help="build_eval_set.py 的产物目录")
    ap.add_argument("--json", default="", help="写出明细 JSON")
    ap.add_argument("--report", default="", help="写出 Markdown 报告")
    args = ap.parse_args()

    from shared import eval_set as ES

    eval_json = os.path.join(args.dir, "eval_set.json")
    if not os.path.exists(eval_json):
        print(f"[ERR] 找不到 {eval_json}（先跑 build_eval_set.py）")
        return 2
    forms = os.path.join(args.dir, "forms")
    if not os.path.isdir(forms):
        print(f"[ERR] 找不到表单目录 {forms}")
        return 2

    rows_by_id = ES.load_rows(eval_json)
    res = ES.score_forms(forms, rows_by_id)
    agg = res["aggregate"]

    print(f"已填 {agg['filled_forms']} 篇 / 未填 {agg['pending_forms']} 篇")
    if not agg["filled_forms"]:
        print("还没有填好的表单：每张表单至少要标 "
              f"{ES.MIN_ITEMS_PER_FORM} 条才会进入统计。")
        return 0
    print(f"清单 {agg['total_items']} 条，留住 {agg['kept_items']} 条")
    print(f"整体保留率 {agg['mean_retention']}（最低 {agg['min_retention']}，目标 "
          f"≥ {agg['retention_target']:.0%}）")
    print(f"低于目标的表单：{agg['forms_below_target']} 篇")
    print("\n按源长桶：")
    for k, v in agg["by_bucket"].items():
        print(f"  {k:<8} n={v['n']:<3} 平均 {v['mean_retention']}  最低 {v['min_retention']}"
              f"  低于目标 {v['below_target']}")
    print("\n痛点 TOP5（保留率最低）：")
    for r in sorted(res["items"], key=lambda r: (r["retention"] is None, r["retention"]))[:5]:
        print(f"  {r['eval_id']} [{r['bucket']}/{r['note_type']}] {r['retention']:.0%}"
              f"  丢 {len(r['missed_texts'])} 条")

    if args.json:
        json.dump(res, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n明细 → {args.json}")
    if args.report:
        open(args.report, "w", encoding="utf-8").write(_report_md(res, args.dir))
        print(f"报告 → {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
