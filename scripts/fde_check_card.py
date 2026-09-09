#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FDE 案例卡忠实度门禁：卡内数字与引文逐项回原文验证（机械防编造）。

用法:
  python scripts/fde_check_card.py "<卡片.md>"
自动按卡文件名前缀 NO.NN 定位 notes/_scraped/fde/cases/caseNN.txt，
也可用 --src 显式指定原文 txt。

检查两类内容:
  1. 引文「...」 / “...”  -> 去空白后必须在原文中命中
  2. 数字（白名单过滤后）  -> 数字串必须出现在原文中

退出码: 0=全部通过  1=存在 FAIL（写卡者须逐条修复或改写措辞后重跑）。
"""
import argparse
import glob
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES_DIR = os.path.join(REPO, "notes", "_scraped", "fde", "cases")

# 提取数字前先剔除的白名单片段（编号/页码/版本/日期等，非事实断言）
WHITELIST_SPANS = [
    r"NO\.\d+", r"P\d+(?:[-–]\d+)?", r"v\d+(?:\.\d+)?", r"Q\d",
    r"§\d+(?:\.\d+)?", r"\d{4}-\d{2}-\d{2}", r"\d{1,3}/245",
    r"\b\d+\.\d+例\b", r"FDE案例100", r"案例100",
]


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def extract_checks(body: str):
    """返回 (quotes, numbers[(num, context)])"""
    quotes = re.findall(r"「([^「」]+)」|“([^“”]+)”", body)
    quotes = [a or b for a, b in quotes]
    stripped = body
    for pat in WHITELIST_SPANS:
        stripped = re.sub(pat, " ", stripped)
    numbers = []
    for m in re.finditer(r"\d+(?:\.\d+)?", stripped):
        ctx = stripped[max(0, m.start() - 18):m.end() + 14].replace("\n", " ")
        numbers.append((m.group(0), ctx.strip()))
    return quotes, numbers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("card")
    ap.add_argument("--src")
    args = ap.parse_args()

    with open(args.card, encoding="utf-8") as f:
        card = f.read()
    # 剔除元数据头（H1 与 > 引用行）、标题行、版本脚注（变更记录会引用已删除的编造内容，属有意豁免）
    body = "\n".join(
        l for l in card.splitlines()
        if not l.startswith(">") and not l.startswith("#")
        and not l.startswith("**版本**"))

    src_path = args.src
    if not src_path:
        m = re.match(r"NO\.(\d+)", os.path.basename(args.card))
        if not m:
            raise SystemExit("卡文件名须以 NO.NN 开头，或用 --src 指定原文")
        src_path = os.path.join(CASES_DIR, f"case{int(m.group(1)):02d}.txt")
    if not os.path.exists(src_path):
        cands = glob.glob(os.path.join(CASES_DIR, "case*.txt"))
        raise SystemExit(f"原文不存在: {src_path}\n现有: {cands}")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    src_n, src_raw = norm(src), src

    quotes, numbers = extract_checks(body)
    fails = []

    print(f"== 忠实度门禁: {os.path.basename(args.card)} ==")
    print(f"引文 {len(quotes)} 条 / 数字 {len(numbers)} 个\n")

    for q in quotes:
        ok = norm(q) in src_n
        print(f"  {'PASS' if ok else 'FAIL'} 引文: {q[:50]}")
        if not ok:
            fails.append(("引文", q))

    # 数字去重后检查
    seen = set()
    for num, ctx in numbers:
        if num in seen or len(num) < 2:  # 单位数字噪声大，跳过
            continue
        seen.add(num)
        ok = num in src_raw or num in src_n
        print(f"  {'PASS' if ok else 'FAIL'} 数字 {num:>6}  …{ctx}…")
        if not ok:
            fails.append(("数字", f"{num} (…{ctx}…)"))

    print(f"\n结果: {'PASS 全部通过' if not fails else f'FAIL {len(fails)} 项'}")
    if fails:
        print("\n须逐条处理: 原文确有 -> 修正措辞对齐原文; 原文没有 -> 删除或标注「原文未给」")
        sys.exit(1)


if __name__ == "__main__":
    main()
