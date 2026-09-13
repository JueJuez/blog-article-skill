"""笔记机械质量门禁测试（DECISION-20260905 / DECISION-20260907）：verifier 落盘前零 AI 校验。

保护需求（历史小问题复盘）：模型输出偶发正文写来源链接（formatter 权威追加后
出现两个链接）、篇幅严重偏离模板区间；2026-09-07 起去 H1（标题由文件名/飞书
节点标题承担），正文出现 # 一级标题改为拦截，代码围栏内 # 注释不误伤。这些在
FORCE_AGENT_MODE=1 主路径（save_summary_only）上无任何校验环节——AI 审核员
默认关且无外部 AI 时返回 None，兜不住，必须机械拦截。
"""
import json
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
from shared import gate_blockers as gate_blockers_mod


def _body(n: int) -> str:
    """构造 n 字正文（单字重复，便于精确控制字数）。"""
    return "字" * n


VALID_NOTE = _body(1000)  # key_points 区间 (800, 1500) 内（去 H1：正文不带 # 标题）


class TestNoH1:
    """去 H1（DECISION-20260907）：正文不含一级标题通过，出现即拦截；围栏内 # 注释不算。"""

    def test_no_h1_passes(self):
        res = verify_note_mechanical("没有主标题的正文", note_type="")
        assert res["passed"] is True
        assert res["issues"] == []

    def test_h1_fails(self):
        res = verify_note_mechanical("# 标题\n\n正文若干", note_type="")
        assert res["passed"] is False
        assert any("一级标题" in i for i in res["issues"])

    def test_double_h1_fails(self):
        res = verify_note_mechanical("# 一\n\n正文\n\n# 二", note_type="")
        assert res["passed"] is False
        assert any("一级标题" in i for i in res["issues"])

    def test_code_fence_hash_lines_ignored(self):
        # 去围栏后扫描：代码块内 # 注释行不得计为一级标题（旧逻辑双 H1 误伤）
        content = _body(1000) + "\n\n```bash\n# 安装依赖\npip install x\n# 验证\npip check\n```\n"
        res = verify_note_mechanical(content, note_type="")
        assert res["passed"] is True
        assert res["issues"] == []


class TestExtractTitleFromSummary:
    """去 H1 后标题提取（DECISION-20260907）：不再扫作者行上方（无 H1 可取），仅认「核心定位：」兜底。"""

    def test_no_core_line_returns_empty(self):
        # 作者行上方无标题时返回空串（original_title 缺省由文件名承担）
        assert articles_main._extract_title_from_summary("**作者**：张三\n\n正文若干") == ""

    def test_core_line_extracted(self):
        md = "**作者**：张三\n\n**核心定位**：这篇讲复利的底层逻辑\n\n正文"
        assert articles_main._extract_title_from_summary(md) == "这篇讲复利的底层逻辑"

    def test_legacy_h1_header_not_extracted(self):
        # 旧格式（H1+标签行+作者行）不再取 H1 当标题：去 H1 后标题由文件名承担
        md = "# 旧格式标题\n\n#标签1 #标签2\n\n**作者**：张三\n\n正文"
        assert articles_main._extract_title_from_summary(md) == ""

    def test_truncated_to_50_chars(self):
        md = "**核心定位**：" + "长" * 80
        assert articles_main._extract_title_from_summary(md) == "长" * 50


class TestSourceLink:
    """来源链接卫生：正文不写来源链接行，避免 formatter 权威追加后出现两个链接。"""

    def test_no_source_link_passes(self):
        res = verify_note_mechanical("纯正文", note_type="")
        assert res["passed"] is True

    def test_source_link_line_fails(self):
        note = "**来源链接**：[原文](https://a.com)\n\n正文"
        res = verify_note_mechanical(note, note_type="")
        assert res["passed"] is False
        assert any("来源链接" in i for i in res["issues"])

    def test_source_url_in_body_fails(self):
        note = "正文引用了 https://a.com/post/1 当论据"
        res = verify_note_mechanical(note, note_type="", source_url="https://a.com/post/1")
        assert res["passed"] is False
        assert any("来源链接" in i for i in res["issues"])

    def test_empty_source_url_no_false_positive(self):
        res = verify_note_mechanical("普通正文", note_type="", source_url="")
        assert res["passed"] is True


