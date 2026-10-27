# -*- coding: utf-8 -*-
"""门禁目的审计回归测试（2026-09-05 · 第二批）。

审计基准（承 Mermaid 三改「目的驱动」）：每类总结有自己的目的（北极星），
硬数字只能作典型区间提示，必须带「以真实数量为准 / 无则省略」逃生口；
数量是结果不是规定。本批修正 5 处一刀切条款：
1. opinion 论据拆解（目的：弄清作者主张什么、凭什么）→ 条数由真实论据决定。
2. opinion 正反双方（目的：忠于原意 + 让读者自判）→ 单面立场文不硬造对立面。
3. roundup 选购建议（目的：帮读者做选择）→ 以原文真实建议为准。
4. reading 主题块（目的：学作者核心思想）→ 块数由主线复杂度决定。
5. 思维模型自检（目的：确认深挖真发生，而非向读者展示审计过程）→
   已于 S5（2026-10-27）连同透镜整体关闭，分析边界接管；质量自检口径同步对齐。
"""

import pytest  # noqa: F401  # 与同目录测试文件保持一致的测试栈声明

from prompts.templates import (
    CONTENT_SUMMARY_PROMPT,
    NOTE_TEMPLATES,
    OPINION_PROMPT,
    QUALITY_GATE_SELFCHECK,
    READING_PROMPT,
    ROUNDUP_PROMPT,
    UNIVERSAL_RULES,
)


class TestOpinionArgumentEscape:
    """目的：弄清作者主张什么、凭什么——论据条数忠于原文，不为凑数拆分或编造。"""

    def test_count_follows_real_arguments(self):
        assert "条数由作者真实论据数量决定" in OPINION_PROMPT
        assert "只有 1 条就写 1 条" in OPINION_PROMPT

    def test_usage_note_has_anti_fabrication(self):
        assert "立场、论据、反驳都从原文真实内容中来" in OPINION_PROMPT


class TestOpinionSingleSidedEscape:
    """目的：忠于原意 + 让读者自判——单面立场文不硬造对立面。"""

    def test_inferred_opposition_is_closed(self):
        """S5 契约：额外反驳不再有「笔记者分析」逃生门，直接不写。"""
        assert "额外反驳可另放笔记者分析区并逐条标推断" not in OPINION_PROMPT
        assert "不另造反方" in OPINION_PROMPT

    def test_no_fabricated_opposition(self):
        assert "只有一面就保留原面" in OPINION_PROMPT


class TestRoundupSuggestionEscape:
    """目的：帮读者做选择——建议条数以原文真实建议为准，无则省略。"""

    def test_count_follows_source(self):
        assert "条数以原文真实建议为准" in ROUNDUP_PROMPT
        assert "不为凑数硬拆硬并" in ROUNDUP_PROMPT

    def test_omittable_when_absent(self):
        assert "5. **选购 / 使用建议**（若原文有）" in ROUNDUP_PROMPT


class TestReadingThemeBlockEscape:
    """目的：学作者核心思想——主题块数由主线复杂度决定，不硬拆不硬并。"""

    def test_count_follows_structure(self):
        assert "块数由主线复杂度自然决定" in READING_PROMPT
        assert "不为凑块拆分" in READING_PROMPT


class TestAnalysisBoundaryClosed:
    """S5 契约（2026-10-27）：思维模型自检连同透镜整体关闭，分析边界接管。

    延伸不再「按需写」，而是默认不写；源内已有的分析照常保留并归属作者。
    """

    def test_thinking_selfcheck_removed(self):
        assert "思维模型自检" not in CONTENT_SUMMARY_PROMPT

    def test_analysis_boundary_propagates_to_all_templates(self):
        for name in ("key_points", "opinion", "reading"):
            assert "不做延伸分析" in NOTE_TEMPLATES[name]["prompt"]
        assert "不做延伸分析" in UNIVERSAL_RULES
        assert "不做延伸分析" in CONTENT_SUMMARY_PROMPT


class TestGateSelfcheckAligned:
    """自检核查无源延伸，不要求把检查日志当作内容。"""

    def test_selfcheck_does_not_force_model_log(self):
        assert "不新增无源延伸" in QUALITY_GATE_SELFCHECK
        assert "逐条说明不适用" not in QUALITY_GATE_SELFCHECK
