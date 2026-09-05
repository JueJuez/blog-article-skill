# -*- coding: utf-8 -*-
"""学习体验包回归测试（2026-09-05）。

学习金字塔映射（被动 → 主动，只取定性排序，不取缺科学依据的百分比）：
- 读：正文基线（既有）。
- 看：Mermaid 结构图 + Obsidian callout 高亮（本批新增，零 API 依赖）。
- 测：自测三问 `[!question]-` 折叠块，检索练习（本批新增）。
- 用：`- [ ]` checkbox 行动项（本批新增）。
- 讲：费曼式「讲出来」块（本批新增，金字塔顶层）。
外加：结论先行（断言式小标题）作为一切要点的排版基线，全 9 个注册键覆盖。
"""

import re

import pytest

from prompts.templates import (
    CASE_PROMPT,
    CONTENT_SUMMARY_PROMPT,
    DISSECTION_PROMPT,
    INTERVIEW_PROMPT,
    KEY_POINTS_PROMPT,
    NOTE_TEMPLATES,
    OPINION_PROMPT,
    READING_PROMPT,
    ROUNDUP_PROMPT,
    UNIVERSAL_RULES,
)

_ALL_NOTE_TYPES = [
    "structured",
    "key_points",
    "case",
    "opinion",
    "interview",
    "roundup",
    "reading",
    "dissection",
    "general",
]


def _p(note_type: str) -> str:
    return NOTE_TEMPLATES[note_type]["prompt"]


def _fixed_structure_section(prompt: str) -> str:
    start = prompt.index("# 固定结构")
    end = prompt.index("提炼规范", start)
    return prompt[start:end]


class TestMermaidGuard:
    """Mermaid 门禁（2026-09-05 三改）：目的驱动，不设机械门槛。

    图只有一个目的——帮读者更好地学习理解（外化结构、降低理解成本）；
    唯一约束是「不能滥用」。张数不设上限，由内容结构复杂度自然决定。
    """

    @pytest.mark.parametrize("prompt_name", ["reading", "case", "dissection", "opinion"])
    def test_mermaid_with_guard(self, prompt_name):
        p = _p(prompt_name)
        assert "```mermaid" in p
        assert "学习理解" in p, "图的目的必须锚定帮读者学习理解"
        assert "滥用" in p, "唯一约束是防滥用"
        assert "禁止为线性罗列强画图" in p, "最典型滥用形态须点名禁止"
        assert "最多 1 张" not in p, "数量上限已废除，不得回归"
        assert "不设上限" in p, "张数由内容结构复杂度决定"


class TestStructuredDiagram:
    """structured / general（深度技术文主落点）必须有结构图规则：目的驱动 + 防滥用。"""

    @pytest.mark.parametrize("prompt_name", ["structured", "general"])
    def test_diagram_rule(self, prompt_name):
        p = _p(prompt_name)
        assert "十八、结构图" in p
        assert "```mermaid" in p
        assert "学习理解" in p
        assert "滥用" in p
        assert "禁止为线性罗列强画图" in p
        assert "最多 1 张" not in p


class TestCallout:
    """Obsidian 原生 callout：金句 / 风险块视觉增强，零 API 依赖。"""

    @pytest.mark.parametrize("prompt_name", ["key_points", "interview", "reading"])
    def test_quote_callout(self, prompt_name):
        assert "[!quote]" in _p(prompt_name)

    @pytest.mark.parametrize("prompt_name", ["reading", "dissection"])
    def test_warning_callout(self, prompt_name):
        assert "[!warning]" in _p(prompt_name)


class TestSelfQuiz:
    """学习金字塔「测」层：自测三问折叠块（检索练习）。"""

    @pytest.mark.parametrize("prompt_name", ["structured", "reading", "case"])
    def test_self_quiz_folded_callout(self, prompt_name):
        p = _p(prompt_name)
        assert "自测三问" in p
        assert "[!question]-" in p


class TestFeynman:
    """学习金字塔顶层「讲」：费曼式讲出来块。"""

    @pytest.mark.parametrize("prompt_name", ["structured", "reading", "case"])
    def test_feynman_block(self, prompt_name):
        p = _p(prompt_name)
        assert "费曼" in p
        assert "讲给" in p
        assert "类比" in p


class TestCheckboxAction:
    """学习金字塔「用」层：checkbox 行动项。"""

    @pytest.mark.parametrize("prompt_name", ["key_points", "interview", "reading"])
    def test_checkbox_list(self, prompt_name):
        assert "`- [ ]`" in _p(prompt_name)


