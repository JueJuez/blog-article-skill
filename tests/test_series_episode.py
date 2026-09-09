"""系列课单集五步管线测试（PLAN-20260908 阶段4.1）。

summarize_series_episode 五步：①登记表闸门（已总结跳过）→ ②1 次 view API 拿
系列名 + 全局集号（仅装饰：folder 路由 + 文件名「第NN集_」前缀，D4 不校验冲突）
→ ③字幕抓取（无 CC 自动 ASR 兜底）→ ④分类器总结 → ⑤落盘 + 登记——③④⑤
委托 _handle_single_video。本测试全 mock：不联网、不落盘、不读真实登记表。

运行：python -m pytest tests/test_series_episode.py -v
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import videos.fetch as vfetch
import videos.main as vm
from shared.routing import MYNOTES_ROOT

EP_URL = "https://www.bilibili.com/video/BV1xx411c7mD"
EP_BVID = "BV1xx411c7mD"
SEASON_TITLE = "Penny的爬虫实战"
AUTHOR = "测试UP主"
EP_PUBDATE = 1700000000
SEGMENTS = [{"text": "大家好，本期讲数据解析。", "start": 0.0}]
TRANSCRIPT = ("数据解析实战", SEGMENTS, AUTHOR)


def setUpModule():
    """防跨测试污染：单视频链路的全局副作用 LAST_PUBDATE 归零。"""
    vfetch.LAST_PUBDATE = 0


def _make_info(with_season: bool = True, pubdate: int = EP_PUBDATE) -> dict:
    """构造 view API 返回：2 个 section（3+2 集），目标集是全局第 4 集。"""
    info: dict = {
        "aid": 900, "cid": 901, "pubdate": pubdate,
        "title": "数据解析实战", "desc": "", "author": AUTHOR,
        "pages": [{"cid": 901, "page": 1, "part": ""}],
        "ugc_season": None,
    }
    if with_season:
        sec1 = [{"bvid": f"BVsec1_{i}", "title": f"入门{i}"} for i in range(1, 4)]
        sec2 = [{"bvid": EP_BVID, "title": "数据解析实战"},
                {"bvid": "BVsec2_2", "title": "进阶"}]
        info["ugc_season"] = {
            "title": SEASON_TITLE,
            "sections": [{"episodes": sec1}, {"episodes": sec2}],
        }
    return info


def _run(info_ret, registry_ret=None, transcript_ret=TRANSCRIPT, input_data=None):
    """统一 mock 脚手架：跑 summarize_series_episode，返回 (结果, 各 mock)。"""
    with mock.patch("videos.main.is_summarized", return_value=registry_ret) as m_gate, \
         mock.patch("videos.fetch._bili_get_video_info", return_value=info_ret) as m_info, \
         mock.patch("videos.fetch.fetch_transcript", return_value=transcript_ret) as m_ft, \
         mock.patch("videos.main._summarize_and_save",
                    return_value=("out/第04集_数据解析实战.md", "正文", False, "", "structured")) as m_save:
        res = vm.summarize_series_episode(EP_URL, dict(input_data or {}))
    return res, m_gate, m_info, m_ft, m_save


# ---------------------------------------------------------------------------
# 步骤①：登记表闸门
# ---------------------------------------------------------------------------

def test_gate_hit_skips_before_any_request():
    """已总结命中 → 直接返回 skipped，view API 与字幕抓取一个请求都不发。"""
    res, m_gate, m_info, m_ft, m_save = _run(
        _make_info(), registry_ret={"filename": "已存在.md"})
    assert res.get("skipped") is True, "命中登记表应返回 skipped"
    assert res.get("success") is True
    assert res.get("filename") == "已存在.md"
    m_info.assert_not_called(), "闸门命中后不应请求 view API"
    m_ft.assert_not_called(), "闸门命中后不应请求字幕"
    m_save.assert_not_called()


def test_force_bypasses_registry_gate():
    """force=True 时闸门放行：即使登记表命中也继续走完整总结。"""
    res, m_gate, m_info, m_ft, m_save = _run(
        _make_info(), registry_ret={"filename": "已存在.md"},
        input_data={"force": True})
    assert res.get("skipped") is None, "force 应绕过闸门，不返回 skipped"
    m_ft.assert_called_once(), "force 放行后应正常抓字幕"


# ---------------------------------------------------------------------------
# 步骤②：view API → 系列名（folder 路由）+ 全局集号（前缀装饰）+ 发布时间
# ---------------------------------------------------------------------------

def test_season_folder_routes_to_author_series():
    """非监控作者的单集 → folder =【我的总结】/作者/<名>/<系列名>。"""
    res, m_gate, m_info, m_ft, m_save = _run(_make_info())
    folder = m_save.call_args.kwargs.get("folder", "")
    assert folder == f"{MYNOTES_ROOT}/作者/{AUTHOR}/{SEASON_TITLE}", \
        f"系列单集应路由到作者/系列容器，实际：{folder}"
    assert res.get("success") is True


def test_episode_prefix_uses_global_index_across_sections():
    """目标集在第二 section 首位（全局第 4）→ title 前缀「第04集_」（D4 装饰）。"""
    res, m_gate, m_info, m_ft, m_save = _run(_make_info())
    title = m_save.call_args[0][2]
    assert title.startswith("第04集_"), \
        f"跨 section 全局集号应为 04，实际 title：{title}"
    assert res.get("success") is True


def test_publish_time_injected_from_view_api():
    """view API 的 pubdate 显式注入保存链路（不依赖 LAST_PUBDATE 副作用）。"""
    res, m_gate, m_info, m_ft, m_save = _run(_make_info(pubdate=EP_PUBDATE))
    assert m_save.call_args.kwargs.get("publish_time") == EP_PUBDATE, \
        "单集发布时间应取自 view API pubdate"


def test_transcript_segments_passed_to_summary():
    """步骤③④：抓到的字幕 segments 原样进入总结（委托链不丢内容）。"""
    res, m_gate, m_info, m_ft, m_save = _run(_make_info())
    assert m_save.call_args[0][0] == SEGMENTS, "字幕 segments 应透传给总结"
    assert m_ft.call_count == 1, "单集只抓一次字幕"


# ---------------------------------------------------------------------------
# 步骤②退化路径：无系列名 / view 失败 → 普通单视频路由，不阻塞总结
# ---------------------------------------------------------------------------

def test_no_season_falls_back_to_plain_single_route():
    """非系列视频（无 ugc_season）→ 无「第NN集_」前缀，folder 走普通路由。"""
    res, m_gate, m_info, m_ft, m_save = _run(_make_info(with_season=False))
    title = m_save.call_args[0][2]
    assert not title.startswith("第"), "无系列名不应拼集号前缀"
    folder = m_save.call_args.kwargs.get("folder", "")
    assert folder == f"{MYNOTES_ROOT}/作者/{AUTHOR}", \
        f"无系列名应退化为普通作者路由，实际：{folder}"
    assert res.get("success") is True


def test_view_api_failure_still_summarizes():
    """view API 失败（返回 None）→ 退化总结照常完成，不阻塞。"""
    res, m_gate, m_info, m_ft, m_save = _run(None)
    assert res.get("success") is True, "view 失败不应阻断总结"
    title = m_save.call_args[0][2]
    assert not title.startswith("第"), "拿不到集号不应拼前缀"


if __name__ == "__main__":
    import unittest
    unittest.main(module="tests.test_series_episode", argv=["x", "-v"], exit=False)
