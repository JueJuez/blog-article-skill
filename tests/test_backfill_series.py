"""PLAN-20260908 阶段4.3：系列课点名补齐命令（RED）。

行为：scripts/backfill_series.py —— 「补齐 <UP> 的系列课」给系列任一集 URL：
① 1 次 view API 列全集 → ② 登记表过滤已总结 → ③ D8 护栏（失败账本累计 3 次
转人工，队列存量条目视为上轮失败 +1）→ ④ 剩余集逐条抓字幕入 pending_summaries
降级队列（folder=系列容器路由 + 第NN集_ 前缀 + prompt 预计算）→ 子 Agent 消费。
"""
import json
import os
import sys
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import scripts.backfill_series as bs  # noqa: E402
from shared.routing import MONITOR_ROOT  # noqa: E402
from prompts.templates import QUALITY_GATE_SELFCHECK  # noqa: E402

SEASON = "Penny的爬虫实战"
AUTHOR = "测试UP主"
EP_BVID = "BV1xx411c7mD"
SEASON_URL = f"https://www.bilibili.com/video/{EP_BVID}"
SERIES_FOLDER = f"{MONITOR_ROOT}/B站/{AUTHOR}/{SEASON}"


def _ep_urls() -> list:
    """_info(layout=(2,2)) 展开出的全集 URL（第 1 集即输入 URL）。"""
    return [SEASON_URL,
            "https://www.bilibili.com/video/BV002",
            "https://www.bilibili.com/video/BV003",
            "https://www.bilibili.com/video/BV004"]


def _info(layout=(2, 2)) -> dict:
    """view API mock 返回：layout 各 section 集数，第 1 集替换为输入 bvid。"""
    sections, n = [], 0
    for cnt in layout:
        sec = []
        for _ in range(cnt):
            n += 1
            sec.append({"bvid": f"BV{n:03d}"})
        sections.append({"episodes": sec})
    all_eps = [ep for s in sections for ep in s["episodes"]]
    all_eps[0]["bvid"] = EP_BVID
    return {"author": AUTHOR, "pubdate": 1700000000,
            "ugc_season": {"title": SEASON, "sections": sections}}


REGISTRY = {AUTHOR: {"platform": "bilibili", "monitored": True, "aliases": []}}


def _fake_transcript(url: str, fail_urls: set = None):
    """按 URL 返回字幕；fail_urls 中的 URL 返回 None（模拟抓取失败）。"""
    fail_urls = fail_urls or set()
    if url in fail_urls:
        return None
    n = url.rsplit("BV", 1)[-1].lstrip("0") or "1"
    return (f"标题{n}", [{"text": f"台词{n}"}], AUTHOR)


def _read_json(path) -> list:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _common_patches(tmp_dir: str, ep2_hit: bool, info: dict, fail_urls: set = None):
    """公共 mock 面：返回 (patch 上下文列表构造器, 待解包 mock 对象字典)。"""
    hit = {_ep_urls()[1]} if ep2_hit else set()
    ctx = [
        mock.patch.dict(os.environ, {"MON_PENDING_SUMMARY_PATH":
                                     os.path.join(tmp_dir, "pending.json")}),
        mock.patch.object(bs, "FAILURES_PATH",
                          os.path.join(tmp_dir, "failures.json")),
        mock.patch("videos.fetch._bili_get_video_info", return_value=info),
        mock.patch("videos.fetch.fetch_transcript",
                   side_effect=lambda u: _fake_transcript(u, fail_urls)),
        mock.patch("articles.main.save_raw_content_to_file",
                   return_value=os.path.join(tmp_dir, "x_raw.md")),
        mock.patch("articles.dedup.batch_is_summarized", return_value=hit),
        mock.patch("shared.routing.load_account_registry", return_value=REGISTRY),
        mock.patch("shared.routing.load_node_registry", return_value={}),
    ]
    return ctx


