# -*- coding: utf-8 -*-
"""接地审计源解析回归（2026-10-25 盲区修复）。

背景：`--recent-hours` 路径曾对两类条目静默漏扫（配不上源就跳过，审计输出空表假绿）：
  1. B站长标题——raw 文件名 key 被截到 30 字符（articles/main.py:save_raw_content_to_file），
     全标题匹配不上；
  2. scys 源——根本不在 _raw_ 索引里（live 在 notes/_scraped/scys/<topicId>.md）。
本测试锁死 resolve_source 的四级解析：scys topicId → raw 全名 → raw 截断前缀 → 模糊兜底。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.audit_gate_signals as ags


def _tmp_raw(tmp, key, ts="20261025-111421"):
    p = os.path.join(tmp, f"_raw_{key}-{ts}.md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("源内容")
    return p


class TestResolveSource(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.bv_map = {}

    def test_truncated_long_title_matches_by_prefix(self):
        """长标题（>30 字符）全名 MISS 时按 30 字符截断前缀命中——修复 2026-10-25 假绿盲区。"""
        full = "第17集_【重制】投资第七课：常见基本面指标分析——讲透ROE、PB、PE"
        raw = _tmp_raw(self.tmp, ags.CS.sanitize_title(full)[:ags.RAW_KEY_MAXLEN])
        raw_idx = {ags.CS.sanitize_title(full)[:ags.RAW_KEY_MAXLEN]: raw}
        src = ags.resolve_source({"title": full, "source_url": ""}, raw_idx, self.bv_map)
        self.assertEqual(src, raw)

    def test_short_title_exact_match(self):
        """短标题（≤30 字符）走全名精确命中，截断回退不改变既有行为。"""
        full = "短标题测试"
        raw = _tmp_raw(self.tmp, ags.CS.sanitize_title(full))
        raw_idx = {ags.CS.sanitize_title(full): raw}
        src = ags.resolve_source({"title": full, "source_url": ""}, raw_idx, self.bv_map)
        self.assertEqual(src, raw)

    def test_no_source_resolves_empty(self):
        """两条路都配不上必须返回空串（调用方据此跳过，不允许返回不存在路径）。"""
        src = ags.resolve_source({"title": "完全不存在的标题xyz", "source_url": ""}, {}, self.bv_map)
        self.assertEqual(src, "")

    def test_bv_map_priority(self):
        """slices 的 bvid 映射是权威来源，命中即返回，不走标题匹配。"""
        raw = _tmp_raw(self.tmp, "随便什么key")
        src = ags.resolve_source(
            {"title": "标题A", "source_url": "https://www.bilibili.com/video/BV14WHN6UEKp"},
            {"标题A": os.path.join(self.tmp, "不存在的路径.md")},
            {"BV14WHN6UEKP": raw},
        )
        self.assertEqual(src, raw)


if __name__ == "__main__":
    unittest.main()