class TestConclusionFirst:
    """结论先行：小标题必须是断言式结论，全 9 个注册键覆盖（含 general 别名）。"""

    @pytest.mark.parametrize("note_type", _ALL_NOTE_TYPES)
    def test_assertion_style_heading(self, note_type):
        p = _p(note_type)
        assert "结论先行" in p
        assert "断言式" in p


class TestReadingLearningOrder:
    """reading 固定结构语义顺序：思考(8) → 测(9) → 讲(10) → 收束(11)。"""

    def test_quiz_is_item_nine(self):
        section = _fixed_structure_section(READING_PROMPT)
        assert "9. **自测三问" in section

    def test_feynman_is_item_ten(self):
        section = _fixed_structure_section(READING_PROMPT)
        assert "10. **讲出来" in section

    def test_summary_is_item_eleven(self):
        section = _fixed_structure_section(READING_PROMPT)
        assert "11. **总结收束**" in section

    def test_quiz_feynman_have_escape_hatch(self):
        """按需块必须带「不凑数」逃生阀（与去水分红线一致）。"""
        section = _fixed_structure_section(READING_PROMPT)
        lines = section.split("\n")
        quiz_line = [l for l in lines if l.startswith("9. ")][0]
        feynman_line = [l for l in lines if l.startswith("10. ")][0]
        assert "不凑数" in quiz_line
        assert "不凑数" in feynman_line

    def test_numbering_strictly_increasing(self):
        section = _fixed_structure_section(READING_PROMPT)
        numbers = [int(m) for m in re.findall(r"^(\d+)\. ", section, re.MULTILINE)]
        assert numbers == list(range(1, len(numbers) + 1))


class TestCaseLearningOrder:
    """case 固定结构语义顺序：延伸思考(8) → 测(9) → 讲(10)。"""

    def test_quiz_is_item_nine(self):
        section = _fixed_structure_section(CASE_PROMPT)
        assert "9. **自测三问" in section

    def test_feynman_is_item_ten(self):
        section = _fixed_structure_section(CASE_PROMPT)
        assert "10. **讲出来" in section

    def test_numbering_strictly_increasing(self):
        section = _fixed_structure_section(CASE_PROMPT)
        numbers = [int(m) for m in re.findall(r"^(\d+)\. ", section, re.MULTILINE)]
        assert numbers == list(range(1, len(numbers) + 1))


class TestStructuredLearningAppendix:
    """structured 走拼装路径：自测三问 / 费曼以 十六 / 十七 追加，且在思维模型自检之后。"""

    def test_quiz_then_feynman_order(self):
        assert "# 十六、自测三问" in CONTENT_SUMMARY_PROMPT
        assert "# 十七、讲出来" in CONTENT_SUMMARY_PROMPT
        assert CONTENT_SUMMARY_PROMPT.index("# 十六、") < CONTENT_SUMMARY_PROMPT.index("# 十七、")

    def test_quiz_after_selfcheck(self):
        assert CONTENT_SUMMARY_PROMPT.index("# 十五、思维模型自检") < CONTENT_SUMMARY_PROMPT.index("# 十六、自测三问")


class TestNoRegression:
    """上午批次（模板借鉴）与既有契约锚点不回退。"""

    def test_reading_anchors_survive(self):
        p = READING_PROMPT
        for anchor in ["论点间关系", "三重验证", "常见误用", "金句摘录", "只带走三句话", "全书地图", "争议与不同声音"]:
            assert anchor in p

    def test_case_anchors_survive(self):
        p = CASE_PROMPT
        for anchor in ["背景与约束", "关键决策", "结果与数据", "可复用经验"]:
            assert anchor in p

    def test_dissection_anchors_survive(self):
        p = DISSECTION_PROMPT
        for anchor in ["分发门", "待校准", "禁区", "标题公式", "占位符"]:
            assert anchor in p

    def test_opinion_anchors_survive(self):
        p = OPINION_PROMPT
        assert "佐证强度" in p
        assert "单一案例" in p

    def test_reading_ends_with_universal_rules(self):
        assert READING_PROMPT.endswith(UNIVERSAL_RULES[-40:])

    def test_roundup_matrix_survives(self):
        assert "对比矩阵" in ROUNDUP_PROMPT
        assert "问答" in INTERVIEW_PROMPT
