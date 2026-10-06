# -*- coding: utf-8 -*-
"""质量纪律 + chart 数据块守护测试（2026-10-06）。

锁定三件事：
1. QUALITY_DISCIPLINE_RULES（推广识别/文风禁令/来源绑定/chart 块）单一真源，
   必须拼进全部 9 个模板（7 轻模板走 UNIVERSAL_RULES §七之四，structured/general 走 §十九）。
2. 每个模板带各自的组织主线提示（Profile 吸收），且不得破坏
   「prompt 以 UNIVERSAL_RULES 结尾」的拼接契约（test_prompt_hygiene 同款锚点）。
3. chart 块格式约定：JSON 单行、type 三选一、数字取自原文的措辞存在。
"""

from prompts.templates import (
    CASE_PROMPT,
    CONTENT_SUMMARY_PROMPT,
    DISSECTION_PROMPT,
    INTERVIEW_PROMPT,
    KEY_POINTS_PROMPT,
    NOTE_TEMPLATES,
    OPINION_PROMPT,
    QUALITY_DISCIPLINE_RULES,
    READING_PROMPT,
    ROUNDUP_PROMPT,
    UNIVERSAL_RULES,
)

_ALL_PROMPTS = {k: v["prompt"] for k, v in NOTE_TEMPLATES.items()}


class TestQualityDisciplineSplicedEverywhere:
    def test_constant_has_four_sections(self):
        for anchor in ["商业推广识别", "文风禁令", "来源绑定", "chart"]:
            assert anchor in QUALITY_DISCIPLINE_RULES

    def test_universal_rules_contains_discipline_section(self):
        assert "七之四、质量纪律" in UNIVERSAL_RULES

    def test_all_templates_contain_discipline(self):
        for name, p in _ALL_PROMPTS.items():
            assert "商业推广识别与标注" in p, name
            assert "禁用" in p and "而是" in p, name  # 文风禁令核心句
            assert "来源绑定与溯源" in p, name

    def test_dissection_promo_exception_preserved(self):
        """dissection 拆解以推广打法为素材，例外条款必须随常量一起在场。"""
        assert "dissection（创作解剖）例外" in DISSECTION_PROMPT


class TestProfileHints:
    def test_each_template_has_own_hint(self):
        hints = {
            "structured": "机制链",
            "key_points": "前置条件",
            "case": "证据强度",
            "opinion": "最强反驳",
            "interview": "叙事线",
            "reading": "论证线",
            "roundup": "对比矩阵",
            "dissection": "结构模具",
        }
        for name, anchor in hints.items():
            assert anchor in _ALL_PROMPTS[name], name

    def test_light_templates_still_end_with_universal_rules(self):
        tail = UNIVERSAL_RULES[-40:]
        for p in (KEY_POINTS_PROMPT, CASE_PROMPT, OPINION_PROMPT,
                  INTERVIEW_PROMPT, ROUNDUP_PROMPT, READING_PROMPT, DISSECTION_PROMPT):
            assert p.endswith(tail)


class TestChartBlock:
    def test_chart_format_contract(self):
        assert '"type"' in QUALITY_DISCIPLINE_RULES
        assert '"labels"' in QUALITY_DISCIPLINE_RULES
        assert '"data"' in QUALITY_DISCIPLINE_RULES
        for t in ("bar", "line", "pie"):
            assert t in QUALITY_DISCIPLINE_RULES

    def test_chart_in_heavy_and_light_paths(self):
        assert "```chart" in CONTENT_SUMMARY_PROMPT
        assert "```chart" in KEY_POINTS_PROMPT

    def test_no_fabricated_numbers_rule(self):
        assert "原样取自原文" in QUALITY_DISCIPLINE_RULES
