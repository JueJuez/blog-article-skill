"""scripts/audit_gate_signals.py — 门禁判据离线审计（只读，不改生产）

背景（`docs/decisions/DECISION-20260915-content-first-gate.md`）：
原 `prompts/verifier.py` 用「笔记字数 ÷ 源长」比例带触发抽检，实测对语音识别稿 100%
误报（本批 212/212），长源批次又 100% 报反方向的「偏短」。该比值与源质量无稳定因果。

本脚本在**改动生产门禁之前**对已落盘语料离线跑「内容判据」，用于：
  1) 看命中率是否合理（期望个位数%，而非 100%）；
  2) 出「命中最多 TOP N」清单供人工核对是否真的差；
  3) 出**分组命中率**——判据必须在「真问题批次」齐发、在「正常批次」静默。

判据实现全部来自 `prompts/content_signals.py`（与生产门禁同一真源，避免漂移）。

用法：
    python scripts/audit_gate_signals.py --corpus all --top 12 --min-anchors 4 \
        --json _tmp/audit_all.json --report notes/_reports/20260915_gate_audit.md
"""
from __future__ import annotations

import argparse
import collections
import difflib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prompts import content_signals as CS  # noqa: E402

REGISTRY = os.path.join(ROOT, "notes", "_meta", "summary_registry.json")
RAW_DIR = os.path.join(ROOT, "notes")
SLICES_DIR = os.path.join(ROOT, "_tmp", "slices")
VAULT = os.getenv("OBSIDIAN_VAULT_PATH") or r"D:\Code\Obsidian\obsidian-path\AI 总结笔记"

RAW_NAME_RE = re.compile(r"^_raw_(?P<key>.+?)-\d{8}-\d{6}\.md$")
BV_RE = re.compile(r"(BV[0-9A-Za-z]{8,})")


# ---------------------------------------------------------------- 语料构建

def _raw_index() -> dict:
    idx = {}
    for f in os.listdir(RAW_DIR):
        m = RAW_NAME_RE.match(f)
        if m:
            idx.setdefault(m.group("key"), os.path.join(RAW_DIR, f))
    return idx


def _slice_raw_map() -> dict:
    """bvid -> raw_file（本批条目的权威来源映射，由切片文件回查）。"""
    out = {}
    if not os.path.isdir(SLICES_DIR):
        return out
    for name in os.listdir(SLICES_DIR):
        if not name.endswith(".json"):
            continue
        try:
            items = json.load(open(os.path.join(SLICES_DIR, name), encoding="utf-8"))
        except Exception:
            continue
        for it in items:
            bv = (it.get("bvid") or "").upper()
            if bv:
                out[bv] = it.get("raw_file") or ""
    return out


def build_corpus(which: str = "all") -> list:
    data = json.load(open(REGISTRY, encoding="utf-8"))
    raw_idx = _raw_index()
    bv_map = _slice_raw_map()
    out = []
    for v in data.values():
        if not isinstance(v, dict):
            continue
        fn = v.get("filename") or ""
        if not fn:
            continue
        note_path = os.path.join(VAULT, fn.replace("/", os.sep))
        if not os.path.exists(note_path):
            continue
        folder = fn.replace(os.sep, "/")
        is_batch = ("林总linn/吸引女生恋爱干货" in folder) or ("图灵AI大模型/26年" in folder)
        is_xiapeng = "夏鹏本鹏" in folder
        if which == "batch" and not is_batch:
            continue
        if which == "xiapeng" and not is_xiapeng:
            continue
        src = ""
        m = BV_RE.search(v.get("source_url", ""))
        if m and bv_map.get(m.group(1).upper()):
            src = bv_map[m.group(1).upper()]
        if not src or not os.path.exists(src):
            key = CS.sanitize_title(v.get("title") or "")
            src = raw_idx.get(key, "")
            if not src:
                close = difflib.get_close_matches(key, list(raw_idx), n=1, cutoff=0.9)
                src = raw_idx[close[0]] if close else ""
        if not src or not os.path.exists(src):
            continue
        out.append({
            "note_path": note_path, "source_path": src, "folder": folder,
            "note_type": v.get("note_type") or "", "title": v.get("title") or "",
            "group": "batch" if is_batch else ("xiapeng" if is_xiapeng else "other"),
        })
    return out


# ---------------------------------------------------------------- 审计

# 2026-09-18：判据实现下沉到 shared.note_audit（此前与 resum_audit_batch 各一份，90% 重叠）
from shared.note_audit import audit_one as _audit_one, score_of  # noqa: E402
from shared.note_audit import evidence as _evidence  # noqa: E402


def audit_one(item: dict) -> dict:
    """本语料需原样带出 title/folder/note_type/group 用于报表分组。"""
    return _audit_one(item, passthrough_keys=("title", "folder", "note_type", "group"))


