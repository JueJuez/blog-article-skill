# -*- coding: utf-8 -*-
"""数值保真门禁（2026-10-29 用户决策）的回归用例。

判据是**二值、不可调参**的：
1. 稿里带单位的数字串，必须在源里逐字存在（千分位 / 全角百分号 / 空格差异视为同一串）；
2. 取整或换算出来的数字，同一行必须保留源原串（否则读者无法核对）；
3. 没有源可接地（例如纯汇编稿）→ 不检查，不伪造结论。

当前默认 **shadow**：只记日志、永不拦落盘；两周后再切 `block`（用户拍板）。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from prompts import numeric_fidelity as NF


class TestUnitNumbers:
    def test_comma_and_space_normalized(self):
        assert NF.unit_numbers("营收 4,540 万元") == {"4540万元"}

    def test_percent_and_fullwidth_percent(self):
        assert NF.unit_numbers("增长 17.3％，毛利率 31.2%") == {"17.3%", "31.2%"}

    def test_bare_number_without_unit_ignored(self):
        assert NF.unit_numbers("第3章（共54页，编号2026）") == set()

    def test_empty_text(self):
        assert NF.unit_numbers("") == set()


class TestCheck:
    def test_all_numbers_grounded(self):
        rep = NF.check_numeric_fidelity(
            "营收4540万元，同比增长17.3%。",
            "营收 4,540 万元，同比增长 17.3%")
        assert rep["passed"] is True
        assert rep["issues"] == []
        assert rep["checked"] == 2

    def test_fabricated_number_reported(self):
        rep = NF.check_numeric_fidelity("营收4530万元。", "营收 4,540 万元")
        assert rep["passed"] is False
        assert "4530万元" in rep["issues"][0]
        assert "第1行" in rep["issues"][0]

    def test_derived_value_with_source_token_same_line_passes(self):
        rep = NF.check_numeric_fidelity(
            "营收4540万元（约0.45亿元）。", "营收4540万元")
        assert rep["passed"] is True
        assert rep["derived_with_source"] == 1

    def test_derived_value_without_source_token_fails(self):
        rep = NF.check_numeric_fidelity("营收约0.45亿元。", "营收4540万元")
        assert rep["passed"] is False
        # 单位清单不含「亿元」，归一后的串是「0.45亿」（与源「4540万元」不同串 → 未接地）
        assert "0.45亿" in rep["issues"][0]

    def test_heading_numbers_are_checked(self):
        """标题行也承载结论，不能靠「不是正文」放过编造读数。"""
        rep = NF.check_numeric_fidelity("## 营收4510万元\n\n正文。", "营收4540万元")
        assert rep["passed"] is False

    def test_metadata_line_excluded(self):
        rep = NF.check_numeric_fidelity(
            "> 作者：张三 2026\n\n营收4540万元。", "营收4540万元")
        assert rep["passed"] is True

    def test_url_digits_ignored(self):
        rep = NF.check_numeric_fidelity(
            "详见 https://example.com/2026/1234 。\n\n营收4540万元。", "营收4540万元")
        assert rep["passed"] is True

    def test_code_fence_ignored(self):
        rep = NF.check_numeric_fidelity(
            "```\npip install v1.2.3 元\n```\n\n营收4540万元。", "营收4540万元")
        assert rep["passed"] is True

    def test_empty_note_passes(self):
        rep = NF.check_numeric_fidelity("", "营收4540万元")
        assert rep["passed"] is True and rep["checked"] == 0


class TestModeAndShadowLog:
    def test_default_mode_is_shadow(self, monkeypatch):
        monkeypatch.delenv("NUMERIC_FIDELITY_GATE", raising=False)
        assert NF.numeric_fidelity_mode() == "shadow"

    def test_unknown_mode_falls_back_to_shadow(self, monkeypatch):
        """未知取值不得悄悄变成硬拦：回落 shadow，绝不拦盘。"""
        monkeypatch.setenv("NUMERIC_FIDELITY_GATE", "enforce")
        assert NF.numeric_fidelity_mode() == "shadow"

    def test_shadow_records_but_does_not_block(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NUMERIC_FIDELITY_GATE", "shadow")
        monkeypatch.setenv("NUMERIC_FIDELITY_LOG", str(tmp_path / "nf.jsonl"))
        rep = NF.run_numeric_fidelity("营收4530万元。", "营收4540万元",
                                      url="https://e.com/1", title="测试")
        assert rep["mode"] == "shadow"
        assert rep["passed"] is False          # 结论仍如实报告
        assert rep["shadow"] is True           # 但调用方不得据此拦盘
        rec = json.loads((tmp_path / "nf.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        assert rec["passed"] is False
        assert rec["url"] == "https://e.com/1"
        assert rec["issues"]

    def test_block_mode_does_not_mark_shadow(self, monkeypatch):
        monkeypatch.setenv("NUMERIC_FIDELITY_GATE", "block")
        monkeypatch.setenv("NUMERIC_FIDELITY_LOG", "")
        rep = NF.run_numeric_fidelity("营收4530万元。", "营收4540万元")
        assert rep["mode"] == "block" and rep["shadow"] is False

    def test_off_mode_skips(self, monkeypatch):
        monkeypatch.setenv("NUMERIC_FIDELITY_GATE", "off")
        rep = NF.run_numeric_fidelity("营收4530万元。", "营收4540万元")
        assert rep["mode"] == "off" and rep["passed"] is True and rep["checked"] == 0

    def test_empty_source_is_not_checked(self, monkeypatch):
        monkeypatch.setenv("NUMERIC_FIDELITY_GATE", "shadow")
        rep = NF.run_numeric_fidelity("营收4530万元。", "")
        assert rep["checked"] == 0 and rep["passed"] is True

    def test_log_failure_does_not_raise(self, tmp_path, monkeypatch):
        log = tmp_path / "as_dir.jsonl"
        log.mkdir()
        monkeypatch.setenv("NUMERIC_FIDELITY_LOG", str(log))
        rep = NF.run_numeric_fidelity("营收4530万元。", "营收4540万元")
        assert rep["logged"] is False


class TestApplyToGate:
    """门禁合并器：shadow/off 只留痕不改门禁，block 才把问题并进 issues。

    视频两条落盘路径（`videos._summarize_and_save` / `videos._save_series_note`）
    与 `save_summary_only` 共用这一处合并逻辑，避免三份各写一遍。
    """

    GATE = {"passed": True, "issues": [], "content_blockers": [], "warnings": [],
            "review_flags": [], "compression_warnings": [],
            "evidence_report": {"findings": [], "semantic_status": "not_reviewed"}}

    def test_shadow_keeps_gate_untouched(self, monkeypatch):
        monkeypatch.setenv("NUMERIC_FIDELITY_GATE", "shadow")
        gate = NF.apply_to_gate(dict(self.GATE), "营收4530万元。", "营收4540万元")
        assert gate["passed"] is True
        assert gate["issues"] == []

    def test_off_mode_keeps_gate_untouched(self, monkeypatch):
        monkeypatch.setenv("NUMERIC_FIDELITY_GATE", "off")
        gate = NF.apply_to_gate(dict(self.GATE), "营收4530万元。", "营收4540万元")
        assert gate["passed"] is True and gate["issues"] == []

    def test_block_mode_merges_numeric_issues(self, monkeypatch):
        monkeypatch.setenv("NUMERIC_FIDELITY_GATE", "block")
        gate = NF.apply_to_gate(dict(self.GATE), "营收4530万元。", "营收4540万元")
        assert gate["passed"] is False
        assert any("数值保真" in issue for issue in gate["issues"])

    def test_block_mode_keeps_existing_issues(self, monkeypatch):
        monkeypatch.setenv("NUMERIC_FIDELITY_GATE", "block")
        base = dict(self.GATE, passed=False, issues=["H1 缺失"])
        gate = NF.apply_to_gate(base, "营收4530万元。", "营收4540万元")
        assert gate["issues"][0] == "H1 缺失"
        assert any("数值保真" in issue for issue in gate["issues"])

    def test_block_mode_grounded_number_passes(self, monkeypatch):
        monkeypatch.setenv("NUMERIC_FIDELITY_GATE", "block")
        gate = NF.apply_to_gate(dict(self.GATE), "营收4540万元。", "营收4540万元")
        assert gate["passed"] is True and gate["issues"] == []


class TestSaveGateWiring:
    """落盘入口接线：shadow 不拦、block 才拦（用户 2026-10-29 决策的上线节奏）。"""

    @pytest.fixture(autouse=True)
    def _stub_save_and_dedup(self, monkeypatch, tmp_path):
        from articles import dedup
        from articles import main as articles_main

        self.saved = []
        monkeypatch.setattr(articles_main, "save_summarized_article",
                            lambda *a, **k: (self.saved.append(1), ("fmt", "f.md"))[1])
        monkeypatch.setattr(dedup, "mark_summarized", lambda *a, **k: None)
        monkeypatch.setattr(dedup, "_INDEX_FILE", str(tmp_path / "dedup.json"))
        monkeypatch.setattr(dedup, "_CACHE_DIR", str(tmp_path))

    @staticmethod
    def _note(number_text: str) -> str:
        return f"{number_text}\n\n" + "字" * 1000

    def _save(self, note_text: str, url: str) -> dict:
        from articles import main as articles_main

        return articles_main.save_summary_only({
            "summarized_content": self._note(note_text), "original_url": url,
            "note_type": "key_points", "source_text": "营收4540万元。"})

    def test_shadow_mode_records_but_saves(self, monkeypatch):
        monkeypatch.setenv("NUMERIC_FIDELITY_GATE", "shadow")
        res = self._save("营收4530万元。", "https://g.com/nf1")
        assert res.get("success") is True
        assert self.saved == [1]
        rec = json.loads(open(NF.LOG_FILE, encoding="utf-8").readlines()[-1])
        assert rec["passed"] is False
        assert "4530万元" in rec["issues"][0]

    def test_block_mode_stops_save(self, monkeypatch):
        monkeypatch.setenv("NUMERIC_FIDELITY_GATE", "block")
        res = self._save("营收4530万元。", "https://g.com/nf2")
        assert res.get("success") is False
        assert res["message"].startswith("VERIFIER_FAILED")
        assert "数值保真" in res["message"]
        assert self.saved == []

    def test_block_mode_allows_grounded_number(self, monkeypatch):
        monkeypatch.setenv("NUMERIC_FIDELITY_GATE", "block")
        res = self._save("营收4540万元。", "https://g.com/nf3")
        assert res.get("success") is True
        assert self.saved == [1]
