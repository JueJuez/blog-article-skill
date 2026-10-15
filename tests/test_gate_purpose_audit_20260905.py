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
   核验在写作时完成，无可靠洞察可省略分析，不输出逐模型日志；质量自检口径同步对齐（2026-10-14）。
"""

import pytest  # noqa: F401  # 与同目录测试文件保持一致的测试栈声明

from prompts.templates import (
    CONTENT_SUMMARY_PROMPT,
    NOTE_TEMPLATES,
    OPINION_PROMPT,
    QUALITY_GATE_SELFCHECK,
    READING_PROMPT,
    ROUNDUP_PROMPT,
    THINKING_SELFCHECK,
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

    def test_inferred_opposition_is_labeled(self):
        assert "额外反驳可另放笔记者分析区并逐条标推断" in OPINION_PROMPT
        assert "（笔记者推断）" in OPINION_PROMPT

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


class TestThinkingSelfcheckReaderCost:
    """目的：确认「按需深挖」真发生，而非向读者展示审计过程。

    检查在写作时完成，可靠洞察按需写，不能靠强制日志诱导凑数。
    """

    def test_verification_happens_at_writing_time(self):
        assert "写作时检查" in THINKING_SELFCHECK

    def test_no_insight_can_omit_entire_analysis(self):
        assert "无可靠洞察时整块省略" in THINKING_SELFCHECK
        assert "不强写" in THINKING_SELFCHECK
        assert "就近推断标签" in THINKING_SELFCHECK

    def test_old_verbose_wording_removed(self):
        assert "逐条列出 6 个模型" not in THINKING_SELFCHECK
        assert "不逐条列明" not in THINKING_SELFCHECK

    def test_propagates_to_all_templates(self):
        assert THINKING_SELFCHECK in CONTENT_SUMMARY_PROMPT
        for name in ("key_points", "opinion", "reading"):
            assert THINKING_SELFCHECK in NOTE_TEMPLATES[name]["prompt"]


class TestGateSelfcheckAligned:
    """自检核查分析标签，不要求把检查日志当作内容。"""

    def test_selfcheck_does_not_force_model_log(self):
        assert "不写无效模型日志" in QUALITY_GATE_SELFCHECK
        assert "逐条说明不适用" not in QUALITY_GATE_SELFCHECK
