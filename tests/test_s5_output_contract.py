# -*- coding: utf-8 -*-
"""S5 输出契约回归测试（PLAN-20261026-low-cost-quality §9.7–§9.9，2026-10-27）。

契约要点：
1. 额外延伸关闭：九类模板不再要求/鼓励模型新增思维模型分析、延伸思考留白、
   自测三问、费曼、类比、迁移建议、无源行动项、认知误区与「我的立场」。
2. 源内信息保护：源内已有的分析、反驳、类比、方法、练习、迁移与行动建议照常保留。
3. 契约同步：SECTION_CONTRACTS 不再把已关闭小节当写作义务；审核 prompt / 自检 /
   抽检 rubric 与模板一致（缺延伸不再扣分，无源延伸本身是缺陷）。
4. 组织原则：「顺序不可颠倒」改为模块准入 + 受约束重组。
5. 护栏不破坏：S4 vizpurpose profile 的替换目标句仍在生产 prompt 中；
   历史稿分析层正则（shared/note_grounding）保留。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from prompts.templates import (
    NOTE_TEMPLATES,
    SECTION_CONTRACTS,
    SOURCE_FIDELITY_RULES,
    UNIVERSAL_RULES,
    QUALITY_GATE_PROMPT,
    QUALITY_GATE_SELFCHECK,
)

_ALL_TYPES = sorted(NOTE_TEMPLATES)

# 这些词出现在生产 prompt 中即代表仍在要求/鼓励模型新增延伸内容
_EXTENSION_MARKERS = (
    "延伸思考",
    "我的想法",
    "自测三问",
    "费曼",
    "笔记者类比",
    "思维模型透镜",
    "思维模型清单",
    "认知误区",
    "我的立场",
    "✍️",
)

# 统一关闭语与源内保护语（SOURCE_FIDELITY_RULES 为全部类型共享，故每类 prompt 都应含）
_CLOSURE_MARK = "不新增"
_SOURCE_PROTECT_MARK = "源内已有的分析"


@pytest.mark.parametrize("note_type", _ALL_TYPES)
def test_no_extension_sections_in_prompt(note_type):
    """九类生产 prompt 均不再包含任何「延伸/留白/自测/费曼/类比/误区/我的立场」条款。"""
    p = NOTE_TEMPLATES[note_type]["prompt"]
    hits = [m for m in _EXTENSION_MARKERS if m in p]
    assert hits == [], f"{note_type} 仍含延伸类标记：{hits}"


@pytest.mark.parametrize("note_type", _ALL_TYPES)
def test_closure_and_source_protection_present(note_type):
    """每类 prompt 都含「不新增」关闭语与「源内已有的分析」保护语。"""
    p = NOTE_TEMPLATES[note_type]["prompt"]
    assert _CLOSURE_MARK in p, note_type
    assert _SOURCE_PROTECT_MARK in p, note_type


def test_source_fidelity_rules_close_extension():
    """SOURCE_FIDELITY_RULES 单一真源：关闭模型新增延伸，保留源内分析并归属作者。"""
    assert "一律不写" in SOURCE_FIDELITY_RULES
    assert _SOURCE_PROTECT_MARK in SOURCE_FIDELITY_RULES
    # 旧的「放笔记者分析区」逃生门已移除
    assert "笔记者分析」区" not in SOURCE_FIDELITY_RULES


def test_universal_rules_analysis_boundary():
    """UNIVERSAL_RULES 第九节改为分析边界，不再含思维模型透镜。"""
    assert "分析边界" in UNIVERSAL_RULES
    assert "思维模型" not in UNIVERSAL_RULES
    assert "不做延伸分析" in UNIVERSAL_RULES


def test_structured_analysis_boundary_inline():
    """structured 内联路径同样含分析边界，且不含自测三问/费曼/思维模型。"""
    p = NOTE_TEMPLATES["structured"]["prompt"]
    assert "分析边界" in p
    assert "思维模型" not in p
    assert "自测三问" not in p
    assert "费曼" not in p


# ---------------------------------------------------------------- 契约同步

def test_section_contracts_drop_closed_sections():
    """已关闭小节不再是写作义务：structured/general 去「延伸思考」，opinion 去「认知误区/笔记者分析」。"""
    for t in ("structured", "general"):
        assert "延伸思考" not in SECTION_CONTRACTS[t]["source_dependent"], t
    assert "认知误区" not in SECTION_CONTRACTS["opinion"]["source_dependent"]
    assert "笔记者分析" not in SECTION_CONTRACTS["opinion"]["source_dependent"]
    # 源内仍有依据的模块保留为写作义务
    assert "正反双方" in SECTION_CONTRACTS["opinion"]["source_dependent"]
    assert "核心公式" in SECTION_CONTRACTS["structured"]["source_dependent"]


def test_quality_gate_checks_extension_as_defect():
    """审核 prompt：无源延伸是缺陷维度；不再把「思维模型落地」当加分检查。"""
    assert "无源延伸" in QUALITY_GATE_PROMPT
    assert "思维模型落地" not in QUALITY_GATE_PROMPT
    assert _SOURCE_PROTECT_MARK in QUALITY_GATE_PROMPT


def test_selfcheck_aligned():
    """作者自检：含无源延伸检查与模块准入，不再要求思维模型/新类比标签。"""
    assert "延伸" in QUALITY_GATE_SELFCHECK
    assert "模块按源准入" in QUALITY_GATE_SELFCHECK
    assert "思维模型" not in QUALITY_GATE_SELFCHECK
    assert "笔记者类比" not in QUALITY_GATE_SELFCHECK


def test_review_rubric_aligned():
    """抽检 rubric：D_fidelity 视无源延伸为缺陷；E_compliance 改模块准入措辞。"""
    from prompts import review_rubric
    d = review_rubric.DIMENSIONS["D_fidelity"]
    assert "无源延伸" in d
    e = review_rubric.DIMENSIONS["E_compliance"]
    assert "模块按源准入" in e


# ---------------------------------------------------------------- 组织原则

@pytest.mark.parametrize("note_type", _ALL_TYPES)
def test_no_fixed_order_phrase(note_type):
    """「顺序不可颠倒」退出正文组织条款（改为模块准入 + 受约束重组）。"""
    p = NOTE_TEMPLATES[note_type]["prompt"]
    assert "顺序不可颠倒" not in p, note_type


def test_opening_system_contract_kept():
    """开篇系统契约（作者行 / 禁 H1 / 标签行 / 主题词区块）不受组织改革影响。"""
    p = NOTE_TEMPLATES["structured"]["prompt"]
    assert "开篇" in p
    assert "禁止输出一级标题" in p
    assert "【核心主题词】" in p


def test_reorganization_principle_present():
    """受约束重组原则写入公共规则：可重组但不丢独有信息。"""
    assert "重组" in UNIVERSAL_RULES


# ---------------------------------------------------------------- 护栏

def test_vizpurpose_edit_targets_still_in_production_prompts():
    """S4 vizpurpose profile 的三处替换目标句必须仍在生产 prompt 中（改坏即 S4 留档失效）。"""
    from scripts.build_round import _VIZ_EDITS
    for old, _new in _VIZ_EDITS:
        found = any(old in NOTE_TEMPLATES[t]["prompt"] for t in NOTE_TEMPLATES)
        assert found, f"vizpurpose 替换目标句已不在任何生产 prompt 中：{old[:40]}…"


def test_table_mandatory_clauses_untouched():
    """S4 结论=不接生产：「必须用表格」与 chart 确定性触发句保持原样。"""
    assert "对比 / 参数 / 层级 / 盘点类内容**必须用表格**。" in UNIVERSAL_RULES
    kp = NOTE_TEMPLATES["key_points"]["prompt"]
    assert "对比 / 参数 / 公式类必须用表格。" in kp


def test_legacy_analysis_layer_regex_unchanged():
    """历史稿分析层正则保留（旧库笔记仍有笔记者分析/延伸思考等区）。"""
    from shared import note_grounding
    pat = note_grounding.ANALYSIS_HEADING.pattern
    assert "延伸思考" in pat and "笔记者分析" in pat


def test_credibility_annotation_retained():
    """（笔记者推断）就地标注机制保留（用于源内信息归纳与条件补齐）。"""
    for t in _ALL_TYPES:
        assert "笔记者推断" in NOTE_TEMPLATES[t]["prompt"], t
