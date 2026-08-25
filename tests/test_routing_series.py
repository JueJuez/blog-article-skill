"""路由系列归类的回归测试（2026-08-25 决策）：

保护需求：
1. 监控账号在 subscriptions.json 配置 series_patterns 后，标题命中关键词的内容
   路由到「【监控】/<平台>/<账号>/<系列名>/」，未命中的落到「日更」。
2. bilibili 展示名（带空格，如「Mark Huang」）与订阅规范名（带下划线「Mark__Huang」）
   经 _match_account 归一化互认，确保活路径上系列匹配真实触发。
3. 日更节点用纯名「日更」（无【】括号），对齐飞书既有存量节点。
"""
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from shared import routing


def _item(account_display, title, platform="bilibili"):
    route = "video" if platform == "bilibili" else "article"
    source = "monitor_video" if platform == "bilibili" else "monitor_wechat"
    return {
        "author": account_display,
        "mp_name": account_display,
        "title": title,
        "source": source,
        "route": route,
        "url": "https://www.bilibili.com/video/BV1xx" if platform == "bilibili"
        else "https://mp.weixin.qq.com/s/abc",
    }


class TestLiveReplaySeries:
    """直播回放关键词：笨笨的韭菜 / 舟亦横 / Mark__Huang 都应命中系列。"""

    @pytest.mark.parametrize("account,title,expect", [
        ("Mark Huang", "【直播回放】2026年8月21日市场复盘",
         "【监控】/B站/Mark__Huang/直播回放"),
        ("笨笨的韭菜", "【直播回放】第3期 韭菜成长记",
         "【监控】/B站/笨笨的韭菜/直播回放"),
        ("舟亦横", "【直播回放】周末答疑",
         "【监控】/B站/舟亦横/直播回放"),
    ])
    def test_live_replay_hits_series(self, account, title, expect):
        assert routing.resolve_folder(_item(account, title)) == expect

    @pytest.mark.parametrize("account,title,expect", [
        ("Mark Huang", "我的交易系统分享", "【监控】/B站/Mark__Huang/日更"),
        ("笨笨的韭菜", "今天又绿了（日常吐槽）", "【监控】/B站/笨笨的韭菜/日更"),
        ("舟亦横", "盘中观察", "【监控】/B站/舟亦横/日更"),
    ])
    def test_non_series_falls_to_daily(self, account, title, expect):
        assert routing.resolve_folder(_item(account, title)) == expect


class TestAccountNameNormalization:
    """展示名（空格）↔ 规范名（下划线）互认，是系列命中的前提。"""

    def test_mark_display_normalizes_to_canonical(self):
        # 活路径上 bilibili 条目 author 是展示名「Mark Huang」
        assert routing._match_account(routing.load_account_registry(), "Mark Huang") == "Mark__Huang"

    def test_match_series_fires_via_display_name(self):
        # 直接走 match_series，account 用展示名也应命中
        assert routing.match_series("Mark Huang", "【直播回放】xxx") == "直播回放"


class TestDailyNodeName:
    """日更节点用纯名，无括号。"""

    def test_daily_constant_is_plain(self):
        assert routing.DAILY == "日更"
