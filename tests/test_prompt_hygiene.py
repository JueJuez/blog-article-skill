# -*- coding: utf-8 -*-
"""Prompt 卫生回归测试（2026-09-05 模板质量评估产出修复）。

覆盖两类问题：
1. 固定结构段顶层编号错位：READING_PROMPT 曾出现双「6.」；修复过程曾误伤 ROUNDUP
   （多模板同构段，替换必须锚定模板独有上下文）。故对全部 7 个带「固定结构」段
   的模板统一断言编号严格递增。
2. QUALITY_GATE_SELFCHECK 六条中 ①②③⑥ 与 UNIVERSAL_RULES §八红线近乎逐字重复，
   合并后只保留两条非重叠专项（思维模型深挖 + 模板固定结构）。
"""

import re

import pytest

from prompts.templates import (
    CASE_PROMPT,
    DISSECTION_PROMPT,
    INTERVIEW_PROMPT,
    KEY_POINTS_PROMPT,
    OPINION_PROMPT,
    QUALITY_GATE_SELFCHECK,
    READING_PROMPT,
    ROUNDUP_PROMPT,
    UNIVERSAL_RULES,
)

_STRUCTURED_TEMPLATES = [
    KEY_POINTS_PROMPT,
    CASE_PROMPT,
    OPINION_PROMPT,
    INTERVIEW_PROMPT,
    ROUNDUP_PROMPT,
    READING_PROMPT,
    DISSECTION_PROMPT,
]

_TEMPLATE_NAMES = {
    id(t): n
    for t, n in zip(
        _STRUCTURED_TEMPLATES,
        ["key_points", "case", "opinion", "interview", "roundup", "reading", "dissection"],
    )
}


def _fixed_structure_section(prompt: str) -> str:
    """截取「# 固定结构」到其后的「# 提炼规范」段，排除尾部拼接的通用规范。"""
    start = prompt.index("# 固定结构")
    end = prompt.index("提炼规范", start)  # KEY_POINTS 尾段为「排版 & 提炼规范」，不带「# 」前缀
    return prompt[start:end]


def _top_level_numbers(section: str) -> list[int]:
    """提取顶格有序编号行（子列表缩进、代码块内容均不匹配）。"""
    return [int(m) for m in re.findall(r"^(\d+)\. ", section, re.MULTILINE)]


class TestTemplateNumbering:
    @pytest.mark.parametrize("prompt", _STRUCTURED_TEMPLATES, ids=_TEMPLATE_NAMES.values())
    def test_fixed_structure_numbering_strictly_increasing(self, prompt):
        """全部模板固定结构顶层编号必须严格递增 1..N，无重复、无跳号。"""
        numbers = _top_level_numbers(_fixed_structure_section(prompt))
        assert numbers == list(range(1, len(numbers) + 1))

    def test_reading_final_item_is_nine(self):
        """READING 修复后共 9 条，「总结收束」为末条（此前双 6 错位致末条为 8）。"""
        section = _fixed_structure_section(READING_PROMPT)
        assert "9. **总结收束**" in section

    def test_tail_splice_untouched(self):
        """编号修复不得影响尾部 UNIVERSAL_RULES 拼接契约（test_templates.py 同款锚点）。"""
        tail = UNIVERSAL_RULES[-40:]
        assert READING_PROMPT.endswith(tail)


class TestQualityGateSelfcheckDedup:
    def test_no_overlap_with_universal_section8(self):
        """gate 自检不得再重复 §八已有红线（标题级空话/目录式复述/万能概括句/凑数等）。"""
        overlap_phrases = [
            "标题级空话",
            "目录式复述",
            "万能概括句",
            "凑数",
            "来龙去脉",
            "笔记者推断",
            "无编造",
        ]
        hits = [p for p in overlap_phrases if p in QUALITY_GATE_SELFCHECK]
        assert hits == [], f"gate 自检与 §八 重复的短语：{hits}"

    def test_keeps_unique_items(self):
        """非重叠专项必须保留：思维模型深挖（原④）+ 模板固定结构（原⑤）。"""
        assert "思维模型" in QUALITY_GATE_SELFCHECK
        assert "固定结构" in QUALITY_GATE_SELFCHECK

    def test_remains_suffix_composable(self):
        """gate 自检仍以换行开头，保持「get_note_prompt(...) + QUALITY_GATE_SELFCHECK」可拼接。"""
        assert QUALITY_GATE_SELFCHECK.startswith("\n")