def test_backfill_lists_season_filters_registry_and_enqueues(tmp_path):
    """正常主流程：列全集 → 已登记过滤 → 剩余抓字幕入队（容器路由+前缀+prompt）。"""
    urls = _ep_urls()
    ctx = _common_patches(str(tmp_path), ep2_hit=True, info=_info())
    for c in ctx:
        c.start()
    try:
        report = bs.backfill_series([SEASON_URL], gap=0)
    finally:
        for c in ctx:
            c.stop()
    assert report["episodes"] == 4
    assert report["skip_summarized"] == 1
    assert report["queued"] == 3
    pending = _read_json(os.path.join(str(tmp_path), "pending.json"))
    assert len(pending) == 3
    assert urls[1] not in [p["url"] for p in pending]  # 已登记集不入队
    first = pending[0]
    assert first["url"] == urls[0]
    assert first["title"].startswith("第01集_")
    assert first["folder"] == SERIES_FOLDER  # 系列容器路由（监控 UP）
    assert first["raw_file"]
    assert QUALITY_GATE_SELFCHECK and first["prompt"].endswith(QUALITY_GATE_SELFCHECK)
    assert first["note_type"]


def test_fail_limit_moves_episode_to_manual_review(tmp_path):
    """D8：账本 fail_count=3 → 不抓字幕不入队进转人工；fail_count=1 对比组正常重试。"""
    urls = _ep_urls()
    fails = os.path.join(str(tmp_path), "failures.json")
    with open(fails, "w", encoding="utf-8") as f:
        json.dump({urls[0]: {"fail_count": 3, "last_error": "历史失败"},
                   urls[2]: {"fail_count": 1, "last_error": "历史失败"}}, f)
    ctx = _common_patches(str(tmp_path), ep2_hit=False, info=_info())
    for c in ctx:
        c.start()
    try:
        report = bs.backfill_series([SEASON_URL], gap=0)
    finally:
        for c in ctx:
            c.stop()
    manual_urls = [m["url"] for m in report["manual"]]
    assert urls[0] in manual_urls
    pending = _read_json(os.path.join(str(tmp_path), "pending.json"))
    assert urls[0] not in [p["url"] for p in pending]
    assert report["queued"] == 3  # ep2 与 ep3（fail_count=1<3 继续重试）、ep4 均入队
    assert len(_read_json(os.path.join(str(tmp_path), "pending.json"))) == 3


def test_transcript_failure_bumps_ledger_and_continues(tmp_path):
    """边界：抓字幕失败 → 账本 +1（不入队），本轮继续处理其余集。"""
    urls = _ep_urls()
    ctx = _common_patches(str(tmp_path), ep2_hit=False, info=_info(),
                          fail_urls={urls[0]})
    for c in ctx:
        c.start()
    try:
        report = bs.backfill_series([SEASON_URL], gap=0)
    finally:
        for c in ctx:
            c.stop()
    with open(os.path.join(str(tmp_path), "failures.json"), encoding="utf-8") as f:
        fails = json.load(f)
    assert fails[urls[0]]["fail_count"] == 1
    assert fails[urls[0]]["last_error"]
    assert report["failed"] == 1
    assert report["queued"] == 3  # 其余 3 集不受影响
    pending = _read_json(os.path.join(str(tmp_path), "pending.json"))
    assert urls[0] not in [p["url"] for p in pending]


def test_queued_but_not_summarized_accumulates_failure(tmp_path):
    """D8：队列已有条目（上轮入队后仍未登记）→ 检出时 +1，达 3 转人工且不重复入队。"""
    urls = _ep_urls()
    fails = os.path.join(str(tmp_path), "failures.json")
    with open(fails, "w", encoding="utf-8") as f:
        json.dump({urls[0]: {"fail_count": 2, "last_error": "上轮失败"}}, f)
    with open(os.path.join(str(tmp_path), "pending.json"), "w", encoding="utf-8") as f:
        json.dump([{"url": urls[0], "title": "第01集_旧条目"}], f)
    ctx = _common_patches(str(tmp_path), ep2_hit=False, info=_info())
    for c in ctx:
        c.start()
    try:
        report = bs.backfill_series([SEASON_URL], gap=0)
    finally:
        for c in ctx:
            c.stop()
    with open(fails, encoding="utf-8") as f:
        fails_now = json.load(f)
    assert fails_now[urls[0]]["fail_count"] == 3
    assert urls[0] in [m["url"] for m in report["manual"]]
    pending = _read_json(os.path.join(str(tmp_path), "pending.json"))
    assert len(pending) == 4  # 旧条目保留，ep1 不重复入队，新增 ep2/ep3/ep4 三条
