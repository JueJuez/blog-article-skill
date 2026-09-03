#!/usr/bin/env python3
"""列出某 B站 UP 的全部视频（复用 monitors.bilibili 的 WBI 签名列表拉取），支持切片输出。

用途：一次性抓取非订阅 UP 的全部视频时，先把列表编号落盘，多会话按 --start/--end 分片，
再逐条 `python videos/run.py --url <url>` 总结（videos/run.py 自带去重，区间重叠也安全）。

用法：
    python scripts/list_up_videos.py --uid 1750569201                # 全量列表
    python scripts/list_up_videos.py --uid 1750569201 --start 81 --end 160
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
from monitors.bilibili import _fetch_vlist  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid", required=True, help="B站 UP 的数字 UID")
    ap.add_argument("--start", type=int, default=1, help="起始序号（1-based，含）")
    ap.add_argument("--end", type=int, default=0, help="结束序号（含）；0=到最后")
    ap.add_argument("--out", default="", help="输出文件路径（默认 notes/_scraped/bili_<uid>_videos.json）")
    ap.add_argument("--refresh", action="store_true", help="忽略已有缓存文件重新拉列表")
    args = ap.parse_args()

    out_path = args.out or os.path.join(ROOT, "notes", "_scraped", f"bili_{args.uid}_videos.json")
    items = None
    if not args.refresh and os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("uid") == args.uid and cached.get("items"):
            items = cached["items"]
            print(f"♻️ 使用缓存列表 {out_path}（{len(items)} 条，--refresh 可重拉）")

    if items is None:
        print(f"🌐 拉取 UP {args.uid} 全部视频列表（WBI 签名，低频翻页）...")
        vlist = _fetch_vlist(args.uid, ps=50, paginate=True)
        if not vlist:
            print("❌ 列表拉取失败或为空（检查 BILI_COOKIE / 风控）", file=sys.stderr)
            sys.exit(1)
        # 按发布时间升序编号（老→新），序号稳定，多会话分片口径一致
        vlist.sort(key=lambda x: x.get("created", 0))
        items = [{"idx": i + 1, "bvid": it.get("bvid"), "title": (it.get("title") or "").strip(),
                  "length": it.get("length", ""), "created": it.get("created", 0)}
                 for i, it in enumerate(vlist) if it.get("bvid")]
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"uid": args.uid, "total": len(items), "generated_at": int(time.time()),
                       "items": items}, f, ensure_ascii=False, indent=1)
        print(f"✅ 列表已落盘 {out_path}（共 {len(items)} 条，按发布时间升序编号）")

    end = args.end or len(items)
    chunk = [it for it in items if args.start <= it["idx"] <= end]
    print(f"\n📋 本次切片 [{args.start}-{end}]：{len(chunk)} 条")
    for it in chunk:
        print(f"  #{it['idx']:>3} https://www.bilibili.com/video/{it['bvid']}  {it['title']}")


if __name__ == "__main__":
    main()
