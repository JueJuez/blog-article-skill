"""test_refetch_enqueued.py — pending_refetch 队列 enqueued_at 时间戳注入的单测。

对应 monitors/run.py:_stamp_refetch_enqueued_at：
只给缺 enqueued_at 的条目补入队时间戳；历史队列恢复的条目保留最初入队时间，
供下游区分「本轮新增待重试」与历史遗留。默认时间由调用方注入，可测可控。
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "monitors"))

from monitors.run import _stamp_refetch_enqueued_at  # noqa: E402


class StampRefetchEnqueuedAtTest(unittest.TestCase):
    """正常/边界/异常三类场景。"""

    def test_new_item_gets_timestamp(self):
        """正常：缺 enqueued_at 的新条目被补上指定写入时刻。"""
        items = [{"url": "u1", "title": "t1"}]
        _stamp_refetch_enqueued_at(items, now=1000)
        self.assertEqual(items[0]["enqueued_at"], 1000)

    def test_historical_item_keeps_original_timestamp(self):
        """边界：历史队列恢复的条目保留最初入队时间，不被覆盖。"""
        original = 500
        items = [{"url": "u2", "title": "t2", "enqueued_at": original}]
        _stamp_refetch_enqueued_at(items, now=2000)
        self.assertEqual(items[0]["enqueued_at"], original)

    def test_mixed_items_stamp_only_missing(self):
        """边界：新旧混合时只给缺字段的条目补时间戳。"""
        items = [
            {"url": "u3", "title": "t3", "enqueued_at": 10},
            {"url": "u4", "title": "t4"},
        ]
        _stamp_refetch_enqueued_at(items, now=99)
        self.assertEqual(items[0]["enqueued_at"], 10)
        self.assertEqual(items[1]["enqueued_at"], 99)

    def test_empty_list_ok(self):
        """边界：空队列不报错，原地返回空列表。"""
        items = []
        self.assertEqual(_stamp_refetch_enqueued_at(items, now=1), [])

    def test_unrelated_fields_untouched(self):
        """异常：不影响条目其余字段，返回的仍是同一批条目对象。"""
        items = [{"url": "u5", "title": "t5", "refetch_count": 3}]
        out = _stamp_refetch_enqueued_at(items, now=7)
        self.assertIs(out, items)
        self.assertEqual(items[0]["url"], "u5")
        self.assertEqual(items[0]["refetch_count"], 3)
        self.assertEqual(items[0]["enqueued_at"], 7)

    def test_default_now_uses_now(self):
        """异常/兜底：不传 now 时用当前时间戳（epoch 秒，整数）。"""
        items = [{"url": "u6", "title": "t6"}]
        _stamp_refetch_enqueued_at(items)
        self.assertIsInstance(items[0]["enqueued_at"], int)


if __name__ == "__main__":
    unittest.main()