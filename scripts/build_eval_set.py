"""scripts/build_eval_set.py — 固定评估集（T3）构建器（只读生产数据）

`docs/plans/PLAN-20261018-knowledge-layer.md` §6 **A1**：后续所有 A 组改动都要靠它回答
「改之前是多少、改之后是多少」。缺省 determinism：种子固定，换机/换人抽样不漂移。

用法:
    python scripts/build_eval_set.py                         # 默认抽 30 篇（每桶 6）
    python scripts/build_eval_set.py --per-bucket 6 --seed 20261018
    python scripts/build_eval_set.py --out notes/_meta/eval/20261018-knowledge-layer

产物（全部在本地私有目录 `notes/_meta/eval/`，不入库、不写 vault）:
    eval_set.json   冻结的样本清单（路径 + 双 hash + 四类判据基线）
    report.md       分层配额 + 每桶基线概览（人工核对用）
    forms/A01.md…   人工必留清单表单（**待用户填写**）
    README.md       填写口径

填写后算保留率:
    python scripts/score_eval_set.py --dir <同一个 out 目录>

注意：本脚本**不写 **vault、不改登记表、不改笔记；重跑会用同种子产出同一批样本，
务必先把上一轮的 `forms/` 备份（或用 `--out` 另开目录）再重建。
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

DEFAULT_OUT = os.path.join(BASE_DIR, "notes", "_meta", "eval", "20261018-knowledge-layer")

README_TEXT = """# 固定评估集（T3）· 填写口径

来源：`docs/plans/PLAN-20261018-knowledge-layer.md` §6 A1。目的：给「信息保留度」
一个稳定基准，使后续改动能回答「改之前多少 → 改之后多少」。

## 怎么填

对每篇 `forms/Axx.md`：

1. 打开表单里「源」指向的文件（先只读源，不要读笔记）。
2. 在 `<!-- MUST_KEEP -->` 和 `<!-- /MUST_KEEP -->` 之间，逐条写下**丢了不行**的核心事实与论点
   （数字口径、前提条件、反例、资源入口）。一条一行，行首 `- [ ]`。
3. 回查笔记：笔记里有这条的，改成 `- [x]`。勾选状态就是判定结果。

## 边界（沿用 PLAN-20261014 §10.6）

- **不判类型**、不通读全篇、不打分。清单只管「重要信息有没有丢」。
- 建议每篇 **5~10 条**；少于 {min} 条的表单不参与统计（防止标 1 条就 100%）。
- 表单下半部「机械候选」只是核对起点（源里有、笔记里字面未命中），**不是结论**。

## 算分

    python scripts/score_eval_set.py --dir {out}

