"""一次性定向抓取：仅对指定 UP 主抓取其内容并入降级队列（不写飞书）。

- 价投小猪仔：all_videos=True（首跑全量，~101 视频，分页拉全 + 取消上限）
- 笨笨的韭菜：window_days=7（首跑仅近 7 天）

复用真实管线 BilibiliSource.discover + run.apply_summaries；FORCE_AGENT_MODE=1 时
apply_summaries 只抓正文/字幕并入 pending（folder 已由统一路由器算成新结构），不写飞书，
后续由执行模型 drain 落盘。
"""
import sys
import os
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, ".env"))  # 必须在导入 bilibili 前加载 .env（bilibili 在 import 时即读 BILI_COOKIE）

from monitors.state import load_state, save_state
from monitors.bilibili import BilibiliSource
from monitors.run import apply_summaries

TARGETS = [
    {"uid": "3707002469485044", "name": "价投小猪仔", "all_videos": True},
    {"uid": "11473291", "name": "笨笨的韭菜", "all_videos": False, "window_days": 7},
]


def main():
    state = load_state()
    all_items = []
    for t in TARGETS:
        print(f"\n=== 抓取 {t['name']} (all_videos={t.get('all_videos', False)}, "
              f"window={t.get('window_days', '首跑默认')}天) ===", flush=True)
        src = BilibiliSource(
            t["uid"],
            all_videos=t.get("all_videos", False),
            window_days=t.get("window_days"),
        )
        found = src.discover(state, first_run_limit=100000, mode="first")
        print(f"  发现 {len(found)} 条（视频+动态）", flush=True)
        all_items.extend(found)
        time.sleep(6)  # 跨源退避，规避 -352
    print(f"\n总计发现 {len(all_items)} 条，进入 apply（抓正文/字幕并入队，不写飞书）...", flush=True)
    apply_summaries(all_items, obsidian=False)
    save_state(state)
    print("✅ 抓取+入队完成。pending 队列已含新结构 folder，待执行模型 drain 落飞书。")


if __name__ == "__main__":
    main()
