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
   全不适用核验在写作时完成，正文只落一行结论；质量自检闸门口径同步对齐。
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
        assert "反对方如实标注「（笔记者推断）」" in OPINION_PROMPT

    def test_no_fabricated_opposition(self):
        assert "不为结构完整硬造对立面" in OPINION_PROMPT


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

    全不适用：核验在写作时逐条完成，正文只落一行结论——
    读者要的是洞察，不是审计日志。
    """

    def test_verification_happens_at_writing_time(self):
        assert "写作时逐条过完" in THINKING_SELFCHECK

    def test_one_line_conclusion_only(self):
        assert "不为自我证明" in THINKING_SELFCHECK
        assert "6 个模型逐条过，均不适用" in THINKING_SELFCHECK

    def test_old_verbose_wording_removed(self):
        assert "逐条列出 6 个模型" not in THINKING_SELFCHECK
        assert "不逐条列明" not in THINKING_SELFCHECK

    def test_propagates_to_all_templates(self):
        assert "不为自我证明" in CONTENT_SUMMARY_PROMPT
        for name in ("key_points", "opinion", "reading"):
            assert "不为自我证明" in NOTE_TEMPLATES[name]["prompt"]


class TestGateSelfcheckAligned:
    """质量自检闸门 ① 与新「一行结论」口径一致，不互相打架。"""

    def test_selfcheck_option_b_is_one_line(self):
        assert "落一行「均不适用」结论" in QUALITY_GATE_SELFCHECK
        assert "逐条说明不适用" not in QUALITY_GATE_SELFCHECK
