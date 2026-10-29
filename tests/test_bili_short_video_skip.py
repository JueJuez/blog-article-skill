# -*- coding: utf-8 -*-
"""回归测试：B站短视频跳过（2026-10-30 用户定策）。

背景：20 秒碎片视频（无 CC）走 ASR 兜底失败入重试队列，反复白耗；
     写作门禁 MIN_WORDS=300 对这类源本来就不可能稳定达标。
策略：监控抓取侧按列表接口时长跳过 < BILI_MIN_DURATION_SECS（默认 60s）的视频，
     计入 video_short_skip；时长缺失/解析失败（-1）不拦，宁漏勿误杀。

本测试全部离线（不联网、不下载、不加载模型）。

运行：
    python tests/test_bili_short_video_skip.py     # 或 pytest
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# monitors/run.py 的同目录导入（status_store 等）依赖 monitors/ 在 sys.path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "monitors"))

from monitors.bilibili import _arc_length_secs


def test_length_parse_mmss():
    assert _arc_length_secs("02:38") == 158


def test_length_parse_hhmmss():
    assert _arc_length_secs("1:02:03") == 3723


def test_length_parse_bad_returns_minus1():
    assert _arc_length_secs(None) == -1
    assert _arc_length_secs("") == -1
    assert _arc_length_secs("abc") == -1


def _run_apply_with_video(it, monkeypatch=None):
    """把单条视频项喂进 apply_summaries 视频分支，返回 (stats, 调用记录)。

    隔离边界：refetch 队列指向临时文件、ASR 池打桩，测试不得触碰真实队列 /
    网络 / Chrome cookie。无 CC 的正常时长视频走 ASR 池桩（返回转写文本 → 总结桩）。
    """
    import monitors.run as mr

    calls = {"transcript": 0, "summarize": 0}

    def _no_transcript(url):
        calls["transcript"] += 1
        return None  # 视为无 CC → 走 ASR 池

    def _no_summarize(*a, **k):
        calls["summarize"] += 1

    def _fake_asr_batch(items, _max):
        return {x["url"]: "假转写文本" for x in items}, []

    if monkeypatch is not None:
        tmp_refetch = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_refetch_test.json")
        monkeypatch.setenv("MON_PENDING_REFETCH_PATH", tmp_refetch)

    with mock.patch("articles.dedup.batch_is_summarized", return_value=set()), \
         mock.patch("videos.fetch.fetch_transcript", _no_transcript), \
         mock.patch("asr_pool.run_asr_batch", _fake_asr_batch), \
         mock.patch.object(mr, "_summarize_video_item", _no_summarize):
        mr.apply_summaries([it], obsidian=False, consume_prev_refetch=False)
    return calls


def _fresh_video(duration_secs):
    return {"source": "bilibili", "route": "video", "id": "BV1TESTSHORT",
            "title": "拉完了，准备迎接暴击……", "mp_name": "舟亦横",
            "publish_time": 0, "url": "https://www.bilibili.com/video/BV1TESTSHORT",
            "is_charging": False, "charging_badge": "",
            "duration_secs": duration_secs}


def test_short_video_skipped_before_transcript(monkeypatch):
    calls = _run_apply_with_video(_fresh_video(20), monkeypatch)
    assert calls["transcript"] == 0, "短视频不应触碰字幕探测/ASR"
    assert calls["summarize"] == 0


def test_normal_video_passes_short_gate(monkeypatch):
    calls = _run_apply_with_video(_fresh_video(158), monkeypatch)
    assert calls["transcript"] == 1, "正常时长视频应照常探测字幕"
    assert calls["summarize"] == 1  # 无 CC（桩返回 None）→ 走 ASR 池前的桩


def test_missing_duration_does_not_block(monkeypatch):
    calls = _run_apply_with_video(_fresh_video(-1), monkeypatch)
    assert calls["transcript"] == 1, "时长未知（-1）不拦，宁漏勿误杀"
    assert calls["summarize"] == 1


def test_threshold_env_override(monkeypatch):
    monkeypatch.setenv("BILI_MIN_DURATION_SECS", "30")
    calls = _run_apply_with_video(_fresh_video(45), monkeypatch)
    assert calls["transcript"] == 1, "阈值调低后 45s 视频应放行"


if __name__ == "__main__":
    class _MP:  # 最小 monkeypatch 替身：只支持 setenv
        @staticmethod
        def setenv(k, v):
            os.environ[k] = v

    test_length_parse_mmss()
    test_length_parse_hhmmss()
    test_length_parse_bad_returns_minus1()
    test_short_video_skipped_before_transcript(_MP)
    test_normal_video_passes_short_gate(_MP)
    test_missing_duration_does_not_block(_MP)
    test_threshold_env_override(_MP)
    print("ALL PASS")
