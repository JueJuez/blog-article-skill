"""scys 非精华高价值互动门槛过滤测试：投锚/点赞组合替代纯阅读数。

背景（2026-08-21 用户决策）：阅读数会被官方指南/推送帖污染，不能单独作为
非精华帖的价值代理；改用互动组合——投锚（coinCount）或点赞（likeCount）
任一达标即视为高价值非精华，精华帖直通不受门槛约束。
校准依据：自媒体最新一页 26 精华 + 4 非精华样本，精华锚数 P50=61/P75=131、
赞 P50=169；非精华中「航海答疑总结」锚62/赞125 达精华中位（应抓），
软广与新帖 锚≤5/赞≤50（应滤）。默认门槛：锚≥30 或 赞≥80。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(BASE_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(BASE_DIR / "scripts"))

from scys_batch_fetch import filter_todo


def _item(tid: str, **kw) -> dict:
    base = {
        "topicId": tid, "showTitle": f"t{tid}", "isDigested": False,
        "readingCount": 1000, "likeCount": 0, "commentCount": 0,
        "coinCount": 0, "gmtCreate": 9999999999,
    }
    base.update(kw)
    return base


ENG = {"min_coin": 30, "min_like": 80}


class TestDigestedPassThrough:
    def test_digested_always_kept_even_zero_engagement(self):
        items = [_item("1", isDigested=True)]
        out = filter_todo(items, set(), 0, digested_only=False,
                          min_reading=0, engagement=ENG)
        assert [it["topicId"] for it in out] == ["1"]

    def test_digested_only_mode_excludes_nondigested_regardless_of_engagement(self):
        items = [_item("1", isDigested=False, coinCount=360, likeCount=502)]
        out = filter_todo(items, set(), 0, digested_only=True,
                          min_reading=0, engagement=ENG)
        assert out == []


class TestEngagementThresholds:
    def test_nondigested_coin_meets_threshold_kept(self):
        items = [_item("1", coinCount=30)]
        out = filter_todo(items, set(), 0, digested_only=False,
                          min_reading=0, engagement=ENG)
        assert [it["topicId"] for it in out] == ["1"]

    def test_nondigested_like_meets_threshold_kept(self):
        items = [_item("1", likeCount=80)]
        out = filter_todo(items, set(), 0, digested_only=False,
                          min_reading=0, engagement=ENG)
        assert [it["topicId"] for it in out] == ["1"]

    def test_nondigested_below_all_thresholds_filtered(self):
        items = [_item("1", coinCount=29, likeCount=79, commentCount=99)]
        out = filter_todo(items, set(), 0, digested_only=False,
                          min_reading=0, engagement=ENG)
        assert out == []

    def test_official_guide_high_reading_low_engagement_filtered(self):
        # 官方指南：阅读被推送拉高，但没人抛锚/点赞 → 滤掉
        items = [_item("1", readingCount=20000, coinCount=2, likeCount=50)]
        out = filter_todo(items, set(), 0, digested_only=False,
                          min_reading=0, engagement=ENG)
        assert out == []

    def test_no_digested_only_and_no_engagement_config_keeps_all(self):
        # engagement=None 且未开仅精华：保持旧的「全抓」语义（显式 --no-digested-only 全量）
        items = [_item("1"), _item("2", coinCount=99)]
        out = filter_todo(items, set(), 0, digested_only=False,
                          min_reading=0, engagement=None)
        assert len(out) == 2


class TestLegacyCompatibility:
    def test_missing_engagement_fields_treated_as_zero(self):
        # 旧列表快照没有 coin/comment 字段 → 按 0 处理不崩
        items = [{"topicId": "1", "showTitle": "old", "isDigested": False,
                  "readingCount": 500, "likeCount": 10, "gmtCreate": 9999999999}]
        out = filter_todo(items, set(), 0, digested_only=False,
                          min_reading=0, engagement=ENG)
        assert out == []

    def test_done_ids_dedup(self):
        items = [_item("1", isDigested=True), _item("2", isDigested=True)]
        out = filter_todo(items, {"1"}, 0, digested_only=True,
                          min_reading=0, engagement=None)
        assert [it["topicId"] for it in out] == ["2"]

    def test_time_window_still_applies(self):
        old = _item("1", isDigested=True, gmtCreate=0)
        out = filter_todo([old], set(), since_days=7, digested_only=True,
                          min_reading=0, engagement=None)
        assert out == []

    def test_min_reading_still_applies(self):
        items = [_item("1", isDigested=True, readingCount=99)]
        out = filter_todo(items, set(), 0, digested_only=True,
                          min_reading=100, engagement=None)
        assert out == []
