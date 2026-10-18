"""scripts/check_resum_necessity.py — 重做前的必要性判断（T13，T8 前置）

来源：`docs/plans/PLAN-20261018-knowledge-layer.md` §6 A2。
Brain 的 Update 阶段写着「也可以选择不改」：**先判该不该改，再谈怎么改**。
现状是直接从登记表演算一批、无差别重做 —— 风险是「重做稿比原稿差且无法回退」（T8 要解决的）。
把「该不该改」这层先补上，T8 才有意义。

判断只用**既有判据**，零新增口径（`shared.note_audit.necessity_of`）：
- `keep` —— 在生产本来会拦的那一档上没有缺陷 → **跳过重做**（选择不改）
- `redo` —— 有确定性内容缺陷（锚点召回/结构缺失/照搬/证据缺陷）→ 这才是重做要修的东西
- 形态信号（超长句/逗号密度/相邻段重复）是软抽检，**不构成重做理由**

用法:
    # 对一份重做候选队列做判断（build_resummarize_queue.py 的产物）
    python scripts/check_resum_necessity.py --queue notes/_meta/resummarize_queue.json
    # 只看前 N 条（长队列先冒烟）
    python scripts/check_resum_necessity.py --queue <队列> --limit 60
    # 单片现查
    python scripts/check_resum_necessity.py --note <笔记.md> --source <源.md> --note-type structured

产物（默认写到队列同目录）:
    <queue>.necessity.json   每条的判定与理由（可直接喂给 build_resum_batches.py --queue）
    necessity_report.md      keep/redo 分布与理由分布

⚠️ 只读：不写 vault、不改登记、不删队列条目。**判定不等于执行**——跳过还是执行，决定权在人。
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


def load_items(args) -> list:
    """队列文件（{"items":[...]}）或单片 --note/--source。"""
    if args.queue:
        data = json.load(open(args.queue, encoding="utf-8"))
        items = data["items"] if isinstance(data, dict) else data
        return items[: args.limit] if args.limit else items
    if not (args.note and args.source):
        raise SystemExit("[ERR] 需要 --queue，或同时给 --note 与 --source")
    return [{
        "title": os.path.basename(args.note)[:-3] if args.note.endswith(".md") else args.note,
        "note_path": args.note, "source_path": args.source,
        "note_type": args.note_type or "", "folder": "", "note_words": 0, "source_words": 0,
    }]


def judge(items: list) -> list:
    from shared.note_audit import audit_one, necessity_of

    out = []
    for it in items:
        row = {
            "title": it.get("title", ""), "note_path": it.get("note_path", ""),
            "source_path": it.get("source_path", ""), "note_type": it.get("note_type", ""),
            "folder": it.get("folder", ""), "author": it.get("author", ""),
            "source_words": it.get("source_words"), "note_words": it.get("note_words"),
            "class": it.get("class", ""),
        }
        if not (row["note_path"] and row["source_path"]):
            row["necessity"] = {"decision": "skip", "reasons": ["缺 note_path/source_path"],
                                "soft": []}
            out.append(row)
            continue
        try:
            res = audit_one(row)
        except Exception as exc:
            row["necessity"] = {"decision": "error", "reasons": [str(exc)], "soft": []}
            out.append(row)
            continue
        nec = necessity_of(res)
        row["necessity"] = nec
        row["anchors"] = res["anchors"]
        row["ratio"] = res["ratio"]
        # 供排序：issues 数越多越该改
        row["_n_issues"] = len(nec["reasons"])
        out.append(row)
    return out


def write_outputs(rows: list, args, base: str) -> None:
    keep = [r for r in rows if r["necessity"]["decision"] == "keep"]
    redo = [r for r in rows if r["necessity"]["decision"] == "redo"]
    other = [r for r in rows if r["necessity"]["decision"] not in ("keep", "redo")]

    if args.out_json:
        json.dump({"items": redo}, open(args.out_json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    reasons = collections.Counter()
    for r in redo:
        for reason in r["necessity"]["reasons"]:
            reasons[reason.split("：")[0].split("（")[0][:28]] += 1

    L = []
    P = L.append
    P("# 重做必要性判断（T13）")
    P("")
    P(f"- 输入：`{os.path.basename(base) if base else '(单片)'}`")
    P(f"- 候选 **{len(rows)}** 条 → **建议重做 {len(redo)}** / **建议跳过 {len(keep)}**"
      + (f" / 无法判定 {len(other)}" if other else ""))
    P("- 判据：既有四类判据的 hard 集合 + 证据确定性缺陷；形态信号不计入（§6 A2）")
    P("")
    P("## 一、为什么这些该重做")
    P("")
    P("| 理由 | 条数 |")
    P("|---|---|")
    for k, v in reasons.most_common():
        P(f"| {k} | {v} |")
    P("")
    P("## 二、建议重做（按问题数排序，TOP 30）")
    P("")
    P("| # | 标题 | 源/稿字数 | 锚点召回 | 理由 |")
    P("|---|---|---|---|---|")
    for i, r in enumerate(sorted(redo, key=lambda x: -x["_n_issues"])[:30], 1):
        a = r.get("anchors") or {}
        rec = f"{a['recall']:.0%}" if a.get("recall") is not None else "不适用"
        P(f"| {i} | {r['title'][:36]} | {r.get('source_words')}/{r.get('note_words')} | {rec} | "
          f"{'；'.join(x[:40] for x in r['necessity']['reasons'][:2])} |")
    P("")
    P("## 三、建议跳过（选择不改）")
    P("")
    P(f"共 {len(keep)} 条 —— 现有稿在生产会拦的那一档上没有缺陷。")
    P("重做它们只会带来「改坏」的风险，没有可论证的收益。")
    P("")
    for r in keep[:40]:
        P(f"- {r['title'][:60]}")
    if len(keep) > 40:
        P(f"- …（另 {len(keep) - 40} 条见 `{os.path.basename(args.out_json)}` 之外的完整清单）")
    P("")
    P("> ⚠️ 本脚本只**判定**，不执行任何写入。执行权在人：")
    P("> 跳过 = 这条不进重做队列；重做 = 交给既有管线（PIPELINES §3）走原路径覆盖。")
    P("")
    with open(args.out_report, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="重做必要性判断（只读，不执行）")
    ap.add_argument("--queue", help="build_resummarize_queue.py 的产物 JSON")
    ap.add_argument("--note", help="单片模式：笔记路径")
    ap.add_argument("--source", help="单片模式：源路径")
    ap.add_argument("--note-type", default="", help="单片模式：笔记类型")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out-json", default="", help="写出「建议重做」子集（喂给 build_resum_batches.py --queue）")
    ap.add_argument("--out-report", default="", help="写出 Markdown 报告")
    args = ap.parse_args()

    from shared.env import load_env
    load_env()

    items = load_items(args)
    rows = judge(items)
    keep = sum(1 for r in rows if r["necessity"]["decision"] == "keep")
    redo = sum(1 for r in rows if r["necessity"]["decision"] == "redo")
    other = len(rows) - keep - redo

    print(f"候选 {len(rows)} 条 → 建议重做 {redo} / 建议跳过 {keep}"
          + (f" / 无法判定 {other}" if other else ""))
    for r in sorted(rows, key=lambda x: -x.get("_n_issues", 0))[:5]:
        print(f"  [redo] {r['title'][:40]} — {'；'.join(x[:44] for x in r['necessity']['reasons'][:2])}")

    base = args.queue or ""
    if args.out_json:
        os.makedirs(os.path.dirname(os.path.abspath(args.out_json)), exist_ok=True)
    if args.out_report:
        os.makedirs(os.path.dirname(os.path.abspath(args.out_report)), exist_ok=True)
        write_outputs(rows, args, base)
        print(f"报告 → {args.out_report}")
    if args.out_json:
        print(f"重做子集 → {args.out_json}（传给 python scripts/build_resum_batches.py --queue <它>）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
