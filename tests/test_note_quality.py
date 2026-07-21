"""笔记提质回归测试（离线、纯字符串断言，不写盘）。

覆盖 2026-07-21 决策（DECISION-20260721-note-quality）：
- 思维模型透镜：UNIVERSAL_RULES + structured 内联均含「思维模型」有序 LIST
- 分类修复：教学视频 → structured，演讲/播客 → key_points，
  访谈 → interview，盘点/横评 → roundup，读书/书摘 → reading
- 三类新模板：interview / roundup / reading 已注册且覆盖思维模型透镜
- 系列课地图：_render_series_overview 输出含「## 学习路径」段

RED 状态：实现未落地前，下列用例应全部失败。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from prompts.templates import NOTE_TEMPLATES, UNIVERSAL_RULES
from prompts.classify import classify_note_type


# --------------------------------------------------------------------------
# 思维模型透镜（有序 LIST · 按需触发）
# --------------------------------------------------------------------------

def test_thinking_lens_in_universal_rules():
    """UNIVERSAL_RULES 须含「思维模型」有序 LIST（第一性原理等），且按序+按需。"""
    u = UNIVERSAL_RULES
    assert "思维模型" in u
    assert "第一性原理" in u
    assert "二阶思维" in u
    assert "按顺序" in u
    assert "按需" in u


def test_thinking_lens_in_structured():
    """structured 内联等价规则也须含思维模型透镜（它与轻模板拼接 UNIVERSAL 不同路径）。"""
    p = NOTE_TEMPLATES["structured"]["prompt"]
    assert "思维模型" in p
    assert "第一性原理" in p


def test_all_templates_have_thinking_lens():
    """全部模板（含三类新模板）覆盖思维模型透镜（structured 内联 + 轻模板拼 UNIVERSAL_RULES）。"""
    for t in NOTE_TEMPLATES:
        assert "思维模型" in NOTE_TEMPLATES[t]["prompt"], t


def test_three_new_templates_registered():
    """访谈/盘点/读书三类模板已注册进 NOTE_TEMPLATES，且各自含思维模型透镜与专属结构。"""
    for k in ("interview", "roundup", "reading"):
        assert k in NOTE_TEMPLATES, k
        assert NOTE_TEMPLATES[k]["prompt"]
        assert "思维模型" in NOTE_TEMPLATES[k]["prompt"], k
    assert "问答" in NOTE_TEMPLATES["interview"]["prompt"]
    assert "对比矩阵" in NOTE_TEMPLATES["roundup"]["prompt"]
    assert "全书地图" in NOTE_TEMPLATES["reading"]["prompt"]


# --------------------------------------------------------------------------
# 分类修复：教学视频不被误判为口播要点
# --------------------------------------------------------------------------

def test_tutorial_video_to_structured():
    """含教学超信号（手把手/教程/从零）的视频 → structured，而非 key_points。"""
    assert classify_note_type("手把手教你用Cursor做AI应用【视频】") == "structured"
    assert classify_note_type("Python 从零教程视频") == "structured"


def test_talk_video_stays_key_points():
    """演讲/口播/播客（非访谈）类仍走 key_points（不被超信号误伤）。"""
    assert classify_note_type("2024 AI 趋势公开演讲【视频】") == "key_points"
    assert classify_note_type("每周闲聊播客 Vol.12") == "key_points"


def test_interview_routes_to_interview():
    """访谈/对话类内容 → interview 独立类型（不再误归 key_points）。"""
    assert classify_note_type("与张三深度对话：AI 创业真相") == "interview"
    assert classify_note_type("独家专访：某创始人复盘失败") == "interview"


def test_roundup_routes_to_roundup():
    """盘点/横评/测评类 → roundup 独立类型。"""
    assert classify_note_type("2024 年 10 个最佳 AI 写作工具盘点") == "roundup"
    assert classify_note_type("3 款主流笔记软件横向测评") == "roundup"


def test_reading_routes_to_reading():
    """读书/书摘/拆书类 → reading 独立类型。"""
    assert classify_note_type("《纳瓦尔宝典》读书笔记与摘抄") == "reading"
    assert classify_note_type("拆书稿：这本书讲透了复利") == "reading"


# --------------------------------------------------------------------------
# 系列课地图：学习路径段
# --------------------------------------------------------------------------

def test_series_overview_has_learning_path():
    """_render_series_overview 输出须含「## 学习路径」段并嵌入生成内容，且不丢原导航表。"""
    from videos.main import _render_series_overview
    rows = [(1, "入门：环境搭建", "讲清环境怎么配", "[笔记](./第01集_x.md)")]
    lp = "建议顺序：先 01 再 02；01 是后续先修。"
    md = _render_series_overview("测试系列", "http://example.com", rows, learning_path_md=lp)
    assert "## 学习路径" in md
    assert lp in md
    # 原有导航表不丢
    assert "## 各集导航" in md
