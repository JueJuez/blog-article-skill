# -*- coding: utf-8 -*-
"""按源长加权生成「阶段C 重跑批次」（只读，不改笔记不落盘）。

- 源长加权分批：>10000 字 → 4 条/批；4000~10000 → 8 条/批；<4000 → 16 条/批
  （每批的源总量近似可控，避免子 Agent 上下文被 2 万字长源撑爆）。
- 源匹配自查：用标题里最长的 CJK 连续段在源文本里找；找不到 → `match:suspect`
  （可能源定位错配，**单独成批，重跑前必须人工核对**）。
- 每条 prompt 预计算（模板 + 篇幅目标块 + QUALITY_GATE_SELFCHECK），新会话直接消费。

用法：python scripts/build_resum_batches.py
产物：notes/_meta/resum_batches/batch_NN.json + notes/_reports/20260916_重跑批次清单.md
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prompts.templates import get_note_prompt, QUALITY_GATE_SELFCHECK  # noqa: E402

QUEUE = os.path.join(ROOT, "notes", "_meta", "resummarize_queue.json")
OUT_DIR = os.path.join(ROOT, "notes", "_meta", "resum_batches")
REPORT = os.path.join(ROOT, "notes", "_reports", "20260916_重跑批次清单.md")
VAULT = os.getenv("OBSIDIAN_VAULT_PATH") or r"D:\Code\Obsidian\obsidian-path\AI 总结笔记"


def distinctive_span(title: str) -> str:
    """标题里最长的 CJK 连续段（>=6 字才可信，返回空串表示无法校验）。"""
    spans = re.findall(r"[\u4e00-\u9fff]{4,}", title or "")
    spans = [s for s in spans if len(s) >= 6] or spans
    return max(spans, key=len) if spans else ""


def main() -> int:
    items = json.load(open(QUEUE, encoding="utf-8"))["items"]
    os.makedirs(OUT_DIR, exist_ok=True)
    for f in os.listdir(OUT_DIR):
        os.remove(os.path.join(OUT_DIR, f))

    ok, suspect = [], []
    for it in items:
        src = open(it["source_path"], encoding="utf-8").read()
        span = distinctive_span(it["title"])
        hit = bool(span) and span in src
        row = dict(it)
        row["match"] = "ok" if (span and hit) else ("unchecked" if not span else "suspect")
        (ok if row["match"] == "ok" else suspect).append(row)

    # 源长加权分批
    buckets = {
        "long": [r for r in ok if r["source_words"] > 10000],
        "mid": [r for r in ok if 4000 <= r["source_words"] <= 10000],
        "short": [r for r in ok if r["source_words"] < 4000],
    }
    sizes = {"long": 4, "mid": 8, "short": 16}

    batches = []
    for name in ("long", "mid", "short"):
        rs = buckets[name]
        for i in range(0, len(rs), sizes[name]):
            part = rs[i:i + sizes[name]]
            for r in part:
                r["prompt"] = get_note_prompt(r["note_type"], r["source_words"]) + QUALITY_GATE_SELFCHECK
            batches.append(part)
    if suspect:
        batches.append(suspect)   # 存疑单列一批，重跑前人工核对

    for i, b in enumerate(batches, 1):
        p = os.path.join(OUT_DIR, f"batch_{i:02d}.json")
        json.dump(b, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 报告
    L = ["# 阶段C 重跑批次清单", ""]
    A = L.append
    A(f"- 队列 {len(items)} 条：匹配可信 {len(ok)} / **存疑 {len(suspect)}**（源文本里找不到标题关键词，"
      "需人工核对 source_path，见最后一批）")
    A(f"- 批次数 {len(batches)}（长源 4 条/批 · 中源 8 条/批 · 短源 16 条/批 · 存疑 1 批）")
    A("- 每条已预计算 prompt（模板 + 篇幅目标块 + 自检段）；新会话直接读批次文件派子 Agent。")
    A("")
    A("| 批次 | 条数 | 源长范围 | 分组构成 |")
    A("|---|---|---|---|")
    for i, b in enumerate(batches, 1):
        srcs = [r["source_words"] for r in b]
        import collections
        g = collections.Counter(r["author"] or r["zone"] for r in b)
        A(f"| batch_{i:02d} | {len(b)} | {min(srcs)}~{max(srcs)} | {dict(g)} |")
    A("")
    if suspect:
        A("## 存疑清单（match=suspect/unchecked，先核对再跑）")
        A("")
        A("| 标题 | 源路径 | 疑点 |")
        A("|---|---|---|")
        for r in suspect:
            A(f'| {r["title"][:34]} | `{os.path.relpath(r["source_path"], ROOT)}` | '
              f'{"源内找不到标题关键词" if r["match"] == "suspect" else "标题无可提取关键词"} |')
    open(REPORT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"批次 {len(batches)}：可信 {len(ok)} / 存疑 {len(suspect)}")
    print("报告 →", os.path.relpath(REPORT, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
