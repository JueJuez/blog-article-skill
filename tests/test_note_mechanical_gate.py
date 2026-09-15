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


VALID_NOTE = _body(1000)  # 篇幅在「内容缺失下限 300」之上（去 H1：正文不带 # 标题）


class TestNoH1:
    """去 H1（DECISION-20260907）：正文不含一级标题通过，出现即拦截；围栏内 # 注释不算。"""

    def test_no_h1_passes(self):
        res = verify_note_mechanical(VALID_NOTE, note_type="")
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
        res = verify_note_mechanical(VALID_NOTE, note_type="")
        assert res["passed"] is True

    def test_source_link_line_fails(self):
        note = "**来源链接**：[原文](https://a.com)\n\n" + VALID_NOTE
        res = verify_note_mechanical(note, note_type="")
        assert res["passed"] is False
        assert any("来源链接" in i for i in res["issues"])

    def test_source_url_in_body_fails(self):
        note = VALID_NOTE + "\n\n正文引用了 https://a.com/post/1 当论据"
        res = verify_note_mechanical(note, note_type="", source_url="https://a.com/post/1")
        assert res["passed"] is False
        assert any("来源链接" in i for i in res["issues"])

    def test_empty_source_url_no_false_positive(self):
        res = verify_note_mechanical(VALID_NOTE, note_type="", source_url="")
        assert res["passed"] is True


class TestWordCount:
    """字数只做两件事（DECISION-20260915 content-first）：内容缺失兜底、失控保护。

    **不再有任何上限压制**，也不再有「比例带」与「轻微越界 warning」——
    只要存在「不能比 X 长」的线，子 Agent 就会去贴它，必然产生「为过门禁而砍内容」。
    """

    def test_middle_length_has_no_word_issues(self):
        # 中段任意长度都不该有字数 issue / warning（框架与源长度都无关）
        for n in (500, 1000, 2600, 5000):
            res = verify_note_mechanical(_body(n), note_type="key_points")
            assert res["passed"] is True
            assert not any("字数" in i or "内容缺失" in i or "失控" in i for i in res["issues"])
            assert not any("字数" in w for w in res["warnings"])

    def test_below_min_words_blocked(self):
        # 299 < 300 → 内容缺失，硬拦（下限与 note_type 无关）
        res = verify_note_mechanical(_body(299), note_type="key_points")
        assert res["passed"] is False
        assert any("内容缺失" in i for i in res["issues"])

    def test_min_words_boundary_passes(self):
        res = verify_note_mechanical(_body(300), note_type="key_points")
        assert res["passed"] is True

    def test_min_words_applies_without_note_type(self):
        # 旧行为是「note_type 未注册就跳过字数检查」；现行下限与类型无关，缺失必拦
        res = verify_note_mechanical(_body(3), note_type="")
        assert res["passed"] is False
        assert any("内容缺失" in i for i in res["issues"])

    def test_runaway_ceiling_uses_absolute_floor(self):
        # 无源长：上限 = 绝对 8000
        assert verify_note_mechanical(_body(8000), note_type="").get("passed") is True
        res = verify_note_mechanical(_body(8001), note_type="")
        assert res["passed"] is False
        assert any("失控保护" in i for i in res["issues"])

    def test_runaway_ceiling_scales_with_source(self):
        # 源长 20000 → 上限 max(8000, 60000) = 60000：长源写长不会撞上限
        res = verify_note_mechanical(_body(20000), note_type="structured",
                                     source_chars=20000)
        assert res["passed"] is True
        # 源长 1000 → 上限 max(8000, 3000) = 8000
        res2 = verify_note_mechanical(_body(8001), note_type="structured",
                                      source_chars=1000)
        assert res2["passed"] is False


class TestWordCountHelper:
    """count_note_words：去空白后计字符数。"""

    def test_counts_non_whitespace_chars(self):
        # 「# 标题\n\n正文  两段」→ 去空白后「#标题正文两段」共 7 字符（含 # 号）
        assert count_note_words("# 标题\n\n正文  两段") == 7

    def test_empty_note(self):
        assert count_note_words("") == 0