def write_report(path: str, corpus: list, results: list, ranked: list, args) -> None:
    ok = [r for r in results if "error" not in r]
    by_title = {c["title"]: c for c in corpus}
    L = []
    A = L.append
    A("# 门禁判据离线审计报告（内容判据 vs 字数比例带）")
    A("")
    A(f"- 生成：`python scripts/audit_gate_signals.py --corpus {args.corpus} --top {args.top}`")
    A(f"- 语料：**{len(ok)}** 条（分组 {dict(collections.Counter(r['group'] for r in ok))}）")
    A("- 判据真源：`prompts/content_signals.py`（与生产门禁同一份实现）")
    A("- 决策依据：`docs/decisions/DECISION-20260915-content-first-gate.md`")
    A("")
    A("## 一、分组命中率（最关键的一张表）")
    A("")
    A("判据必须满足：**在真问题批次齐发、在正常批次静默**。")
    A("")
    A("| 分组 | n | ① 锚点<60% | ② 形态命中 | ③ 结构缺失 | 旧判据 比值>0.45 |")
    A("|---|---|---|---|---|---|")
    for g in sorted({r["group"] for r in ok}):
        gs = [r for r in ok if r["group"] == g]
        gu = [r for r in gs if r["anchors"]["total"] >= args.min_anchors]
        ga = sum(1 for r in gu
                 if r["anchors"]["recall"] is not None and r["anchors"]["recall"] < CS.RECALL_THRESHOLD)
        gf = sum(1 for r in gs if r["form"]["signals"])
        gm = [r for r in gs if r["structure"]["required"]]
        gms = sum(1 for r in gm if r["structure"]["missing"])
        gr = sum(1 for r in gs if r["ratio"] > 0.45)
        A(f"| `{g}` | {len(gs)} | {ga}/{len(gu)} 适用 | {gf}/{len(gs)} | {gms}/{len(gm)} | {gr}/{len(gs)} |")
    A("")
    A("- `batch` = 2026-09-15 林总linn + 图灵AI；`xiapeng` = 2026-09-14 夏鹏本鹏；`other` = 其余历史批次")
    A("- 若旧判据有效，它应与内容判据在同一分组同向。实测相反。")
    A("")
    A("## 二、各判据全局命中")
    A("")
    usable = [r for r in ok if r["anchors"]["total"] >= args.min_anchors]
    a_hit = sum(1 for r in usable
                if r["anchors"]["recall"] is not None and r["anchors"]["recall"] < CS.RECALL_THRESHOLD)
    any_form = [r for r in ok if r["form"]["signals"]]
    mapped = [r for r in ok if r["structure"]["required"]]
    miss = [r for r in mapped if r["structure"]["missing"]]
    A("| 判据 | 命中 | 说明 |")
    A("|---|---|---|")
    A(f"| ① 硬锚点召回 < {CS.RECALL_THRESHOLD:.0%} | {a_hit} / {len(usable)} 适用 | "
      f"**{len(ok) - len(usable)} 条源锚点 < {args.min_anchors}，该判据不适用** |")
    A(f"| ② 破碎形态 | {len(any_form)} / {len(ok)} | 见分布 |")
    A(f"| ③ 结构缺失 | {len(miss)} / {len(mapped)} | 模板声明必备模块的条目才参评 |")
    A(f"| ④ 照搬重合 > {CS.VERBATIM_THRESHOLD:.0%} | "
      f"{sum(1 for r in ok if r['verbatim'] > CS.VERBATIM_THRESHOLD)} / {len(ok)} | 转录稿改写后字面重合极低 |")
    A(f"| ~~旧：字数比例带 > 0.45~~ | {sum(1 for r in ok if r['ratio'] > 0.45)} / {len(ok)} | **对照组** |")
    A("")
    fc = collections.Counter()
    for r in ok:
        for s in r["form"]["signals"]:
            fc[s] += 1
    A("破碎形态分布：" + (" · ".join(f"{k} {v}" for k, v in fc.most_common()) if fc else "无命中"))
    A("")
    mc = collections.Counter(m for r in miss for m in r["structure"]["missing"])
    if mc:
        A("结构缺失分布：" + " · ".join(f"缺 {k} {v}" for k, v in mc.most_common()))
        A("")
    A(f"## 三、命中最多 TOP{args.top}（请人工核对是否真的差）")
    A("")
    for i, r in enumerate(ranked, 1):
        a = r["anchors"]
        A(f"### {i}. {r['title']}")
        A("")
        A(f"- 分组/模板：`{r['group']}` / `{r['note_type']}`　"
          f"笔记 {r['note_words']} 字 / 源 {r['source_words']} 字（比值 {r['ratio']}）")
        A(f"- 锚点：{a['matched']}/{a['total']}"
          + (f"，丢了 {'、'.join(a['missed'][:6])}" if a.get("missed") else "")
          + (f"（分型 {a['kinds']}）" if a.get("kinds") else "（无锚点，该判据不适用）"))
        A(f"- 形态：{('、'.join(r['form']['signals']) or '无')}　"
          f"最长句 {r['form']['max_sentence']} 字　相邻段相似 {r['form']['adjacent_sim']}")
        A(f"- 结构缺失：{'、'.join(r['structure']['missing']) or '无'}")
        A(f"- 照搬重合：{r['verbatim']}　文件夹：`{r['folder']}`")
        c = by_title.get(r["title"])
        if c:
            for tag, text in _evidence(c, limit=240):
                A(f"- **{tag} 证据**：")
                A("")
                A("  > " + text.replace("\n", " ")[:400])
        A("")
    worst_batch = sorted([r for r in ok if r["group"] == "batch"],
                         key=lambda r: score_of(r, args.min_anchors), reverse=True)[:3]
    A("## 四、对照组：`batch`（09-15 本次）得分最高的 3 条")
    A("")
    A("用来验证判据是否会在「正常批次」上乱报——若这 3 条看着也没问题，说明判据静默是对的。")
    A("")
    for i, r in enumerate(worst_batch, 1):
        A(f"{i}. **{r['title']}**　笔记 {r['note_words']} 字 / 源 {r['source_words']} 字"
          f"（比值 {r['ratio']}）｜锚点 {r['anchors']['matched']}/{r['anchors']['total']}"
          f"｜形态 {'、'.join(r['form']['signals']) or '无'}"
          f"｜缺 {('、'.join(r['structure']['missing']) or '无')}")
    A("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="all", choices=["all", "batch", "xiapeng"])
    ap.add_argument("--json", default="")
    ap.add_argument("--report", default="")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--min-anchors", type=int, default=CS.MIN_ANCHORS)
    args = ap.parse_args()

    corpus = build_corpus(args.corpus)
    if not corpus:
        print("语料为空：检查 OBSIDIAN_VAULT_PATH 与 summary_registry 的 filename 字段")
        return 1
    results = []
    for it in corpus:
        try:
            results.append(audit_one(it))
        except Exception as e:
            results.append({"title": it["title"], "group": it["group"], "error": str(e)})
    ok = [r for r in results if "error" not in r]

    print(f"语料 {len(results)} 条（成功 {len(ok)}）")
    print("分组:", dict(collections.Counter(r["group"] for r in ok)))

    print("\n=== 分组命中率（关键：应在真问题批次齐发、正常批次静默）===")
    print(f"  {'分组':<10}{'n':>5}  ① 锚点<60%      ② 形态        ③ 结构缺失    旧判据>0.45")
    for g in sorted({r["group"] for r in ok}):
        gs = [r for r in ok if r["group"] == g]
        gu = [r for r in gs if r["anchors"]["total"] >= args.min_anchors]
        ga = sum(1 for r in gu
                 if r["anchors"]["recall"] is not None and r["anchors"]["recall"] < CS.RECALL_THRESHOLD)
        gf = sum(1 for r in gs if r["form"]["signals"])
        gm = [r for r in gs if r["structure"]["required"]]
        gms = sum(1 for r in gm if r["structure"]["missing"])
        gr = sum(1 for r in gs if r["ratio"] > 0.45)
        print(f"  {g:<10}{len(gs):>5}  {ga:>4}/{len(gu):<4}适用  {gf:>4}/{len(gs):<4}    "
              f"{gms:>4}/{len(gm):<4}    {gr:>4}/{len(gs):<4}")

    print("\n=== 判据详情 ===")
    usable = [r for r in ok if r["anchors"]["total"] >= args.min_anchors]
    print(f"① 锚点：适用 {len(usable)}/{len(ok)}，命中 "
          f"{sum(1 for r in usable if r['anchors']['recall'] is not None and r['anchors']['recall'] < CS.RECALL_THRESHOLD)}")
    fc = collections.Counter(s for r in ok for s in r["form"]["signals"])
    print(f"② 形态：{dict(fc) or '无命中'}")
    mc = collections.Counter(m for r in ok for m in r["structure"]["missing"])
    print(f"③ 结构缺失：{dict(mc) or '无命中'}")
    print(f"④ 照搬：> {CS.VERBATIM_THRESHOLD:.0%} 命中 "
          f"{sum(1 for r in ok if r['verbatim'] > CS.VERBATIM_THRESHOLD)}")
    print(f"对照·旧判据：比值>0.45 {sum(1 for r in ok if r['ratio'] > 0.45)}，"
          f"比值<0.25 {sum(1 for r in ok if r['ratio'] < 0.25)}")

    ranked = sorted(ok, key=lambda r: score_of(r, args.min_anchors), reverse=True)[:args.top]
    print(f"\n=== 命中最多 TOP{args.top} ===")
    for i, r in enumerate(ranked, 1):
        a = r["anchors"]
        print(f"{i:2d}. [{r['group']}/{r['note_type']}] {r['title'][:44]}")
        print(f"    笔记{r['note_words']}字 / 源{r['source_words']}字 比值{r['ratio']}"
              f" | 锚点 {a['matched']}/{a['total']}"
              f" | 形态 {','.join(r['form']['signals']) or '-'}"
              f" | 缺 {','.join(r['structure']['missing']) or '-'}"
              f" | 重合 {r['verbatim']}")
        if a.get("missed"):
            print(f"    丢了: {', '.join(a['missed'][:6])}")

    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        json.dump({"results": results, "corpus_size": len(results)},
                  open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n明细已写入 {args.json}")
    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        write_report(args.report, corpus, results, ranked, args)
        print(f"人工核对报告已写入 {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
