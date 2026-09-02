# -*- coding: utf-8 -*-
"""跨来源去重（公众号 ↔ scys）单测：find_cross_duplicate 的命中/放行语义。"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from articles import dedup


def _with_archive(archive, fn):
    with mock.patch.object(dedup, "_scys_archive_cache", archive):
        return fn()


class TestCrossSourceDedup(unittest.TestCase):
    ARCHIVE = [(
        dedup.normalize_title("从没做过小程序，到 9000 元接单 10 天交付客户项目：我是怎么用 AI Coding 跑通闭环"),
        dedup.normalize_title("16 年全栈开发，却从没做过小程序。先说结论：能跑通，但流程和想象完全不一样。")[:300],
        "从没做过小程序，到 9000 元接单 10 天交付客户项目：",
    )]

    def test_same_article_title_hit(self):
        """同一篇帖子（标题一致，含标点差异）→ 命中。"""
        res = _with_archive(self.ARCHIVE, lambda: dedup.find_cross_duplicate(
            title="从没做过小程序，到 9000 元接单 10 天交付客户项目：我是怎么用 AI Coding 跑通闭环！"))
        self.assertTrue(res)
        self.assertEqual(res["via"], "title")

    def test_truncated_wechat_title_hit(self):
        """公众号 64 字截断标题（互为前缀）→ 命中。"""
        res = _with_archive(self.ARCHIVE, lambda: dedup.find_cross_duplicate(
            title="从没做过小程序，到 9000 元接单 10 天交付客户项目：我是怎么用 AI Codin"))
        self.assertTrue(res)

    def test_similar_content_hit(self):
        """标题泛化但正文前缀高度相似 → 经 content 命中。"""
        res = _with_archive(self.ARCHIVE, lambda: dedup.find_cross_duplicate(
            title="一个接单复盘",
            content="16 年全栈开发，却从没做过小程序。先说结论：能跑通，但流程和想象完全不一样。" + "x" * 300))
        self.assertTrue(res)
        self.assertEqual(res["via"], "content")

    def test_unrelated_article_passes(self):
        """无关文章 → 不命中。"""
        res = _with_archive(self.ARCHIVE, lambda: dedup.find_cross_duplicate(
            title="说好的最后一轮A股组合盘点", content="今天把组合里的持仓再盘一遍。"))
        self.assertEqual(res, {})

    def test_short_title_no_crash(self):
        """过短标题 + 空正文 → 直接放行，不误伤。"""
        res = _with_archive(self.ARCHIVE, lambda: dedup.find_cross_duplicate(title="1", content=""))
        self.assertEqual(res, {})


if __name__ == "__main__":
    unittest.main()
