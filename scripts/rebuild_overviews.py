"""scripts/rebuild_overviews.py — 重建监控账号容器的总览索引（历史数据兜底）。

用法：
  python scripts/rebuild_overviews.py --folder "【监控】/公众号/哥飞"
  python scripts/rebuild_overviews.py --folder "【监控】/B站/价投小猪仔"
  python scripts/rebuild_overviews.py --all          # 重建 state 里记录的所有 folder

场景：用户在飞书手动补的历史节点（非本系统落盘）没有进总览，或总览被改乱，
扫容器下所有子节点按发布时间重建总览。一次性操作，每个节点 fetch 一次正文取日期。
"""
import os
import sys
import json
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

from articles.feishu import FeishuOutput
from shared import feishu_overview as fo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default="", help="要重建的 folder 路径，如 【监控】/公众号/哥飞")
    ap.add_argument("--all", action="store_true", help="重建 state 里记录的所有 folder")
    args = ap.parse_args()

    f = FeishuOutput()
    if not f.is_available():
        print("⚠️ 飞书不可用，退出")
        sys.exit(1)

    if args.all:
        db = fo._load()
        folders = list(db.keys())
        print(f"重建 state 中的 {len(folders)} 个 folder")
    elif args.folder:
        folders = [args.folder]
    else:
        print("⚠️ 请指定 --folder <路径> 或 --all")
        sys.exit(1)

    for folder in folders:
        # 推导 parent_token：folder 逐级建/取
        dirs = [d for d in folder.split("/") if d]
        parent_token = f.ensure_folder_path(dirs) if dirs else f.wiki_parent_node
        if not parent_token:
            print(f"  ⚠️ 找不到容器节点，跳过：{folder}")
            continue
        n = fo.rebuild(folder, parent_token=parent_token)
        print(f"  ✅ {folder} → 重建 {n} 条到总览")


if __name__ == "__main__":
    main()