目标保留率 {target:.0%}（`PLAN-20261014` §10.6）。统计维度：整体 / 按源长桶 / 按笔记类型。
"""


def _measure_all(which: str):
    """构建源/笔记配对语料并测量字数（不跑四类判据：1298 条全跑 shingle 太贵）。"""
    from scripts import audit_gate_signals as A
    from shared.eval_set import measure

    corpus = A.build_corpus(which)
    return [measure(it) for it in corpus]


def _write_report(path: str, plan: dict, items: list, pool_counts: dict) -> None:
    from shared.eval_set import BUCKETS

    L = []
    P = L.append
    P("# 固定评估集（T3）· 抽样与基线")
    P("")
    P("- 生成：`python scripts/build_eval_set.py`")
    P("- 抽样：**源长分桶**（等额，保每层分辨率）+ 桶内**类型轮转**（铺开模板类型）")
    P(f"- 样本 **{len(items)}** 篇（只读：不写 vault、不改笔记）")
    P("")
    P("## 一、配额")
    P("")
    P("| 源长桶 | 全库可用 | 抽 | 类型分布 |")
    P("|---|---|---|---|")
    for label, _lo, _hi in BUCKETS:
        sub = [i for i in items if i["bucket"] == label]
        tc = collections.Counter(i["note_type"] for i in sub)
        dist = "、".join(f"{k} {v}" for k, v in tc.most_common())
        P(f"| `{label}` | {pool_counts.get(label, 0)} | {plan.get(label, 0)} | {dist or '—'} |")
    P("")
    P("## 二、基线（既有四类判据，回答「改之前是多少」）")
    P("")
    P("| # | 桶 | 类型 | 源/稿字数 | 比值 | 锚点召回 | 形态 | 结构缺失 | 照搬 |")
    P("|---|---|---|---|---|---|---|---|---|")
    for i in items:
        a = i["anchors"]
        rec = f"{a['recall']:.0%} ({a['matched']}/{a['total']})" if a.get("recall") is not None \
            else f"不适用({a['total']})"
        P(f"| `{i['eval_id']}` | {i['bucket']} | `{i['note_type']}` | "
          f"{i['source_words']}/{i['note_words']} | {i['ratio']} | {rec} | "
          f"{'、'.join(i['form']['signals']) or '—'} | "
          f"{'、'.join(i['structure']['missing']) or '—'} | {i['verbatim']} |")
    P("")
    P("> 锚点「不适用」= 源锚点数 < MIN_ANCHORS —— 这是 63% 语料的真实处境，")
    P("> 也正是必须靠人工必留清单才能度量的部分。")
    P("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="固定评估集（T3）构建器")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"产物目录（默认 {DEFAULT_OUT}）")
    ap.add_argument("--corpus", default="all", choices=["all", "batch", "xiapeng"])
    ap.add_argument("--per-bucket", type=int, default=6, help="每源长桶抽几条（默认 6 → 5 桶 30 条）")
    ap.add_argument("--seed", type=int, default=20261018, help="抽样种子，固定值保证复现")
    args = ap.parse_args()

    from shared.env import load_env
    load_env()

    from shared import eval_set as ES

    print(f"[1/4] 构建语料（{args.corpus}）并测量源/稿字数…")
    rows = _measure_all(args.corpus)
    usable = [r for r in rows if r["bucket"]]
    pool_counts = collections.Counter(r["bucket"] for r in usable)
    print(f"      语料 {len(rows)} 条，可分层 {len(usable)} 条，分布 {dict(pool_counts)}")

    print("[2/4] 分层抽样…")
    picked, plan = ES.stratify_sample(usable, per_bucket=args.per_bucket, seed=args.seed)
    print(f"      配额 {plan} → 实抽 {len(picked)} 条")
    shortage = ES.allocate(usable, per_bucket=args.per_bucket)[1]
    if shortage:
        print(f"      ⚠️ 缺额（桶内样本不足，**不做富桶补抽**）：{shortage}")

    print("[3/4] 跑既有判据基线（仅抽中样本）…")
    items = []
    for row in picked:
        try:
            items.append(ES.audit_row(row))
        except Exception as exc:                      # 单篇失败不影响整体，但要留痕
            print(f"      ⚠️ {row.get('title', '')[:30]} 基线失败：{exc}")
    if not items:
        print("[ERR] 没有任何样本跑成功")
        return 1

    os.makedirs(args.out, exist_ok=True)
    forms = os.path.join(args.out, "forms")
    os.makedirs(forms, exist_ok=True)
    for row in items:
        with open(os.path.join(forms, f"{row['eval_id']}.md"), "w", encoding="utf-8") as fh:
            fh.write(ES.render_form(row))

    payload = {
        "_note": "固定评估集（T3）。只读产物：不写 vault、不改登记表。",
        "seed": args.seed,
        "per_bucket": args.per_bucket,
        "corpus": args.corpus,
        "corpus_usable": len(usable),
        "pool_counts": dict(pool_counts),
        "plan": plan,
        "retention_target": ES.RETENTION_TARGET,
        "min_items_per_form": ES.MIN_ITEMS_PER_FORM,
        "items": items,
    }
    json.dump(payload, open(os.path.join(args.out, "eval_set.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    _write_report(os.path.join(args.out, "report.md"), plan, items, pool_counts)
    with open(os.path.join(args.out, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(README_TEXT.format(min=ES.MIN_ITEMS_PER_FORM, out=args.out,
                                    target=ES.RETENTION_TARGET))
    print(f"[4/4] 产物 → {args.out}")
    print(f"      eval_set.json / report.md / forms/（{len(items)} 张待填）")
    print(f"\n下一步：阅读源、填写 forms/ 里的「必留清单」，然后跑：")
    print(f"      python scripts/score_eval_set.py --dir {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
