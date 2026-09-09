#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FDE案例100 PDF 抽取器：解析目录 -> 自动算各例物理页边界 -> 清洗正文落盘。

用法:
  python scripts/fde_extract_cases.py                     # 全部案例
  python scripts/fde_extract_cases.py --only 3            # 只抽 NO.3
  python scripts/fde_extract_cases.py --pdf "<新版PDF路径>"  # 书更新后重跑

产出:
  notes/_scraped/fde/cases/caseNN.txt    每例清洗后正文
  notes/_scraped/fde/manifest.json       案例号/标题/起止物理页/字符数

清洗规则（源自 2026-09-08 人工验证）:
  - LaTeX 转义残留 \\~ \\. \\- \\_ \\& \\# \\$ \\% \\\\ -> 还原字符
  - 页脚噪声 "N/245" 独立行 -> 删除
  - 3+ 连续空行 -> 压成 1 空行
页码映射: 目录标页 + 2 = 物理页（本书实证: 标6=物理8, 标14=16, 标46=48）。
"""
import argparse
import json
import os
import re

import fitz  # PyMuPDF

DEFAULT_PDF = r"C:/Users/O1830/Desktop/Datawhale FDE案例100.pdf"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(REPO, "notes", "_scraped", "fde")
PAGE_OFFSET = 2  # 目录标页 -> 物理页偏移

LATEX_ESCAPES = [
    (r"\~", "~"), (r"\.", "."), (r"\-", "-"), (r"\_", "_"),
    (r"\&", "&"), (r"\#", "#"), (r"\$", "$"), (r"\%", "%"),
    ("\\\\", "\\"),
]
FOOTER_RE = re.compile(r"\n\s*\d{1,3}/245\s*(?=\n|$)")


def clean(text: str) -> str:
    text = text.replace("\r", "")
    for a, b in LATEX_ESCAPES:
        text = text.replace(a, b)
    text = FOOTER_RE.sub("\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_toc(doc) -> list:
    """从目录页解析 [(no, title, toc_page)]。标题可跨行，页码是独立数字行。"""
    pat = re.compile(r"NO\.(\d+)\s+([\s\S]*?)\n(\d{1,3})(?:\s*\n|$)")
    seen, entries = set(), []
    for pno in range(min(10, len(doc))):
        text = doc[pno].get_text("text")
        if "目录" not in text or "NO." not in text:
            continue
        text = FOOTER_RE.sub("\n", text)
        for m in pat.finditer(text):
            no = int(m.group(1))
            if no in seen:
                continue
            seen.add(no)
            title = " ".join(m.group(2).split())
            entries.append((no, title, int(m.group(3))))
    return sorted(entries)


def locate_start(doc, toc_start: int, title: str) -> int:
    """物理起始页 = 标页+offset；校验失败则在附近扫描（空白归一化匹配，跳过目录页）。"""
    cand = toc_start + PAGE_OFFSET - 1  # 转 0-based
    key = re.sub(r"\s+", "", title)[:10]

    def ok(p: int) -> bool:
        if not 0 <= p < len(doc):
            return False
        t = doc[p].get_text("text")
        if "目录" in t[:30]:  # 目录页也含标题，必须排除
            return False
        return key in re.sub(r"\s+", "", t)

    for probe in [cand] + [cand + d for d in range(-4, 5) if d != 0]:
        if ok(probe):
            return probe
    return cand


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default=DEFAULT_PDF)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--only", type=int, help="只抽指定案例号")
    args = ap.parse_args()

    doc = fitz.open(args.pdf)
    entries = parse_toc(doc)
    if not entries:
        raise SystemExit("目录解析失败：未找到任何 NO.N 条目")
    print(f"目录解析: {len(entries)} 例 (NO.{entries[0][0]} ~ NO.{entries[-1][0]})")

    cases_dir = os.path.join(args.out, "cases")
    os.makedirs(cases_dir, exist_ok=True)
    manifest = []
    for i, (no, title, toc_page) in enumerate(entries):
        if args.only and no != args.only:
            continue
        start = locate_start(doc, toc_page, title)
        if i + 1 < len(entries):
            nxt = entries[i + 1]
            end = locate_start(doc, nxt[2], nxt[1]) - 1
        else:
            end = len(doc) - 1
        text = clean("\n".join(
            doc[p].get_text("text") for p in range(start, end + 1)))
        fp = os.path.join(cases_dir, f"case{no:02d}.txt")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(text)
        manifest.append({
            "no": no, "title": title,
            "phys_start": start + 1, "phys_end": end + 1,
            "file": fp, "chars": len(text),
        })
        print(f"NO.{no:<2} 物理 P{start+1}-P{end+1}  {len(text):>6} 字符  {title[:30]}")

    mp = os.path.join(args.out, "manifest.json")
    old = []
    if os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            old = [e for e in json.load(f) if e["no"] not in {m["no"] for m in manifest}]
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(sorted(manifest + old, key=lambda e: e["no"]),
                  f, ensure_ascii=False, indent=2)
    print(f"manifest -> {mp}")


if __name__ == "__main__":
    main()
