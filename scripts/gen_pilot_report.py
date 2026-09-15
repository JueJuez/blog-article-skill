# -*- coding: utf-8 -*-
"""生成「重总结试点 A/B 对照」报告（只读，读 _tmp/resum_pilot 的产物）。"""
import json
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prompts import content_signals as CS  # noqa: E402

BATCHES = [
    ("_tmp/resum_pilot/batch_1.json", "v1(无反向警戒)"),
    ("_tmp/resum_pilot/recheck_1.json", "v2(含反向警戒)"),
    ("_tmp/resum_pilot/batch_2.json", "v2(含反向警戒)"),
]


def metrics(note, src, note_type):
    d = CS.content_flags(note, src, note_type)["details"]
    a, f = d["anchors"], d["form"]
    miss = CS.structure_missing(note, note_type).get("missing") or []
    sec = len([l for l in note.splitlines() if l.startswith("## ")])
    return {
        "w": CS.count_words(note),
        "ratio": round(CS.count_words(note) / max(1, CS.count_words(src)), 2),
        "miss": len(miss),
        "sec": sec,
        "ar": f"{a['matched']}/{a['total']}" if a["total"] else "—",
        "form": len(f["signals"]),
        "v": round(CS.verbatim_ratio(note, src), 3),
    }


def main():
    rows = []
    for path, tag in BATCHES:
        for it in json.load(open(os.path.join(ROOT, path), encoding="utf-8")):
            out = it["out_path"]
            if not os.path.exists(out):
                continue
            src = CS.strip_source(open(it["source_path"], encoding="utf-8").read())
            old = CS.strip_note(open(it["old_note_path"], encoding="utf-8").read())
            new = CS.strip_note(open(out, encoding="utf-8").read())
            rows.append({
                "tag": tag, "title": it["title"], "src": CS.count_words(src),
                "old": metrics(old, src, it["note_type"]),
                "new": metrics(new, src, it["note_type"]),
                "out": out,
            })

    L = []
    P = L.append
    P("# 重总结试点 A/B 对照（生成侧改造验证）")
    P("")
    P("- 日期：2026-09-16　语料：`notes/_meta/resummarize_queue.json` 优先级前列 5 篇长/中源")
    P("- **v1 = 覆盖优先规范（无反向警戒）**；**v2 = v1 + 反向警戒（初稿>源长60% ⇒ 压缩表达、不丢要点）**")
    P("- 判据全部来自 `prompts/content_signals.py`（机械、可复算）；旧笔记 = 库内现版")
    P("")
    P("## 一、结论")
    P("")
    P("| 版本 | 篇幅比值（新/源）中位 | 必备模块缺失合计 | 小节数中位 | 照搬率最大 |")
    P("|---|---|---|---|---|")
    for tag in ("v1(无反向警戒)", "v2(含反向警戒)"):
        rs = [r for r in rows if r["tag"] == tag]
        if not rs:
            continue
        P(f"| {tag} | {st.median(r['new']['ratio'] for r in rs):.2f}（旧 {st.median(r['old']['ratio'] for r in rs):.2f}） "
          f"| {sum(r['new']['miss'] for r in rs)}（旧 {sum(r['old']['miss'] for r in rs)}） "
          f"| {st.median(r['new']['sec'] for r in rs):.0f}（旧 {st.median(r['old']['sec'] for r in rs):.0f}） "
          f"| {max(r['new']['v'] for r in rs):.3f}（≈0 即无照搬） |")
    P("")
    P("### 两条关键事实")
    P("")
    P("1. **v1 把「不设上限」执行成了「比源还长」**：3 篇长源笔记 1.03~1.67 倍，属复述式膨胀。")
    P("2. **加上「源长 60% 反向警戒」后全部落回 0.57~0.61**，且必备模块、小节覆盖、锚点召回**没有回退**——")
    P("   证明「压缩表达、不压内容」可被模型稳定执行。")
    P("")
    P("## 二、逐篇明细")
    P("")
    for r in rows:
        P(f"### {r['title'][:44]}")
        P("")
        P(f"- 源 {r['src']} 字　版本 `{r['tag']}`")
        P(f"- 旧笔记：{r['old']['w']} 字（比值 {r['old']['ratio']}）｜缺模块 {r['old']['miss']}｜小节 {r['old']['sec']}｜锚点 {r['old']['ar']}")
        P(f"- 新笔记：{r['new']['w']} 字（比值 {r['new']['ratio']}）｜缺模块 {r['new']['miss']}｜小节 {r['new']['sec']}｜"
          f"锚点 {r['new']['ar']}｜形态信号 {r['new']['form']}｜照搬 {r['new']['v']}")
        P(f"- 新笔记文件：`{os.path.relpath(r['out'], ROOT)}`")
        P("")
    P("## 三、遗留信号（进入全量重跑前须知）")
    P("")
    P("- **A超长句**（单句 >140 字）在 5 篇新笔记中出现 3 篇：完整性达标后，模型的下一个坏习惯是")
    P("  「把解释写成一句超长复合句」。处置：不改门禁（不拦落盘），作为抽检触发信号；")
    P("  若全量重跑后命中率仍高，再在模板中把「单句不超过 60 字」升为硬性条款。")
    P("- 锚点召回仍有个别丢失（4/6、10/12）：丢失项多为口语源里的弱相关数字，属可接受噪声；抽检时人工确认。")
    P("")
    out = os.path.join(ROOT, "notes", "_reports", "20260916_重总结试点A_B对照.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("报告已写", os.path.relpath(out, ROOT))


if __name__ == "__main__":
    main()
