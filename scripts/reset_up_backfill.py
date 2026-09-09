#!/usr/bin/env python3
"""scripts/reset_up_backfill.py — 重置某 UP 的补齐状态（备份式，2026-09-06）。

场景：用户把该 UP 的笔记从 Obsidian 全删，要求全部重抓重总结。此时残留的
「已完成」记录会让补齐管道跳过这些视频（V4 修复后过滤=fetch_results ∪ dedup
索引 ∪ risk_skip），必须成套清掉，且清掉的东西可回滚（移动到备份目录，不删除）。

线性主干（ABCDE 显式步骤）：
  A. 备份目录 notes/_scraped/_backup_<作者>_<时间戳>/，迁移该作者全部补齐产物
     （fetch_results / risk_skip / backfill 运行日志）。
  B. 清 dedup 索引：按 URL hash（列表规范 URL）+ 标题双向包含匹配，双保险
     （URL 形态变体靠标题兜底；index 只存 hash 不可反查，故两路都做）。
  C. 识别该 UP 的系列：扫描 notes/ 根目录系列文件夹内 *_raw.md 头部命中作者名
     （series_state 已随系列课去流程化退役，PLAN-20260908）。
  E. 迁移 notes/<系列> 文件夹到备份目录（raw/body 整体走，重抓重新生成）。

用法：
  python scripts/reset_up_backfill.py --uid 1750569201 --author 趋势浪子 --dry   # 预览
  python scripts/reset_up_backfill.py --uid 1750569201 --author 趋势浪子 --apply
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SCRAPED = os.path.join(ROOT, "notes", "_scraped")
DEDUP_INDEX = os.path.join(ROOT, ".cache", "dedup.json")


def _norm_title(s: str) -> str:
    return re.sub(r"[\s，。！？、：；“”‘’（）《》【】·…—\-_|,.!?:;'\"()\[\]（）]+", "", s or "").lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid", required=True)
    ap.add_argument("--author", required=True)
    ap.add_argument("--apply", action="store_true", help="默认 dry 预览；--apply 才真正执行")
    args = ap.parse_args()

    list_path = os.path.join(SCRAPED, f"bili_{args.uid}_videos.json")
    items = json.load(open(list_path, encoding="utf-8"))["items"]
    urls = [f"https://www.bilibili.com/video/{it['bvid']}" for it in items]
    titles = {_norm_title(it["title"]) for it in items}
    print(f"[plan] UP={args.author} uid={args.uid}，列表 {len(items)} 条")

    # ---- A. 备份目录 + 迁移补齐产物 ----
    backup = os.path.join(SCRAPED, f"_backup_{args.author}_{time.strftime('%Y%m%d_%H%M%S')}")
    moved_files = []
    for fn in (f"{args.author}_fetch_results.json", f"{args.author}_risk_skip.json"):
        p = os.path.join(SCRAPED, fn)
        if os.path.exists(p):
            moved_files.append((p, os.path.join(backup, fn)))
    for fn in os.listdir(SCRAPED):
        if fn.startswith(f"{args.author}_backfill_"):
            moved_files.append((os.path.join(SCRAPED, fn), os.path.join(backup, fn)))
    print(f"[A] 将迁移 {len(moved_files)} 个补齐产物 → {backup}")

    # ---- B. 清 dedup 索引 ----
    from articles import dedup
    from articles.dedup import _normalize_url, _hash
    index = dedup._load_index()
    url_hashes = {_hash(_normalize_url(u)) for u in urls}
    by_url, by_title = [], []
    for h, rec in index.items():
        if h in url_hashes:
            by_url.append(h)
        elif any(t and (t in _norm_title(rec.get("title", ""))
                        or _norm_title(rec.get("title", "")) in t)
                 for t in titles):
            by_title.append(h)
    print(f"[B] dedup 索引共 {len(index)} 条：URL 命中 {len(by_url)}，标题兜底命中 {len(by_title)}"
          f"，将清除 {len(by_url) + len(by_title)} 条")

    # ---- C. 识别该 UP 的系列（扫 notes/ 根目录系列文件夹；series_state 已退役）----
    hit_series = []
    notes_root = os.path.join(ROOT, "notes")
    if os.path.isdir(notes_root):
        for name in sorted(os.listdir(notes_root)):
            sdir = os.path.join(notes_root, name)
            if not os.path.isdir(sdir) or name.startswith("_"):
                continue
            hit = False
            for fn in os.listdir(sdir):
                if not fn.endswith("_raw.md"):
                    continue
                try:
                    head = "".join(open(os.path.join(sdir, fn), encoding="utf-8").readlines()[:8])
                except Exception:
                    continue
                if args.author in head:
                    hit = True
                    break
            if hit:
                hit_series.append((name, sdir))
    print(f"[C] 命中作者「{args.author}」的系列 {len(hit_series)} 个：")
    for st, _ in hit_series:
        print(f"    - {st}")

    # ---- D/E. 执行 ----
    if not args.apply:
        print("\n[dry] 预览结束，未做任何改动。加 --apply 执行。")
        return
    os.makedirs(backup, exist_ok=True)
    for src, dst in moved_files:
        shutil.move(src, dst)
    for h in by_url + by_title:
        index.pop(h, None)
    dedup._save_index(index)
    for st, sdir in hit_series:
        dst = os.path.join(backup, "series_" + st)
        shutil.move(sdir, dst)
        print(f"[E] notes/{st} → 备份")
    print(f"\n✅ 重置完成。备份目录（可回滚）：{backup}")
    print(f"   dedup 索引剩 {len(index)} 条。")


if __name__ == "__main__":
    main()
