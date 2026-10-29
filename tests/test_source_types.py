# -*- coding: utf-8 -*-
"""源类型判定（路由策略）的回归用例。

策略口径：判据是「信息半衰期」，靠类别词的存在性判定，不带数值阈值；
因此这里测的是**类目归属与优先级**，不测任何阈值。
"""
import pytest

from shared import source_types as ST


class TestClassifySourceType:
    def test_tutorial_title_is_tutorial(self):
        d = ST.classify_source_type("手把手教学 - 用Claude Code 搭建自动化工作流", "")
        assert d["type"] == "tutorial"
        assert "手把手" in d["hits"]

    def test_ephemeral_title_is_ephemeral(self):
        d = ST.classify_source_type("【深度】中国核电、中国广核价值分析", "")
        assert d["type"] == "ephemeral"

    def test_framework_title_is_framework(self):
        d = ST.classify_source_type("交易的底层逻辑与方法论", "")
        assert d["type"] == "framework"

    def test_series_course_title_is_tutorial(self):
        """系列课（缠论/体系/波浪理论）按教程走两阶段：用户 2026-10-29 追加。"""
        d = ST.classify_source_type("缠论的底层逻辑，技术分析的核心点", "")
        assert d["type"] == "tutorial"
        assert "缠论" in d["hits"]

    def test_news_title_is_news(self):
        d = ST.classify_source_type("下周行情展望（周复盘）", "")
        assert d["type"] == "news"

    def test_macro_opinion_is_ephemeral(self):
        """黄金观点文 / 宏观研究：时效性结论，归 ephemeral。"""
        d = ST.classify_source_type("黄金不值得投资——黄金到底保得是什么值", "")
        assert d["type"] == "ephemeral"

    def test_tutorial_wins_over_ephemeral(self):
        """优先级：教程 > 时效（手把手教你做估值，仍按教程处理）。"""
        d = ST.classify_source_type("手把手教你用 DCF 做估值", "")
        assert d["type"] == "tutorial"

    def test_unclassified_returns_empty_type(self):
        d = ST.classify_source_type("一个没有类别词的长文标题", "正文也没有类别词。" * 20)
        assert d["type"] == ""
        assert d["hits"] == []

    def test_empty_input_is_unclassified(self):
        assert ST.classify_source_type("", "")["type"] == ""

    def test_body_head_is_scanned(self):
        """标题看不出时，正文开头仍可判定（宏观研究者常在首段点题）。"""
        d = ST.classify_source_type("", "本文讨论宏观数据与市场逻辑……")
        assert d["type"] == "ephemeral"

    def test_body_beyond_head_is_ignored(self):
        """正文只在开头一段取样：避免全文偶发词把长文误判成时效。"""
        d = ST.classify_source_type("技术随笔", "本文讲工程实现。" + "估值" * 50)
        assert d["type"] == ""


class TestRouteOfType:
    @pytest.mark.parametrize("type_name", ["ephemeral", "framework", "news"])
    def test_non_tutorial_types_route_single(self, type_name):
        assert ST.route_for_type(type_name) == "single"

    @pytest.mark.parametrize("type_name", ["tutorial", ""])
    def test_tutorial_and_unclassified_route_distill(self, type_name):
        assert ST.route_for_type(type_name) == "distill"

    def test_unknown_type_falls_back_to_distill(self):
        """未知类别不得静默降级：未识别一律按可沉淀处理。"""
        assert ST.route_for_type("no_such_type") == "distill"


class TestKeywordTableHygiene:
    def test_table_has_no_duplicate_words(self):
        for kws in ST.KEYWORDS.values():
            assert len(kws) == len(set(kws))

    def test_priority_covers_all_types(self):
        assert set(ST.PRIORITY) == set(ST.KEYWORDS)
