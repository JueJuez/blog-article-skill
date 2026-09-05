"""模板借鉴移植回归测试（2026-09-05，离线、纯字符串断言，不写盘）。

背景：
- 来源 A：ppt-master-main/content/reading/BOOK_DIGEST_METHODOLOGY.md（拆书方法论，
  RIA-TV++ 裁剪版）→ 移植进 reading 模板（论点间关系 / 三重验证 / 边界与常见误用 /
  三句话带走），并部分外溢到 case（单案例假设、基线口径）与 structured（术语卡常识差异列）
- 来源 B：ppt-master-main/.workbuddy/skills/social-content（机制分型分发门 +
  单案例「假设待校准」纪律）→ 移植进 dissection 模板（分发门归类、待校准标注）
- 来源 C：三重验证 V1「跨域佐证」的消费端轻量版 → opinion 论据佐证强度标注

RED 状态：实现未落地前，下列用例应失败。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from prompts.templates import NOTE_TEMPLATES


def _p(note_type: str) -> str:
    assert note_type in NOTE_TEMPLATES, f"未注册的模板类型: {note_type}"
    return NOTE_TEMPLATES[note_type]["prompt"]


# --------------------------------------------------------------------------
# 来源 A：拆书方法论 → reading 模板（4 点）
# --------------------------------------------------------------------------

def test_reading_prompt_argument_relations():
    """全书地图须标注论点间关系（并列 / 递进 / 对比 / 反驳），防线性列表失真。"""
    p = _p("reading")
    assert "论点间关系" in p
    assert "递进" in p
    assert "反驳" in p


def test_reading_prompt_triple_verification():
    """核心论点拆解须引入三重验证筛选：跨域佐证 / 预测力 / 独特性。"""
    p = _p("reading")
    assert "三重验证" in p
    assert "跨域" in p
    assert "预测力" in p
    assert "独特" in p


def test_reading_prompt_boundary_and_misuse():
    """适用边界须扩为「边界 + 常见误用」（作者警告的失败模式 / 预警信号）。"""
    p = _p("reading")
    assert "常见误用" in p
    assert "预警" in p


def test_reading_prompt_takeaway_three_sentences():
    """收束处可附「如果只带走三句话」极简清单（与既有内容重复时省略）。"""
    p = _p("reading")
    assert "只带走三句话" in p


# --------------------------------------------------------------------------
# 来源 B：social-content → dissection 模板（2 点）
# --------------------------------------------------------------------------

def test_dissection_prompt_distribution_gate():
    """模具表须有分发门归类（算法 / 订阅 / 社交主导 + 第一抓手）。"""
    p = _p("dissection")
    assert "分发门" in p
    assert "算法" in p
    assert "订阅" in p
    assert "社交" in p


def test_dissection_prompt_single_case_hypothesis():
    """模具须标注「单案例假设待校准」，换平台 / 人群验证后才当普适规律。"""
    p = _p("dissection")
    assert "待校准" in p


# --------------------------------------------------------------------------
# 来源 A 外溢：case 模板（2 点）
# --------------------------------------------------------------------------

def test_case_prompt_baseline_discipline():
    """结果与数据须注明口径与对比基线；原文未给基线要显式标注。"""
    p = _p("case")
    assert "基线" in p


def test_case_prompt_single_case_hypothesis():
    """可复用经验须标注单案例假设属性，换场景验证后再当普适规律。"""
    p = _p("case")
    assert "假设" in p
    assert "验证" in p


# --------------------------------------------------------------------------
# 来源 A 外溢：structured 术语卡（1 点）
# --------------------------------------------------------------------------

def test_structured_term_card_common_sense_diff():
    """关键术语卡须含「与常识用法的差异」列（作者私房术语防望文生义）。"""
    p = _p("structured")
    assert "与常识" in p


# --------------------------------------------------------------------------
# 来源 C：三重验证消费端轻量版 → opinion 论据拆解（1 点）
# --------------------------------------------------------------------------

def test_opinion_prompt_evidence_strength():
    """论据拆解须标注佐证强度（单一案例 / 多源跨域 / 纯逻辑推演）。"""
    p = _p("opinion")
    assert "佐证强度" in p
    assert "单一案例" in p


# --------------------------------------------------------------------------
# 回归护栏：既有核心标记不受本次改动影响
# --------------------------------------------------------------------------

def test_guard_reading_core_sections_intact():
    """reading 原有骨架（全书地图 / 核心论点拆解 / 争议与不同声音 / 金句）仍在。"""
    p = _p("reading")
    for marker in ("全书地图", "核心论点拆解", "争议与不同声音", "金句摘录"):
        assert marker in p, marker


def test_guard_dissection_core_fields_intact():
    """dissection 原有模具字段（标题公式占位符 / 钩子 / CTA / 禁区）仍在。"""
    p = _p("dissection")
    for marker in ("标题公式", "占位符", "钩子", "CTA", "禁区"):
        assert marker in p, marker


def test_guard_case_core_sections_intact():
    """case 原有骨架（背景与约束 / 关键决策 / 结果与数据 / 可复用经验）仍在。"""
    p = _p("case")
    for marker in ("背景与约束", "关键决策", "结果与数据", "可复用经验"):
        assert marker in p, marker
