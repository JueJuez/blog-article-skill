#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 topicId 列表逐条抓 scys 源正文（绕过 dedup，不总结不入队）。

用于「待重抓清单」：笔记已总结、源文本丢失，只把原文补回
notes/_scraped/scys/<topicId>.md（与 scys_batch_fetch 落源同目录、同名）。

复用 scys_batch_fetch.ScysBatchFetcher.fetch_article（只抓主文 + 可选外链知识库），
但**不**调 build_pending_entry，因此不入队、不触发总结。

用法：
    # 直接从待重抓清单抓取（scys 段）
    python scripts/fetch_scys_by_ids.py --list-md notes/_reports/20260915_待重抓清单.md --limit 3
    # 指定 id
    python scripts/fetch_scys_by_ids.py --ids 45544588518241818,45544511144481428
    # 不抓外链知识库（只主文，更快）
    python scripts/fetch_scys_by_ids.py --list-md <清单> --no-external
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
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
import scys_batch_fetch as SCF
from scys_batch_fetch import ScysBatchFetcher, BASE
from shared.cdp_session import SharedCdpSession

STATE_PATH = BASE / "refetch_state.json"
ARTICLE_GAP = (15, 40)


def extract_topic_ids_from_md(md_path: str) -> list[str]:
    txt = Path(md_path).read_text(encoding="utf-8")
    return re.findall(r"scys\.com/articleDetail/xq_topic/(\d+)", txt)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list-md", default="", help="待重抓清单 markdown，正则提取 scys topicId")
    ap.add_argument("--ids", default="", help="逗号分隔 topicId 列表")
    ap.add_argument("--ids-file", default="", help="JSON 数组文件路径")
    ap.add_argument("--limit", type=int, default=0, help="最多抓几条（探针用）")
    ap.add_argument("--no-external", action="store_true", help="不抓外链知识库（只主文）")
    ap.add_argument("--force", action="store_true", help="忽略 refetch_state 的 done，强制重抓")
    args = ap.parse_args()

    ids: list[str] = []
    if args.ids:
        ids += [x.strip() for x in args.ids.split(",") if x.strip()]
    if args.ids_file and os.path.exists(args.ids_file):
        ids += json.loads(Path(args.ids_file).read_text(encoding="utf-8"))
    if args.list_md:
        ids += extract_topic_ids_from_md(args.list_md)
    ids = list(dict.fromkeys(ids))
    if args.limit:
        ids = ids[: args.limit]
    print(f"[scys-refetch] 目标 {len(ids)} 条")

    state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {"done": []}
    done = set(str(x) for x in state.get("done", []))
    todo = ids if args.force else [i for i in ids if i not in done]
    print(f"[scys-refetch] 已完成 {len(done)}，待抓 {len(todo)}" +
          ("（--force 覆盖）" if args.force else ""))
    if not todo:
        return

    dummy = ScysBatchFetcher("__refetch__", 0, 0, 1)
    sess = SharedCdpSession()
    sess.__enter__()
    try:
        page = sess.new_page()
        for tid in todo:
            if args.no_external:
                orig = list(SCF.EXTERNAL_DOC_HOSTS)
                SCF.EXTERNAL_DOC_HOSTS = ()  # type: ignore[misc]
            try:
                r = dummy.fetch_article(page, tid, allow_internal=False)
                done.add(tid)
                state["done"] = sorted(done)
                STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[OK] {tid} -> {r.get('output')} ({r.get('chars')}字) wall={r.get('login_wall') or '无'}")
            except Exception as e:
                print(f"[FAIL] {tid}: {e}")
            finally:
                if args.no_external:
                    SCF.EXTERNAL_DOC_HOSTS = orig  # type: ignore[misc]
            gap = random.uniform(*ARTICLE_GAP)
            time.sleep(gap)
    finally:
        sess.__exit__(None, None, None)


if __name__ == "__main__":
    main()