class TestWordCount:
    """篇幅区间：硬阈值（<min*0.6 或 >max*1.5）拦截，轻微越界仅 warning。"""

    def test_in_range_passes(self):
        res = verify_note_mechanical(_body(1000), note_type="key_points")
        assert res["passed"] is True

    def test_severely_low_fails(self):
        # 400 字 < 800*0.6=480 → 硬拦
        res = verify_note_mechanical(_body(400), note_type="key_points")
        assert res["passed"] is False
        assert any("字数" in i for i in res["issues"])

    def test_severely_high_fails(self):
        # 2500 字 > 1500*1.5=2250 → 硬拦
        res = verify_note_mechanical(_body(2500), note_type="key_points")
        assert res["passed"] is False
        assert any("字数" in i for i in res["issues"])

    def test_slightly_out_of_range_warns_only(self):
        # 700 字 ∈ (480, 800)：轻微越界不拦，仅提示
        res = verify_note_mechanical(_body(700), note_type="key_points")
        assert res["passed"] is True
        assert res["warnings"] and any("字数" in w for w in res["warnings"])

    def test_unknown_note_type_skips_word_count(self):
        res = verify_note_mechanical(_body(3), note_type="mystery")
        assert res["passed"] is True

    def test_empty_note_type_skips_word_count(self):
        res = verify_note_mechanical(_body(3), note_type="")
        assert res["passed"] is True


class TestWordCountHelper:
    """count_note_words：去空白后计字符数。"""

    def test_counts_non_whitespace_chars(self):
        # 「# 标题\n\n正文  两段」→ 去空白后「#标题正文两段」共 7 字符（含 # 号）
        assert count_note_words("# 标题\n\n正文  两段") == 7

    def test_empty_note(self):
        assert count_note_words("") == 0


class TestWordLimitsConsistency:
    """防漂移：NOTE_WORD_LIMITS 必须与各模板字数声明一致。

    - structured / general 走 source-aware 参考值（源长×比例带），模板不声明固定区间，
      改为声明「参考值（源长×…）」；其余轻模板仍声明固定区间且须与 NOTE_WORD_LIMITS 一致。
    """

    # 走 source-aware 参考值的类型（无固定区间声明）
    SOURCE_AWARE_TYPES = {"structured", "general"}

    @pytest.mark.parametrize("note_type", sorted(NOTE_WORD_LIMITS))
    def test_limits_match_template_text(self, note_type):
        prompt = templates_mod.NOTE_TEMPLATES[note_type]["prompt"]
        if note_type in self.SOURCE_AWARE_TYPES:
            # 源长感知型：模板须声明参考值（源长×比例带），不得出现固定「单篇正文 X～Y 字」硬区间
            assert "参考值" in prompt and "源长" in prompt, \
                f"{note_type} 应声明 source-aware 参考值（含『参考值』『源长』）"
            assert not re.search(r"单篇正文\s*\d+～\d+\s*字", prompt), \
                f"{note_type} 不应声明固定字数硬区间（已改为参考值）"
        else:
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


