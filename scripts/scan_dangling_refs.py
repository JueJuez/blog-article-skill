"""scripts/scan_dangling_refs.py — 悬空指代全库扫描（T4 · 只读统计，不拦不改）

来源：`docs/plans/PLAN-20261018-knowledge-layer.md` §4 / §6 A7。

要回答的问题只有一个：**「段落悬空指代」在我们的库里到底是不是个问题？**
下游按段落检索（`splitKnowledgePassages`），段首写「这个方法/如上/后者」会让孤立片段变成残缺事实；
但如果全库命中率是个位数百分比，那就说明我们在解决一个不存在的问题（§7：不为不存在的问题建改写轮）。

用法（默认全库只读、dry-run）:
    python scripts/scan_dangling_refs.py
    python scripts/scan_dangling_refs.py --limit 200 --sample 15      # 先看样本辨真假
    python scripts/scan_dangling_refs.py --json _tmp/dangling.json --report notes/_reports/dangling.md

输出口径:
    - `para` = 普通段落首行（主要判据，决定要不要立项）
    - `item` = 列表块的第一项（单列一类，先看占比再决定是否并入）

⚠️ 本脚本**不判定、不拦截、不改写任何笔记**。它是 §3.3 的「报告项」：
先看分布，确认有效后才考虑把它升级为生成端约束（走 prompt 预防，绝不建独立改写轮）。
"""
from __future__ import annotations

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

DEFAULT_VAULT = os.getenv("OBSIDIAN_VAULT_PATH") or r"D:\Code\Obsidian\obsidian-path\AI 总结笔记"


def iter_notes(vault: str, limit: int = 0, under: str = ""):
    """遍历 vault 下的 .md（跳过隐藏目录）；`limit` 只取前 N 篇用于冒烟。"""
    root = os.path.join(vault, under) if under else vault
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in sorted(filenames):
            if not name.endswith(".md"):
                continue
            yield os.path.join(dirpath, name)
            n += 1
            if limit and n >= limit:
                return


def scan(vault: str, limit: int = 0, under: str = "") -> list:
    from shared import dangling_ref as DR

    out = []
    for path in iter_notes(vault, limit, under):
        try:
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        try:
            res = DR.scan_note(raw)
        except Exception:                       # 单篇解析失败不影响全库统计
            continue
        rel = os.path.relpath(path, vault).replace(os.sep, "/")
        res.update({"path": path, "rel": rel})
        out.append(res)
    return out


def _zone(rel: str) -> str:
    return rel.split("/")[0] if "/" in rel else "根目录"


