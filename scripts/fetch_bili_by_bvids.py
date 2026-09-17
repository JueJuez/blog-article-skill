#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 bvid 列表逐条抓 B站字幕/转录（绕过 dedup，不总结不入队）。

用于「待重抓清单」：笔记已总结、源文本丢失，只把字幕/转录补回
notes/_scraped/bili/<bvid>.md（标题 + 正文）。

直接调 videos.fetch.fetch_bilibili_transcript（纯抓字幕函数，不走 dedup、不入队）。
需要 .env 里的 BILI_COOKIE（脚本自动加载）。

用法：
    # 直接从待重抓清单抓取（B站段）
    python scripts/fetch_bili_by_bvids.py --list-md notes/_reports/20260915_待重抓清单.md --limit 2
    # 指定 bvid
    python scripts/fetch_bili_by_bvids.py --ids BV1bhbV6hE4F,BV1SvtC6LEW5
"""
import argparse
import json
import os
import re
import sys
import time
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BASE = ROOT / "notes" / "_scraped" / "bili"
STATE_PATH = BASE / "refetch_state.json"
GAP = (15, 30)


def extract_bvids_from_md(md_path: str) -> list[str]:
    txt = Path(md_path).read_text(encoding="utf-8")
    return re.findall(r"bilibili\.com/video/(BV\w+)", txt)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list-md", default="", help="待重抓清单 markdown，正则提取 bvid")
    ap.add_argument("--ids", default="", help="逗号分隔 bvid 列表")
    ap.add_argument("--limit", type=int, default=0, help="最多抓几条（探针用）")
    ap.add_argument("--lang", default="zh", help="字幕语言（默认 zh）")
    ap.add_argument("--with-asr", action="store_true",
                    help="无 CC 字幕时启用 ASR 音频转写（默认关，沙箱下常失败）")
    args = ap.parse_args()

    from shared.env import load_env
    load_env()
    from videos.fetch import fetch_bilibili_transcript

    ids: list[str] = []
    if args.ids:
        ids += [x.strip() for x in args.ids.split(",") if x.strip()]
    if args.list_md:
        ids += extract_bvids_from_md(args.list_md)
    ids = list(dict.fromkeys(ids))
    if args.limit:
        ids = ids[: args.limit]
    print(f"[bili-refetch] 目标 {len(ids)} 条")

    BASE.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {"done": []}
    done = set(state.get("done", []))
    todo = [i for i in ids if i not in done]
    print(f"[bili-refetch] 已完成 {len(done)}，待抓 {len(todo)}")
    if not todo:
        return

    for bv in todo:
        url = f"https://www.bilibili.com/video/{bv}"
        try:
            res = fetch_bilibili_transcript(url, lang=args.lang)
            if not res:
                print(f"[FAIL] {bv}: 未取到字幕（无CC且ASR未启用/失败）— 留待 --with-asr 单独处理")
                gap = random.uniform(*GAP)
                time.sleep(gap)
                continue
            # fetch_bilibili_transcript 返回 (title, segments, author)
            title, segs, author = res
            text = "\n".join((s.get("text") or "") for s in segs) if segs else ""
            if not text.strip():
                print(f"[FAIL] {bv}: 字幕为空（无CC且ASR未启用/失败）— 留待 --with-asr 单独处理")
                gap = random.uniform(*GAP)
                time.sleep(gap)
                continue
            out = BASE / f"{bv}.md"
            out.write_text(f"# {title}\n\n{text}\n", encoding="utf-8")
            done.add(bv)
            state["done"] = sorted(done)
            STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] {bv} -> {out.name} ({len(text)}字) {title[:30]}")
        except Exception as e:
            print(f"[FAIL] {bv}: {e}")
        gap = random.uniform(*GAP)
        time.sleep(gap)


if __name__ == "__main__":
    main()