class TestCountBlocks:
    """count_blocks：跨滚动台账文件统计同 URL 历史拦截次数（重试放行的判定依据）。"""

    def _write_ledger(self, tmp_path, name, records):
        (tmp_path / name).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
            encoding="utf-8")

    def test_counts_across_rolling_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gate_blockers_mod, "GATE_BLOCKERS_BASE",
                            str(tmp_path / "gate_blockers.jsonl"))
        self._write_ledger(tmp_path, "gate_blockers.20260910.jsonl",
                           [{"url": "https://a.com"}, {"url": "https://b.com"}])
        self._write_ledger(tmp_path, "gate_blockers.20260911.jsonl",
                           [{"url": "https://a.com"}])
        assert gate_blockers_mod.count_blocks("https://a.com") == 2

    def test_no_ledger_returns_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gate_blockers_mod, "GATE_BLOCKERS_BASE",
                            str(tmp_path / "gate_blockers.jsonl"))
        assert gate_blockers_mod.count_blocks("https://a.com") == 0

    def test_bad_lines_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gate_blockers_mod, "GATE_BLOCKERS_BASE",
                            str(tmp_path / "gate_blockers.jsonl"))
        (tmp_path / "gate_blockers.20260910.jsonl").write_text(
            "not-json\n" + json.dumps({"url": "https://a.com"}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        assert gate_blockers_mod.count_blocks("https://a.com") == 1

    def test_empty_url_returns_zero(self, tmp_path, monkeypatch):
        # 无 URL 输入直接返 0：防止无主内容借空串匹配台账记录绕过门禁
        monkeypatch.setattr(gate_blockers_mod, "GATE_BLOCKERS_BASE",
                            str(tmp_path / "gate_blockers.jsonl"))
        self._write_ledger(tmp_path, "gate_blockers.20260910.jsonl", [{"url": ""}])
        assert gate_blockers_mod.count_blocks("") == 0


class TestRetryBypassGate:
    """重试放行语义（2026-09-10）：同 URL 首次字数违规拦截让模型重改；再次重交
    仍纯字数违规 → 放行落盘（重改仍越界说明压缩已到头：干货密度高或原文本身
    撑不起模板区间）。H1/来源链接等确定性可修复问题永不放行；
    放行记台账 action=bypassed_retry 保持审计闭环。"""

    @pytest.fixture(autouse=True)
    def _stub_env(self, monkeypatch, tmp_path):
        self.save_calls = []
        self.log_calls = []
        monkeypatch.setattr(articles_main, "save_summarized_article",
                            lambda *a, **k: (self.save_calls.append(1), ("fmt", "f.md"))[1])
        monkeypatch.setattr(dedup, "mark_summarized", lambda *a, **k: None)
        monkeypatch.setattr(dedup, "_CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dedup, "_INDEX_FILE", str(tmp_path / "dedup.json"))
        # 台账指向 tmp：count_blocks / log_gate_block 走真实实现，行为随台账文件变化
        monkeypatch.setattr(gate_blockers_mod, "GATE_BLOCKERS_BASE",
                            str(tmp_path / "gate_blockers.jsonl"))
        real_log = gate_blockers_mod.log_gate_block

        def _spy(*a, **k):
            self.log_calls.append(k.get("action", "blocked"))
            return real_log(*a, **k)

        monkeypatch.setattr(gate_blockers_mod, "log_gate_block", _spy)

    def _seed_block(self, url):
        gate_blockers_mod.log_gate_block(source="queue", note_type="key_points",
                                         url=url, title="旧标题", issues=["字数 400 低于硬下限"])

    def test_first_word_violation_blocked(self):
        # 首次（台账无记录）：字数硬拦 → 返回 issues 让模型重改，不落盘
        res = articles_main.save_summary_only({
            "summarized_content": _body(400), "original_url": "https://g.com/r1",
            "note_type": "key_points"})
        assert res.get("success") is False
        assert res.get("message", "").startswith("VERIFIER_FAILED")
        assert res.get("issues")
        assert self.save_calls == []
        assert self.log_calls == ["blocked"]

    def test_second_word_violation_bypassed(self, tmp_path):
        # 首次拦截已留台账 → 再次重交仍纯字数违规 → 放行落盘
        self._seed_block("https://g.com/r2")
        res = articles_main.save_summary_only({
            "summarized_content": _body(400), "original_url": "https://g.com/r2",
            "note_type": "key_points"})
        assert res.get("success") is True
        assert self.save_calls == [1]
        assert self.log_calls == ["blocked", "bypassed_retry"]
        # 台账文件中 action=bypassed_retry 可追溯
        lines = []
        for f in tmp_path.glob("gate_blockers*.jsonl"):
            lines += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
        assert any(r.get("action") == "bypassed_retry" for r in lines)

    def test_h1_never_bypassed(self):
        # H1 是确定性可修复问题：即使该 URL 已被拦 3 次，仍拦截不落盘
        for _ in range(3):
            gate_blockers_mod.log_gate_block(source="queue", note_type="",
                                             url="https://g.com/r3", title="t", issues=["一级标题"])
        res = articles_main.save_summary_only({
            "summarized_content": "# 标题\n\n正文若干", "original_url": "https://g.com/r3",
            "note_type": ""})
        assert res.get("success") is False
        assert res.get("message", "").startswith("VERIFIER_FAILED")
        assert self.save_calls == []
        assert self.log_calls[-1] == "blocked"

    def test_mixed_issues_never_bypassed(self):
        # 混合 issues（H1 + 字数）：存在非字数 issue → 不放行
        self._seed_block("https://g.com/r4")
        res = articles_main.save_summary_only({
            "summarized_content": "# 标题\n\n" + _body(400),
            "original_url": "https://g.com/r4", "note_type": "key_points"})
        assert res.get("success") is False
        assert self.save_calls == []
        assert self.log_calls[-1] == "blocked"

    def test_no_url_never_bypassed(self):
        # 无 URL 无法定位台账 → 字数违规照拦（防无主内容绕过门禁）
        res = articles_main.save_summary_only({
            "summarized_content": _body(400), "original_url": "",
            "note_type": "key_points"})
        assert res.get("success") is False
        assert self.save_calls == []


class TestSourceAwareReference:
    """source-aware 参考值（2026-09-13）：有源长时按 源长×比例带 作参考，仅极端畸高/极低硬拦，
    越出参考带（非畸高）转为 review_flags 供父 Agent 抽检，不拦落盘。"""

    SRC = 10000  # 源长 1 万字符
    # ref_lo=2500, ref_hi=4500, ceiling=max(8000,7000)=8000, floor=400

    def _v(self, count, src=SRC):
        return verify_note_mechanical(_body(count), note_type="structured",
                                      source_url="https://g.com/x", source_chars=src)

    def test_in_reference_band_passes_clean(self):
        r = self._v(3500)
        assert r["passed"] is True
        assert r["issues"] == []
        assert r["review_flags"] == []

    def test_over_reference_flags_review_not_block(self):
        # 5000 > ref_hi(4500) 但 < ceiling(8000) → 不拦，置 review_flags（超参考值）
        r = self._v(5000)
        assert r["passed"] is True
        assert any("超参考值" in f for f in r["review_flags"])

    def test_under_reference_but_not_short_ok(self):
        # 2000 < ref_lo(2500) 但 >= 阈值1500 → 水货源压更短也放行，无 flag
        r = self._v(2000)
        assert r["passed"] is True
        assert r["review_flags"] == []

    def test_under_reference_and_short_flags_review(self):
        # 1000 < ref_lo(2500) 且 < 1500 → 偏短抽检信号
        r = self._v(1000)
        assert r["passed"] is True
        assert any("偏短" in f for f in r["review_flags"])

    def test_absurdly_high_blocked(self):
        # 8500 > ceiling(8000) → 畸高照搬，硬拦
        r = self._v(8500)
        assert r["passed"] is False
        assert any("畸高" in i for i in r["issues"])

    def test_extremely_low_blocked(self):
        # 300 < floor(400) → 内容缺失，硬拦
        r = self._v(300)
        assert r["passed"] is False
        assert any("内容缺失" in i for i in r["issues"])

    def test_review_flags_propagate_to_save_result(self, tmp_path, monkeypatch):
        # 越参考值落盘后，save_summary_only 结果须带回 review_flags
        monkeypatch.setattr(articles_main, "save_summarized_article",
                            lambda *a, **k: ("fmt", "f.md"))
        monkeypatch.setattr(dedup, "mark_summarized", lambda *a, **k: None)
        monkeypatch.setattr(dedup, "_CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dedup, "_INDEX_FILE", str(tmp_path / "dedup.json"))
        res = articles_main.save_summary_only({
            "summarized_content": _body(5000), "original_url": "https://g.com/sa",
            "note_type": "structured", "source_chars": self.SRC})
        assert res.get("success") is True
        assert any("超参考值" in f for f in res.get("review_flags", []))
