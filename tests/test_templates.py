"""模板升级回归测试（离线、纯字符串断言，不写盘）。

覆盖 2026-07-20 模板升级：
- structured 去水分：核心必备 + 按需可选，显式禁止凑模块
- 全线新增区块按模板性质分配（分层TL;DR / 正反例 / 延伸思考+留白 / 可信度标注）
- opinion 强化为「正反双方 + 我的立场」
- UNIVERSAL_RULES 正确拼接进轻模板
- get_note_prompt 回退到 structured
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from prompts.templates import (
    NOTE_TEMPLATES,
    UNIVERSAL_RULES,
    get_note_prompt,
    list_note_types,
)


# --------------------------------------------------------------------------
# 全局：4 个类型齐全
# --------------------------------------------------------------------------

def test_all_four_types_present():
    assert set(NOTE_TEMPLATES) == {"structured", "key_points", "case", "opinion"}


def test_get_note_prompt_fallback():
    assert get_note_prompt("不存在的类型") == NOTE_TEMPLATES["structured"]["prompt"]
    assert get_note_prompt("opinion") == NOTE_TEMPLATES["opinion"]["prompt"]


# --------------------------------------------------------------------------
# structured：去水分 + 按需模块
# --------------------------------------------------------------------------

def test_structured_no_padding_rule():
    """structured 必须显式声明「按需模块、没有不凑、禁止编造」。"""
    p = NOTE_TEMPLATES["structured"]["prompt"]
    assert "按需" in p
    # 不再出现「9大模块一个不能少」式的强制凑数措辞
    assert "9大必备笔记模块，不得缺项漏项" not in p
    assert ("不凑" in p) or ("没有则不写" in p) or ("宁缺毋滥" in p)


def test_structured_optional_modules_marked():
    """工具表/步骤/数据等改为可选，须标注「仅原文有才写」。"""
    p = NOTE_TEMPLATES["structured"]["prompt"]
    assert "可选模块" in p


# --------------------------------------------------------------------------
# 通用新增区块（按模板性质分配）
# --------------------------------------------------------------------------

def test_layered_tldr_in_heavy_templates():
    """structured / key_points 须有分层速览（TL;DR）。"""
    for t in ("structured", "key_points"):
        assert "速览" in NOTE_TEMPLATES[t]["prompt"], t


def test_pos_neg_examples_present():
    """正反例/正反双方对照须出现在 structured / case / opinion。"""
    for t in ("structured", "case", "opinion"):
        p = NOTE_TEMPLATES[t]["prompt"]
        assert ("正反例" in p) or ("正例" in p) or ("正反双方" in p), t


def test_reflection_writein_zone():
    """structured / key_points / case 须有「延伸思考 + 我的想法留白」。"""
    for t in ("structured", "key_points", "case"):
        p = NOTE_TEMPLATES[t]["prompt"]
        assert "延伸思考" in p, t
        assert "我的想法" in p, t


def test_opinion_stance_decision_zone():
    """opinion 须有「正反双方 + 我的立场」决策位。"""
    p = NOTE_TEMPLATES["opinion"]["prompt"]
    assert "我的立场" in p


def test_credibility_annotation():
    """可信度标注约定：全线出现『笔记者推断』标记约定。"""
    for t in NOTE_TEMPLATES:
        assert "笔记者推断" in NOTE_TEMPLATES[t]["prompt"], t


def test_quality_self_check_redline():
    """质量自检红线：UNIVERSAL_RULES 须正面定义『敷衍/烂输出』的几种典型长相。"""
    u = UNIVERSAL_RULES
    assert "质量自检红线" in u
    # 四种典型敷衍形态都要被点名
    assert "标题级空话" in u
    assert "目录式复述" in u
    assert "万能概括句" in u
    assert "凑数" in u
    # 自检项必须落到「读者脱离原文能否看懂」这一可执行标准
    assert "脱离原文" in u
    # 红线须覆盖全部 4 个模板（structured 内联 + 3 轻模板拼接 UNIVERSAL_RULES）
    assert "质量自检红线" in NOTE_TEMPLATES["structured"]["prompt"]
    for t in ("key_points", "case", "opinion"):
        assert "质量自检红线" in NOTE_TEMPLATES[t]["prompt"], t



# --------------------------------------------------------------------------
# UNIVERSAL_RULES 拼接
# --------------------------------------------------------------------------

def test_universal_rules_merged_into_light_templates():
    tail = UNIVERSAL_RULES[-40:]
    for t in ("key_points", "case", "opinion"):
        assert tail in NOTE_TEMPLATES[t]["prompt"], t


def test_list_note_types_shape():
    rows = list_note_types()
    assert {r["key"] for r in rows} == {"structured", "key_points", "case", "opinion"}
    for r in rows:
        assert r["name"] and r["desc"]
