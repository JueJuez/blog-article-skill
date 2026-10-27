# -*- coding: utf-8 -*-
"""学习体验包回归测试（2026-09-05；2026-10-27 S5 更新）。

学习金字塔映射（被动 → 主动，只取定性排序，不取缺科学依据的百分比）：
- 读：正文基线（既有）。
- 看：Mermaid 结构图 + Obsidian callout 高亮（本批新增，零 API 依赖）。
- 用：`- [ ]` checkbox 行动项（本批新增，S5 起仅源内明确建议）。
- 测 / 讲（自测三问、费曼「讲出来」）：**已于 S5 关闭**——属模型自增的附加学习栏目，
  不再出现在生产 prompt；见 PLAN-20261026-low-cost-quality §9.7。
外加：结论先行且保留证据强度作为排版基线，全 9 个注册键覆盖（2026-10-14）。
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
    start = prompt.index("# 模块与组织")
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
        assert "十五、结构图" in p
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


class TestSelfQuizClosed:
    """S5 关闭：自测三问不再出现在任何生产 prompt。"""

    @pytest.mark.parametrize("note_type", _ALL_NOTE_TYPES)
    def test_self_quiz_removed(self, note_type):
        p = _p(note_type)
        assert "自测三问" not in p
        assert "[!question]-" not in p


class TestFeynmanClosed:
    """S5 关闭：费曼「讲出来」块不再出现在任何生产 prompt。"""

    @pytest.mark.parametrize("note_type", _ALL_NOTE_TYPES)
    def test_feynman_removed(self, note_type):
        p = _p(note_type)
        assert "费曼" not in p


class TestCheckboxAction:
    """学习金字塔「用」层：checkbox 行动项。"""

    @pytest.mark.parametrize("prompt_name", ["key_points", "interview", "reading"])
    def test_checkbox_list(self, prompt_name):
        assert "`- [ ]`" in _p(prompt_name)


class TestConclusionFirst:
    """结论先行保留，但标题强度服从源证据，不能把预测写成确定因果。"""

    @pytest.mark.parametrize("note_type", _ALL_NOTE_TYPES)
    def test_heading_preserves_evidence_strength(self, note_type):
        p = _p(note_type)
        assert "结论先行" in p
        assert "证据强度" in p
        assert "断言式" not in p


class TestReadingLearningOrder:
    """reading 模块语义顺序（S5 后共 8 条）：行动启发(7，仅源内) → 收束(8) 仍为末条。"""

    def test_summary_is_item_eight(self):
        section = _fixed_structure_section(READING_PROMPT)
        assert "8. **总结收束**" in section

    def test_numbering_strictly_increasing(self):
        section = _fixed_structure_section(READING_PROMPT)
        numbers = [int(m) for m in re.findall(r"^(\d+)\. ", section, re.MULTILINE)]
        assert numbers == list(range(1, len(numbers) + 1))


class TestCaseLearningOrder:
    """case 模块（S5 后共 7 条）：可复用经验(7，源内依据) 为末条，无延伸/自测/费曼。"""

    def test_reusable_experience_is_last(self):
        section = _fixed_structure_section(CASE_PROMPT)
        assert "7. **可复用经验（源内依据）**" in section

    def test_numbering_strictly_increasing(self):
        section = _fixed_structure_section(CASE_PROMPT)
        numbers = [int(m) for m in re.findall(r"^(\d+)\. ", section, re.MULTILINE)]
        assert numbers == list(range(1, len(numbers) + 1))


class TestStructuredLearningAppendixClosed:
    """structured 拼装路径（S5 后）：自测三问 / 费曼 / 思维模型节全部退出。"""

    def test_quiz_then_feynman_order(self):
        assert "# 十六、自测三问" not in CONTENT_SUMMARY_PROMPT
        assert "# 十七、讲出来" not in CONTENT_SUMMARY_PROMPT

    def test_analysis_boundary_is_section_fourteen(self):
        assert "# 十四、分析边界" in CONTENT_SUMMARY_PROMPT


class TestNoRegression:
    """上午批次（模板借鉴）与既有契约锚点不回退。"""

    def test_reading_anchors_survive(self):
        p = READING_PROMPT
        for anchor in ["论点间关系", "证据评议", "常见误用", "金句摘录", "只带走三句话", "全书地图", "材料内争议与缺口"]:
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