class TestWordLimitsConsistency:
    """防漂移：NOTE_WORD_LIMITS 仅是「参考值记录」，且必须与模板声明一致。

    DECISION-20260915 后所有模板都改声明「参考值（源长×比例带）」，不再有固定硬区间；
    NOTE_WORD_LIMITS 保留常量供对齐与查阅，**不参与任何拦截或抽检触发**。
    """

    @pytest.mark.parametrize("note_type", sorted(NOTE_WORD_LIMITS))
    def test_templates_declare_reference_not_fixed_range(self, note_type):
        prompt = templates_mod.NOTE_TEMPLATES[note_type]["prompt"]
        assert "参考值" in prompt and "源长" in prompt, \
            f"{note_type} 应声明 source-aware 参考值（含『参考值』『源长』）"
        assert not re.search(r"单篇正文\s*\d+～\d+\s*字", prompt), \
            f"{note_type} 不应声明固定字数硬区间（已改为参考值）"

    def test_limits_are_record_only(self):
        """NOTE_WORD_LIMITS 不得再影响判定：同一篇笔记换 any note_type 结果一致。"""
        res_a = verify_note_mechanical(_body(2600), note_type="opinion")  # 超出 opinion 旧区间
        res_b = verify_note_mechanical(_body(2600), note_type="structured")
        assert res_a["passed"] is True and res_b["passed"] is True
        assert res_a["issues"] == res_b["issues"] == []

    def test_covers_all_templates(self):
        assert set(NOTE_WORD_LIMITS) == set(templates_mod.NOTE_TEMPLATES)


class TestSelfcheckAndGatePrompt:
    """建议 5：AI 审核员链路的篇幅自检维度（SELFCHECK 第③条 + GATE_PROMPT 评分项）。"""

    def test_selfcheck_declares_four_items(self):
        assert "四条专项自检" in templates_mod.QUALITY_GATE_SELFCHECK

    def test_selfcheck_has_coverage_and_length_items(self):
        # ③ 覆盖自检（内容完整优先）；④ 篇幅自检（只守下限）
        sc = templates_mod.QUALITY_GATE_SELFCHECK
        assert "③ 覆盖自检" in sc and "④ 篇幅自检" in sc

    def test_gate_prompt_weights_completeness(self):
        # 评分维度 7 改为「内容完整（权重最高）」，取代旧的「篇幅达标」
        assert "内容完整" in templates_mod.QUALITY_GATE_PROMPT
        assert "篇幅达标" not in templates_mod.QUALITY_GATE_PROMPT


class TestCoverageFirstGeneration:
    """生成侧「内容完整优先」（DECISION-20260915 §7）：所有模板须注入覆盖铁律，
    且**不得再出现诱导压缩的表述**；按源长注入具体篇幅目标。"""

    def test_all_templates_carry_coverage_rules(self):
        for k, v in templates_mod.NOTE_TEMPLATES.items():
            p = v["prompt"]
            assert "内容完整优先" in p, f"{k} 缺覆盖优先规范"
            assert "禁止合并要点" in p, f"{k} 缺「禁止合并要点」"
            assert "禁止概括替代" in p, f"{k} 缺「禁止概括替代」"

    def test_no_compression_threat_left(self):
        # 旧口径「写长了会被父 Agent 抽检，要求重压」会诱导子 Agent 主动压短 —— 必须消失
        for k, v in templates_mod.NOTE_TEMPLATES.items():
            assert "要求重压" not in v["prompt"], f"{k} 仍含压缩威胁句"
            assert "超参考带上沿" not in v["prompt"], f"{k} 仍含参考带上沿威胁"

    def test_reference_band_raised_to_30_50(self):
        for k, v in templates_mod.NOTE_TEMPLATES.items():
            p = v["prompt"]
            assert "源长×25%" not in p, f"{k} 仍用旧 25% 下沿"
            assert "源长×30%" in p, f"{k} 未声明新下沿 30%"

    def test_coverage_guide_injected_by_source_chars(self):
        short = templates_mod.get_note_prompt("structured")
        assert "附：本篇篇幅目标" not in short      # 未给源长 → 不注入
        g = templates_mod.render_coverage_guide(20000)
        assert "20000" in g and "6000～10000" in g
        assert "上限不硬卡" in g and "覆盖优先于字数" in g
        assert "源长的 60%" in g  # 防复述式膨胀的反向警戒
        # 短源不给目标（避免误导），< 400 字返回空
        assert templates_mod.render_coverage_guide(300) == ""

    def test_source_chars_of_ignores_whitespace(self):
        assert templates_mod.source_chars_of("ab c" + chr(10) + "d") == 4
        assert templates_mod.source_chars_of("") == 0


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