def write_report(path: str, results: list, agg: dict, sample: int) -> None:
    from shared import dangling_ref as DR

    L = []
    P = L.append
    P("# 悬空指代全库扫描（T4 · 只读报告）")
    P("")
    P("- 生成：`python scripts/scan_dangling_refs.py`")
    P(f"- 笔记 **{agg['notes']}** 篇，block（空行分段）{agg['paragraphs']} 段"
      f"，内容行 {agg['line_scan']['lines']} 行")
    P(f"- block 首命中 **{agg['hits']}** 处 → 命中率 **{agg['paras_with_hits_pct']:.2%}**（**主判据**）")
    P(f"- 行首命中 {agg['line_scan']['hits']} 处 → 命中率 {agg['line_scan']['rate']:.2%}（辅助观察）")
    P(f"- 有命中的笔记 {agg['notes_with_hits']} 篇（{agg['notes_with_hits_pct']:.1%}）")
    P("")
    P(f"> **结论：{DR.verdict(agg)}**")
    P("")
    P("## 一、命中类别分布")
    P("")
    P("| 类别 | 命中 |")
    P("|---|---|")
    for k, v in agg["by_kind"].items():
        P(f"| `{k}` | {v} |")
    P("")
    P("- `para` = 普通段落首行（**主判据**）；`item` = 列表块首行（先看占比再决定要不要并入）。")
    P("- 分母口径：下游 writing-studio 先按行切、再把连续行打包成 ≤1200 字符块并补前后各 200 字符")
    P("  （`src/lib/knowledgePassages.ts:splitKnowledgePassages`）。故行首选在块中间时通常能被邻文救回，")
    P("  真正的孤立风险在 block 首 —— 这也是本表以 block 为准、行级只作辅助的原因。")
    P("")
    P("## 二、分区分布")
    P("")
    zones = {}
    for r in results:
        z = _zone(r["rel"])
        d = zones.setdefault(z, {"notes": 0, "paras": 0, "hits": 0})
        d["notes"] += 1
        d["paras"] += r["paragraphs"]
        d["hits"] += len(r["hits"])
    P("| 分区 | 笔记 | 段落 | 命中 | 命中率 |")
    P("|---|---|---|---|---|")
    for z, d in sorted(zones.items(), key=lambda x: -x[1]["paras"]):
        rate = f"{d['hits'] / d['paras']:.2%}" if d["paras"] else "—"
        P(f"| `{z}` | {d['notes']} | {d['paras']} | {d['hits']} | {rate} |")
    P("")
    P(f"## 三、样本（命中最多前 {sample} 段，用于判断真阳性率）")
    P("")
    flat = [(r, h) for r in results for h in r["hits"]]
    for r, h in flat[:sample]:
        P(f"- `{h['kind']}` — {h['text']}")
        P(f"  - `{r['rel']}`")
    P("")
    P("## 四、怎么读这份报告")
    P("")
    P("- 先看**样本**：样本里多数是真的『离开上文读不懂』，这个信号才成立。")
    P("- 再看**命中率**：个位数百分比可判定为不成问题，不必为它改 prompt。")
    P("- 即便立项也只能走**生成端预防**（在 prompt 里加一条约束），不建独立改写轮——")
    P("  改写会丢限定词，违反信息守恒（§7）。")
    P("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="悬空指代全库扫描（只读）")
    ap.add_argument("--vault", default=DEFAULT_VAULT)
    ap.add_argument("--under", default="", help="只扫某个子目录（如【监控】）")
    ap.add_argument("--limit", type=int, default=0, help="只扫前 N 篇（冒烟用）")
    ap.add_argument("--json", default="")
    ap.add_argument("--report", default="")
    ap.add_argument("--sample", type=int, default=20, help="报告里打印多少条样本")
    args = ap.parse_args()

    from shared import dangling_ref as DR

    if not os.path.isdir(args.vault):
        print(f"[ERR] vault 不存在: {args.vault}")
        return 2
    results = scan(args.vault, limit=args.limit, under=args.under)
    if not results:
        print("[ERR] 没有扫到任何笔记")
        return 1
    agg = DR.aggregate(results)

    print(f"笔记 {agg['notes']} 篇 / block（空行分段）{agg['paragraphs']} 段 / 内容行 {agg['line_scan']['lines']} 行")
    print(f"block 首悬空指代 {agg['hits']} 处 → 命中率 {agg['paras_with_hits_pct']:.2%}（主判据）")
    print(f"行首悬空指代 {agg['line_scan']['hits']} 处 → 命中率 {agg['line_scan']['rate']:.2%}（辅助观察）")
    print(f"有命中的笔记：{agg['notes_with_hits']} 篇（{agg['notes_with_hits_pct']:.1%}）")
    print("类别分布：", dict(list(agg["by_kind"].items())[:8]))
    print(f"\n结论：{DR.verdict(agg)}")
    print(f"\n样本（前 {min(args.sample, 8)} 条）：")
    for r, h in [(r, h) for r in results for h in r["hits"]][:8]:
        print(f"  [{h['kind']}] {h['text'][:50]}  ← {r['rel'][-46:]}")

    if args.json:
        import json
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        json.dump({"aggregate": agg, "results": results},
                  open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n明细 → {args.json}")
    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        write_report(args.report, results, agg, args.sample)
        print(f"报告 → {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
