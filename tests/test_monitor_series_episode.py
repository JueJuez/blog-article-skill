"""PLAN-20260908 阶段4.2：监控增量对系列单集的接线测试（RED）。

行为：monitors._summarize_video_item 改走 summarize_series_episode 五步管线——
① 系列单集路由到【监控】/<平台>/<账号>/<系列> 容器（新建）；
② 第二集 transcript 直喂（ASR 批量场景）复用同容器，不重复抓字幕，
   降级队列条目带系列路由与全局集号前缀；
③ 非系列单视频走日更路由，不回归。
"""
import os
import sys
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "monitors"))

import videos.fetch as vfetch  # noqa: E402
from monitors import run as mrun  # noqa: E402
from shared.routing import MONITOR_ROOT, DAILY  # noqa: E402

SEASON_TITLE = "Penny的爬虫实战"
AUTHOR = "测试UP主"
EP_PUBDATE = 1700000000
EP_BVID = "BV1xx411c7mD"
EP_URL = f"https://www.bilibili.com/video/{EP_BVID}"
SERIES_FOLDER = f"{MONITOR_ROOT}/B站/{AUTHOR}/{SEASON_TITLE}"
DAILY_FOLDER = f"{MONITOR_ROOT}/B站/{AUTHOR}/{DAILY}"

REGISTRY = {AUTHOR: {"platform": "bilibili", "monitored": True, "aliases": []}}


def setUpModule():
    vfetch.LAST_PUBDATE = 0  # 防跨测试全局副作用污染


def _make_info(ep_bvid: str, pos: int, layout=(3, 2)) -> dict:
    """view API mock 返回：layout 各 section 集数，目标集全局第 pos 集（1-based）。"""
    sections, n = [], 0
    for cnt in layout:
        sec = []
        for _ in range(cnt):
            n += 1
            sec.append({"bvid": f"BV{n:03d}"})
        sections.append({"episodes": sec})
    all_eps = [ep for sec in sections for ep in sec["episodes"]]
    all_eps[pos - 1]["bvid"] = ep_bvid
    return {"author": AUTHOR, "pubdate": EP_PUBDATE,
            "ugc_season": {"title": SEASON_TITLE, "sections": sections}}


def test_series_episode_routes_to_monitor_series_container():
    """系列首集（有 CC）：落盘 folder = 【监控】/B站/<账号>/<系列>（新建容器）。"""
    it = {"url": EP_URL, "title": "第4集 进阶用法", "sub_name": AUTHOR,
          "publish_time": 123, "route": "video"}
    with mock.patch("videos.main.is_summarized", return_value=None) as m_gate, \
         mock.patch("videos.fetch._bili_get_video_info",
                    return_value=_make_info(EP_BVID, pos=4)), \
         mock.patch("videos.fetch.fetch_transcript",
                    return_value=("爬虫进阶", ["seg1"], AUTHOR)), \
         mock.patch("videos.main._summarize_and_save",
                    return_value=("fn.md", "# ok", False, "", "structured")) as m_save, \
         mock.patch("shared.routing.load_account_registry", return_value=REGISTRY), \
         mock.patch("shared.routing.load_node_registry", return_value={}), \
         mock.patch("monitors.run._queue_pending_summary") as m_queue:
        stats = {"video": 0, "error": 0}
        mrun._summarize_video_item(it, True, stats)
    m_gate.assert_called()  # 登记表闸门已执行（管线①+单视频内部双闸门，幂等）
    m_queue.assert_called_once()
    assert stats["video"] == 1
    assert m_save.call_args.kwargs["folder"] == SERIES_FOLDER


def test_second_episode_reuses_container_via_transcript_queue():
    """第二集 transcript 直喂（ASR 批量）：复用同容器 + 队列条目带系列路由与前缀。"""
    it = {"url": EP_URL, "title": "爬虫第二集", "sub_name": AUTHOR,
          "author": AUTHOR, "publish_time": 456, "route": "video"}
    with mock.patch("videos.main.is_summarized", return_value=None), \
         mock.patch("videos.fetch._bili_get_video_info",
                    return_value=_make_info(EP_BVID, pos=2)), \
         mock.patch("videos.fetch.fetch_transcript") as m_ft, \
         mock.patch("videos.main._summarize_and_save",
                    return_value=("", "", True, "字幕正文", "key_points")) as m_save, \
         mock.patch("articles.main.save_raw_content_to_file",
                    return_value="notes/x_raw.md"), \
         mock.patch("shared.routing.load_account_registry", return_value=REGISTRY), \
         mock.patch("shared.routing.load_node_registry", return_value={}), \
         mock.patch("monitors.run._queue_pending_summary") as m_queue:
        mrun._summarize_video_item(it, True, {"video": 0, "error": 0},
                                    transcript="字幕文本")
    m_ft.assert_not_called()  # 已持转写文本，不重复抓字幕
    res = m_queue.call_args[0][1]
    assert res["folder"] == SERIES_FOLDER  # 与首集同容器（复用）
    assert res["original_title"].startswith("第02集_")  # 全局集号前缀进入队标题
    assert m_save.call_args.kwargs["folder"] == SERIES_FOLDER


def test_non_series_video_keeps_daily_folder():
    """非系列单视频：走日更路由，不回归。"""
    info = {"author": AUTHOR, "pubdate": EP_PUBDATE}  # 无 ugc_season
    it = {"url": EP_URL, "title": "普通单视频", "sub_name": AUTHOR,
          "publish_time": 789, "route": "video"}
    with mock.patch("videos.main.is_summarized", return_value=None), \
         mock.patch("videos.fetch._bili_get_video_info", return_value=info), \
         mock.patch("videos.fetch.fetch_transcript",
                    return_value=("普通标题", ["seg1"], AUTHOR)), \
         mock.patch("videos.main._summarize_and_save",
                    return_value=("fn.md", "# ok", False, "", "structured")) as m_save, \
         mock.patch("shared.routing.load_account_registry", return_value=REGISTRY), \
         mock.patch("shared.routing.load_node_registry", return_value={}), \
         mock.patch("monitors.run._queue_pending_summary"):
        mrun._summarize_video_item(it, True, {"video": 0, "error": 0})
    assert m_save.call_args.kwargs["folder"] == DAILY_FOLDER