class TestNoRetryBypass:
    """**重试放行机制已删除**（DECISION-20260915）。

    旧机制（2026-09-10）为「源本身撑不起固定字数区间」而设：同 URL 二次重交仍纯字数越界
    则放行。现行两条字数硬拦——内容缺失（<300）与失控保护（>max(8000,源长×3)）——
    都是确定性缺陷，放行等于把坏笔记写进库。本类改为守住「永不因重试而放行」。
    """

    @pytest.fixture(autouse=True)
    def _stub_env(self, monkeypatch, tmp_path):
        self.save_calls = []
        self.log_calls = []
        monkeypatch.setattr(articles_main, "save_summarized_article",
                            lambda *a, **k: (self.save_calls.append(1), ("fmt", "f.md"))[1])
        monkeypatch.setattr(dedup, "mark_summarized", lambda *a, **k: None)
        monkeypatch.setattr(dedup, "_CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dedup, "_INDEX_FILE", str(tmp_path / "dedup.json"))
        monkeypatch.setattr(gate_blockers_mod, "GATE_BLOCKERS_BASE",
                            str(tmp_path / "gate_blockers.jsonl"))
        real_log = gate_blockers_mod.log_gate_block

        def _spy(*a, **k):
            self.log_calls.append(k.get("action", "blocked"))
            return real_log(*a, **k)

        monkeypatch.setattr(gate_blockers_mod, "log_gate_block", _spy)

    def _seed_block(self, url):
        gate_blockers_mod.log_gate_block(source="queue", note_type="key_points",
                                         url=url, title="旧标题", issues=["内容缺失"])

    def test_content_missing_blocked_repeatedly(self):
        # 同一 URL 反复重交 299 字笔记：每次都拦，绝不因「已拦过」而放行
        for _ in range(3):
            res = articles_main.save_summary_only({
                "summarized_content": _body(299), "original_url": "https://g.com/nb1",
                "note_type": "key_points"})
            assert res.get("success") is False
            assert res.get("message", "").startswith("VERIFIER_FAILED")
        assert self.save_calls == []
        assert self.log_calls == ["blocked"] * 3

    def test_runaway_blocked_even_after_history(self):
        # 台账已有历史拦截记录，失控保护仍拦截（旧机制会在此放行）
        self._seed_block("https://g.com/nb2")
        res = articles_main.save_summary_only({
            "summarized_content": _body(9000), "original_url": "https://g.com/nb2",
            "note_type": "key_points"})
        assert res.get("success") is False
        assert self.save_calls == []

    def test_h1_still_never_bypassed(self):
        for _ in range(3):
            gate_blockers_mod.log_gate_block(source="queue", note_type="",
                                             url="https://g.com/nb3", title="t", issues=["一级标题"])
        res = articles_main.save_summary_only({
            "summarized_content": "# 标题\n\n" + VALID_NOTE, "original_url": "https://g.com/nb3",
            "note_type": ""})
        assert res.get("success") is False
        assert self.save_calls == []

    def test_no_url_blocked(self):
        # 无 URL 无法定位台账 → 缺陷照拦
        res = articles_main.save_summary_only({
            "summarized_content": _body(299), "original_url": "",
            "note_type": "key_points"})
        assert res.get("success") is False
        assert self.save_calls == []


class TestContentSignalGate:
    """内容判据门禁（DECISION-20260915）：字数比例带废除，抽检触发改由 content_signals 提供。

    关键回归守卫：**「笔记比源长」不再触发任何信号**（这条线一旦复活，子 Agent 就会
    为过门禁而砍内容，正是用户踩过的坑）。
    """

    def _v(self, note, source="", note_type="", source_chars=None):
        return verify_note_mechanical(
            note, note_type=note_type, source_url="https://g.com/x",
            source_chars=source_chars if source_chars is not None else len(source),
            source_text=source)

    def test_note_longer_than_source_has_no_length_flag(self):
        # 源 1000 字 / 笔记 2600 字（比值 2.6）→ 不得有任何「字数/比例」类信号
        source = "原文讲述方法论与案例，包含若干要点与推导过程。" * 50   # ~1000 字
        r = self._v(_body(2600), source=source, source_chars=len(source), note_type="structured")
        assert r["passed"] is True
        assert not any(("超参考值" in f) or ("偏短" in f) or ("字数" in f)
                       for f in r["review_flags"])

    def test_short_note_vs_long_source_has_no_flag(self):
        # 源 20000 字 / 笔记 1200 字（比值 0.06）→ 也不因「偏短」报警（那是旧判据）
        r = self._v(_body(1200), source=_body(20000), source_chars=20000, note_type="structured")
        assert r["passed"] is True
        assert not any(("偏短" in f) or ("超参考值" in f) for f in r["review_flags"])

    def test_anchor_recall_flags_missing_facts(self):
        # 源含数字锚点，笔记一个都没写 → 触发「疑遗漏核心事实」
        source = "营收 1200 万，2024 年增长 300%，目标 5000 万。"
        r = self._v(_body(600), source=source, source_chars=len(source), note_type="structured")
        assert r["passed"] is True
        assert any("疑遗漏核心事实" in f for f in r["review_flags"])

    def test_anchor_recall_not_applicable_below_min(self):
        # 源锚点 < 4 → 该判据不适用，不得报缺
        source = "只有 1200 万这一个数字。"
        r = self._v(_body(600), source=source, source_chars=len(source), note_type="structured")
        assert not any("疑遗漏核心事实" in f for f in r["review_flags"])

    def test_structure_missing_flags(self):
        # structured 模板声明「分层速览」「正反例对照」「延伸思考」等必备模块；
        # 正文只是重复字 → 触发结构缺失
        r = self._v(_body(1500), note_type="structured")
        assert r["passed"] is True
        assert any("结构缺失" in f for f in r["review_flags"])

    def test_structure_complete_no_flag(self):
        # 按真实笔记的写法给齐必备模块（标题措辞与线上一致）
        note = ("**作者**：张三\n\n> **30秒速览**：一句话结论。\n\n"
                "## 三、正反例对照\n\n"
                "| 场景 | ✅ 正例 | ❌ 反例 |\n|---|---|---|\n| a | b | c |\n\n"
                "## 四、延伸思考\n\n1. ？\n\n" + _body(400))
        r = self._v(note, note_type="structured")
        assert not any("结构缺失" in f for f in r["review_flags"])

    def test_review_flags_propagate_to_save_result(self, tmp_path, monkeypatch):
        # 内容判据命中后落盘，save_summary_only 结果须带回 review_flags（供父 Agent 抽检）
        monkeypatch.setattr(articles_main, "save_summarized_article",
                            lambda *a, **k: ("fmt", "f.md"))
        monkeypatch.setattr(dedup, "mark_summarized", lambda *a, **k: None)
        monkeypatch.setattr(dedup, "_CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dedup, "_INDEX_FILE", str(tmp_path / "dedup.json"))
        res = articles_main.save_summary_only({
            "summarized_content": _body(1500), "original_url": "https://g.com/cs1",
            "note_type": "structured"})
        assert res.get("success") is True
        assert any("结构缺失" in f for f in res.get("review_flags", []))
