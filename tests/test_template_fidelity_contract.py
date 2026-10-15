"""源不支持模块不能靠机械硬拦逼模型编造；离线回归，不写vault。"""

import pytest

from prompts import templates
from prompts import content_signals as signals
from prompts.verifier import verify_note_mechanical


@pytest.mark.parametrize("note_type", ["structured", "general"])
def test_source_without_counterexample_can_omit_analysis(note_type):
    # 只有操作要点，没有反例或值得延伸的材料：保留速览与核心内容即可。
    note = "> **30秒速览**：先记录输入，再检查输出。\n\n## 操作要点\n" + (
        "记录每一步的输入和输出，保留原先可以运行的版本，再检查这次修改的结果。" * 15
    )
    gate = verify_note_mechanical(note, note_type=note_type)
    assert not any("结构缺失" in flag for flag in gate["content_blockers"])


def test_comparison_without_winner_does_not_require_recommendation():
    # 原文只给两项价格，没有同条件测评；不能为了推荐模块制造胜出结论。
    note = "## 对比矩阵\n|对象|原文价格|\n|---|---|\n|A|10元|\n|B|20元|\n"
    assert not signals.structure_missing(note, "roundup")["missing"]


def test_simulated_dialogue_does_not_require_fabricated_interview():
    # 类型错配的单人教学可按内容主线回退；本批不伪称分类器已修复。
    note = "## 讲解要点\n这里只有一人模拟工作情景并说明操作，没有嘉宾。"
    assert not signals.structure_missing(note, "interview")["missing"]


def test_partial_book_can_use_episode_map():
    # 单集书评仍有核心论点，但无全书目录；不得用缺全书地图逼其外推。
    note = "## 本集地图\n起因与转折。\n## 核心论点拆解\n解释已提供片段。"
    assert not signals.structure_missing(note, "reading")["missing"]


def test_unconditional_lede_and_reading_core_still_required():
    # 放松源相关模块，不能把既有可确定结构检查全部清空。
    assert "分层速览" in signals.structure_missing("## 内容\n要点", "structured")["missing"]
    assert "核心论点拆解" in signals.structure_missing("## 本集地图\n起因", "reading")["missing"]


def test_required_sections_do_not_follow_arbitrary_prompt_prose(monkeypatch):
    # 将来的文案含“必备”不能偷偷变更生产硬拦；结构契约需显式修改。
    entry = dict(templates.NOTE_TEMPLATES["structured"])
    entry["prompt"] += "\n正反例对照（必备）\n"
    monkeypatch.setitem(templates.NOTE_TEMPLATES, "structured", entry)
    assert signals.required_sections("structured") == ["分层速览"]


@pytest.mark.parametrize("note_type", list(templates.NOTE_TEMPLATES))
def test_all_prompt_paths_use_one_fidelity_contract(note_type):
    # structured内联与轻模板拼接必须部署同一套契约，禁止旧冲突指令残留。
    contract = getattr(templates, "SOURCE_FIDELITY_RULES", "")
    prompt = templates.get_note_prompt(note_type, 10000) + templates.QUALITY_GATE_SELFCHECK
    assert contract and prompt.count(contract) == 1
    for obsolete in ["默认所有陈述", "断言式", "一一对应的小节", "下限必须守", "正文无转述引导语"]:
        assert obsolete not in prompt, (note_type, obsolete)


def test_all_types_have_explicit_structure_contract():
    # 新增类型不能意外遗漏硬/条件结构契约；未知类型仍无机械结构假设。
    contracts = getattr(templates, "SECTION_CONTRACTS", {})
    assert set(contracts) == set(templates.NOTE_TEMPLATES)
    assert all("required" in value and "source_dependent" in value for value in contracts.values())
    assert signals.required_sections("unknown") == []


def test_section_contract_return_is_not_mutable_global_state():
    # 调用方修改返回列表，不能污染后续每篇笔记的门禁要求。
    original = signals.required_sections("structured")
    signals.required_sections("structured").append("伪模块")
    assert signals.required_sections("structured") == original
