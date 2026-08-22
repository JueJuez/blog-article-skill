"""scys boilerplate 污染修复回归测试。

背景（2026-08-21 发现）：
- scys 页面抓取的原始 Markdown 含导航 boilerplate「首页/项目/航海/聚会/圈友/寻鲸之旅/AI问答」
- 其中「AI问答」的「问答」命中 INTERVIEW_KEYWORDS，导致 309 篇全部被误判为 interview
- 「分享」在 scys 正文高频出现（185 篇），作为 KEY_POINTS_KEYWORDS 过于泛化

修复方案：
1. classify_note_type 入口增加 _strip_scys_boilerplate 预处理
2. INTERVIEW_KEYWORDS 去掉「问答」（scys 导航噪声；真访谈靠「访谈/对谈/专访/对话」+ 内容级兜底）
3. KEY_POINTS_KEYWORDS 去掉「分享」（过于泛化；真口播靠「公开课/讲座/演讲/播客/直播」等）

RED 状态：实现未落地前，下列用例应失败。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from prompts.classify import classify_note_type


# --------------------------------------------------------------------------
# scys boilerplate 不再污染分类
# --------------------------------------------------------------------------

def test_scys_boilerplate_not_classified_as_interview():
    """scys 导航含「AI问答」但正文是普通经验复盘 -> 不应被误判为 interview。

    回归 2026-08-21 实战：309 篇 scys 文章全部因 boilerplate「AI问答」被误判 interview。
    """
    title = "我用Cursor做了一个DeepSeek提示词管理工具"
    content = (
        "---\n\n"
        "首页\n项目\n航海\n聚会\n圈友\n寻鲸之旅\nAI问答\n"
        "靓仔\n关注\n2025-04-14 18:11\n精华\n"
        "零基础用Cursor + Claude3.7做了一个 DeepSeek 提示词管理工具\n"
        "听全文\n原文朗读\n\n"
        "首先要感谢生财3月份AI网站开发航海，让我这个零基础的人也能做出产品。"
    )
    assert classify_note_type(title, content) != "interview"


def test_scys_boilerplate_structured_article():
    """scys 教程类文章（boilerplate + 教学超信号）-> structured。"""
    title = "零基础用Cursor做AI应用：保姆级教程"
    content = (
        "---\n\n"
        "首页\n项目\n航海\n聚会\n圈友\n寻鲸之旅\nAI问答\n"
        "亦仁\n关注\n2026-07-16 19:14\n精华\n"
        "零基础用Cursor做AI应用：保姆级教程\n\n"
        "本文手把手教你如何从零开始用Cursor开发AI应用。"
    )
    assert classify_note_type(title, content) == "structured"


def test_scys_real_interview_still_interview():
    """scys 真访谈（标题含「访谈」）-> 仍为 interview，不受 boilerplate 影响。"""
    title = "访谈生财传术师一只仙：每天只工作2小时"
    content = (
        "---\n\n"
        "首页\n项目\n航海\n聚会\n圈友\n寻鲸之旅\nAI问答\n"
        "关注\n精华\n"
        "访谈生财传术师一只仙\n\n"
        "今天我们访谈了一只仙，聊聊她的工作方式。"
    )
    assert classify_note_type(title, content) == "interview"


# --------------------------------------------------------------------------
# 「问答」不再作为 interview 关键词
# --------------------------------------------------------------------------

def test_qa_keyword_not_interview():
    """「问答」单独出现不触发 interview（scys 导航噪声词）。

    注意：真访谈靠「访谈/对谈/专访/对话」标题关键词 + 内容级兜底。
    """
    title = "AI问答：普通人怎么用AI赚钱"
    content = "这是一篇关于AI赚钱的问答帖子，回答了新手常见问题。"
    assert classify_note_type(title, content) != "interview"


# --------------------------------------------------------------------------
# 「分享」不再作为 key_points 关键词
# --------------------------------------------------------------------------

def test_share_keyword_not_key_points():
    """「分享」单独出现不触发 key_points（过于泛化，scys 正文高频）。

    注意：真口播靠「公开课/讲座/演讲/播客/直播/口播」等更精确的信号。
    """
    title = "我做了一个AI工具的经验分享"
    content = "这篇文章分享了我用AI编程做工具的完整经验，包括踩坑和解决方案。"
    assert classify_note_type(title, content) != "key_points"


def test_share_with_tutorial_signal_to_structured():
    """「分享」+ 教学超信号 -> structured（不被「分享」抢成 key_points）。"""
    title = "AI编程实战分享：从零搭建SaaS的保姆级教程"
    content = "本文分享如何从零开始搭建一个SaaS产品。"
    assert classify_note_type(title, content) == "structured"


# --------------------------------------------------------------------------
# 现有分类行为不受影响（回归保护）
# --------------------------------------------------------------------------

def test_real_talk_still_key_points():
    """真演讲/播客（有「演讲/播客」词）仍走 key_points。"""
    assert classify_note_type("2024 AI 趋势公开演讲") == "key_points"
    assert classify_note_type("每周闲聊播客 Vol.12") == "key_points"


def test_real_interview_still_interview():
    """真访谈（有「访谈/对话/专访」词）仍走 interview。"""
    assert classify_note_type("与张三深度对话：AI 创业真相") == "interview"
    assert classify_note_type("独家专访：某创始人复盘失败") == "interview"
