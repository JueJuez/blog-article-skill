# -*- coding: utf-8 -*-
"""阶段C 每批抽检：对刚落盘的批次跑 content_signals（只读，不改生产）。

为什么自建而非 `audit_gate_signals.py --corpus batch`：
  那个 batch 语料硬编码 2026-09-15 试点组（林总linn/图灵AI），不适用于重跑批次；
  本工具直接按批次 JSON 的 note_path vs source_path 逐条审计。

用法：python scripts/resum_audit_batch.py notes/_meta/resum_batches/batch_NN.json [--top N]
命中判定与 `audit_gate_signals.py::score_of` 一致：
  ① 硬锚点召回 < RECALL_THRESHOLD（源锚点数 >= 阈值才适用）
  ② 破碎形态信号
  ③ 必备结构缺失（required_sections）
  ④ 原文复述 > VERBATIM_THRESHOLD
  另报 A超长句（已知观察项：试点 3/5 命中，命中则 fix_inline 拆句）。
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prompts import content_signals as CS  # noqa: E402
# 2026-09-18：判据实现下沉到 shared.note_audit（此前本脚本与 audit_gate_signals 各一份，
# 90% 重叠；判据口径必须单一，否则改一处另一处不跟）
from shared.note_audit import audit_one, score_of  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: resum_audit_batch.py <batch.json> [--top N]")
        return 2
    top = 8
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])
    batch_path = sys.argv[1]
    items = json.load(open(batch_path, encoding="utf-8"))
    print(f"=== 抽检 {os.path.basename(batch_path)}: {len(items)} 条 ===")
    res = []
    for i, it in enumerate(items):
        if not os.path.exists(it.get("note_path") or ""):
            print(f"  [{i}] !! 无笔记文件（未落盘?）")
            continue
        if not os.path.exists(it.get("source_path") or ""):
            print(f"  [{i}] !! 缺源文件 {it.get('source_path')}")
            continue
        r = audit_one(it, with_evidence=True, evidence_limit=200)
        res.append(r)
        a = r["anchors"]
        hits = []
        if a["recall"] is not None and a["total"] >= 4 and a["recall"] < CS.RECALL_THRESHOLD:
            hits.append(f"锚点{a['recall']:.0%}")
        if r["form"]["signals"]:
            hits.append("破碎形态:" + ",".join(r["form"]["signals"][:2]))
        if r["structure"]["missing"]:
            hits.append("缺结构:" + ",".join(r["structure"]["missing"][:3]))
        if r["verbatim"] > CS.VERBATIM_THRESHOLD:
            hits.append(f"复述{r['verbatim']:.0%}")
        mark = "  <== HIT " + " | ".join(hits) if hits else ""
        ev = (" | A超长句" + ("".join(" " + v for _, v in r["evidence"]))[:60]) if r["evidence"] else ""
        print(f"  [{i}] {r['title']} | {r['note_words']}字/{r['source_words']}源={r['ratio']}"
              f" | 锚点 {a.get('recall') if a['total']>=4 else 'n/a('+str(a['total'])+')'}"
              f"{mark}{ev}")
    scored = [r for r in res if any([
        (r["anchors"]["recall"] is not None and r["anchors"]["total"] >= 4
         and r["anchors"]["recall"] < CS.RECALL_THRESHOLD),
        r["form"]["signals"], r["structure"]["missing"],
        r["verbatim"] > CS.VERBATIM_THRESHOLD])]
    print(f"\n命中 {len(scored)}/{len(res)}（判据：锚点召回<{CS.RECALL_THRESHOLD:.0%} / 形态 / 结构 / 复述>{CS.VERBATIM_THRESHOLD:.0%}）")
    return 0 if len(scored) == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
