"""机械质量门禁测试（DECISION-20260905）：verifier 落盘前零 AI 校验。

保护需求（历史小问题复盘）：模型输出偶发缺/多主标题、正文写来源链接
（formatter 权威追加后出现两个链接）、篇幅严重偏离模板区间。这些在
FORCE_AGENT_MODE=1 主路径（save_summary_only）上无任何校验环节——
AI 审核员默认关且无外部 AI 时返回 None，兜不住，必须机械拦截。
"""
import re
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from articles import dedup
from articles import main as articles_main
from prompts import templates as templates_mod
from prompts.verifier import NOTE_WORD_LIMITS, count_note_words, verify_note_mechanical


def _body(n: int) -> str:
    """构造 n 字正文（单字重复，便于精确控制字数）。"""
    return "字" * n


VALID_NOTE = "# 合规标题\n\n" + _body(1000)  # key_points 区间 (800, 1500) 内


class TestH1:
    """主标题必备项：单 H1 通过，缺失或重复拦截。"""

    def test_single_h1_passes(self):
        res = verify_note_mechanical("# 标题\n\n正文若干", note_type="")
        assert res["passed"] is True
        assert res["issues"] == []

    def test_no_h1_fails(self):
        res = verify_note_mechanical("没有主标题的正文", note_type="")
        assert res["passed"] is False
        assert any("主标题" in i for i in res["issues"])

    def test_double_h1_fails(self):
        res = verify_note_mechanical("# 一\n\n正文\n\n# 二", note_type="")
        assert res["passed"] is False
        assert any("主标题" in i for i in res["issues"])


class TestSourceLink:
    """来源链接卫生：正文不写来源链接行，避免 formatter 权威追加后出现两个链接。"""

    def test_no_source_link_passes(self):
        res = verify_note_mechanical("# 标题\n\n纯正文", note_type="")
        assert res["passed"] is True

    def test_source_link_line_fails(self):
        note = "# 标题\n\n**来源链接**：[原文](https://a.com)\n\n正文"
        res = verify_note_mechanical(note, note_type="")
        assert res["passed"] is False
        assert any("来源链接" in i for i in res["issues"])

    def test_source_url_in_body_fails(self):
        note = "# 标题\n\n正文引用了 https://a.com/post/1 当论据"
        res = verify_note_mechanical(note, note_type="", source_url="https://a.com/post/1")
        assert res["passed"] is False
        assert any("来源链接" in i for i in res["issues"])

    def test_empty_source_url_no_false_positive(self):
        res = verify_note_mechanical("# 标题\n\n普通正文", note_type="", source_url="")
        assert res["passed"] is True


class TestWordCount:
    """篇幅区间：硬阈值（<min*0.6 或 >max*1.5）拦截，轻微越界仅 warning。"""

    def test_in_range_passes(self):
        res = verify_note_mechanical("# 标题\n\n" + _body(1000), note_type="key_points")
        assert res["passed"] is True

    def test_severely_low_fails(self):
        # 400 字 < 800*0.6=480 → 硬拦
        res = verify_note_mechanical("# 标题\n\n" + _body(400), note_type="key_points")
        assert res["passed"] is False
        assert any("字数" in i for i in res["issues"])

    def test_severely_high_fails(self):
        # 2500 字 > 1500*1.5=2250 → 硬拦
        res = verify_note_mechanical("# 标题\n\n" + _body(2500), note_type="key_points")
        assert res["passed"] is False
        assert any("字数" in i for i in res["issues"])

    def test_slightly_out_of_range_warns_only(self):
        # 700 字 ∈ (480, 800)：轻微越界不拦，仅提示
        res = verify_note_mechanical("# 标题\n\n" + _body(700), note_type="key_points")
        assert res["passed"] is True
        assert res["warnings"] and any("字数" in w for w in res["warnings"])

    def test_unknown_note_type_skips_word_count(self):
        res = verify_note_mechanical("# 标题\n\n" + _body(3), note_type="mystery")
        assert res["passed"] is True

    def test_empty_note_type_skips_word_count(self):
        res = verify_note_mechanical("# 标题\n\n" + _body(3), note_type="")
        assert res["passed"] is True


class TestWordCountHelper:
    """count_note_words：去空白后计字符数。"""

    def test_counts_non_whitespace_chars(self):
        # 「# 标题\n\n正文  两段」→ 去空白后「#标题正文两段」共 7 字符（含 # 号）
        assert count_note_words("# 标题\n\n正文  两段") == 7

    def test_empty_note(self):
        assert count_note_words("") == 0


class TestWordLimitsConsistency:
    """防漂移：NOTE_WORD_LIMITS 必须与各模板「单篇正文 X～Y 字」声明一致。"""

    @pytest.mark.parametrize("note_type", sorted(NOTE_WORD_LIMITS))
    def test_limits_match_template_text(self, note_type):
        prompt = templates_mod.NOTE_TEMPLATES[note_type]["prompt"]
        m = re.search(r"单篇正文(?:软上限)?\s*(\d+)～(\d+)\s*字", prompt)
        assert m, f"{note_type} 模板缺少「单篇正文 X～Y 字」声明"
        lo, hi = int(m.group(1)), int(m.group(2))
        assert NOTE_WORD_LIMITS[note_type] == (lo, hi)

    def test_covers_all_templates(self):
        assert set(NOTE_WORD_LIMITS) == set(templates_mod.NOTE_TEMPLATES)


class TestSelfcheckAndGatePrompt:
    """建议 5：AI 审核员链路的篇幅自检维度（SELFCHECK 第③条 + GATE_PROMPT 评分项）。"""

    def test_selfcheck_declares_three_items(self):
        assert "三条专项自检" in templates_mod.QUALITY_GATE_SELFCHECK

    def test_selfcheck_has_word_count_item(self):
        sc = templates_mod.QUALITY_GATE_SELFCHECK
        assert "③" in sc and "篇幅" in sc

    def test_gate_prompt_has_word_dimension(self):
        assert "篇幅" in templates_mod.QUALITY_GATE_PROMPT


class TestSaveSummaryOnlyGate:
    """门禁接入 save_summary_only：拦截不落盘、不 dedup，子 Agent 可按 issues 修复重试。"""

    @pytest.fixture(autouse=True)
    def _stub_save_and_dedup(self, monkeypatch, tmp_path):
        self.save_calls = []
        self.mark_calls = []
        self._real_mark = dedup.mark_summarized  # 保留真实实现，供预置索引用例写入 tmp 索引
        monkeypatch.setattr(articles_main, "save_summarized_article",
                            lambda *a, **k: (self.save_calls.append(1), ("fmt", "f.md"))[1])
        monkeypatch.setattr(dedup, "mark_summarized",
                            lambda *a, **k: self.mark_calls.append(1))
        monkeypatch.setattr(dedup, "_CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dedup, "_INDEX_FILE", str(tmp_path / "dedup.json"))

    def test_violation_blocked(self):
        res = articles_main.save_summary_only({
            "summarized_content": "总结", "original_url": "https://g.com/1",
            "note_type": "key_points"})
        assert res.get("success") is False
        assert res.get("message", "").startswith("VERIFIER_FAILED")
        assert self.save_calls == []
        assert self.mark_calls == []

    def test_compliant_passes(self):
        res = articles_main.save_summary_only({
            "summarized_content": VALID_NOTE, "original_url": "https://g.com/2",
            "note_type": "key_points"})
        assert res.get("success") is True
        assert self.save_calls == [1]

    def test_dedup_precedes_gate(self):
        # 已总结 URL：dedup 闸门优先于质量门禁，保持 ALREADY_EXISTS 按成功出队语义
        self._real_mark(url="https://g.com/3", filename="old.md")
        res = articles_main.save_summary_only({
            "summarized_content": "总结", "original_url": "https://g.com/3",
            "note_type": "key_points"})
        assert res.get("success") is True
        assert res.get("skipped") is True
        assert "ALREADY_EXISTS" in res.get("message", "")
        assert self.save_calls == []

    def test_force_does_not_bypass_quality_gate(self):
        # force 只豁免 dedup（强制重写逃生舱），不豁免机械质量底线
        self._real_mark(url="https://g.com/4", filename="old.md")
        res = articles_main.save_summary_only({
            "summarized_content": "总结", "original_url": "https://g.com/4",
            "note_type": "key_points", "force": True})
        assert res.get("success") is False
        assert res.get("message", "").startswith("VERIFIER_FAILED")
        assert self.save_calls == []

    def test_gate_blocks_even_without_folder(self):
        # 队列条目常不带 folder：门禁在自动路由之前拦截，不触发路由副作用
        res = articles_main.save_summary_only({
            "summarized_content": "总结", "original_url": "https://g.com/5",
            "note_type": "key_points"})
        assert res.get("success") is False
        assert res.get("message", "").startswith("VERIFIER_FAILED")
        assert self.save_calls == []
