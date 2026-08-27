"""第 8 类笔记模板「创作解剖 dissection」回归测试（离线、纯字符串断言，不写盘）。

背景（2026-08-26）：
- 订阅源（scys 等）大量存在「爆款拆解 / 带货复盘 / 账号运营」类内容
- 现有 case 模板总结「发生了什么」，缺少「可复用结构模具」提炼
  （标题公式 / 开头钩子 / 正文节奏 / 结尾 CTA / 禁区），读完不能直接套用到自己的创作
- 方法论来源：ppt-master-main 项目 social-content SKILL 的
  note_dissection_sop.md（LLM 提炼 Prompt）+ note_molds 字段结构，移植进本仓库模板体系

RED 状态：实现未落地前，下列用例应失败。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from prompts.templates import NOTE_TEMPLATES, UNIVERSAL_RULES, get_note_prompt
from prompts.classify import classify_note_type


# --------------------------------------------------------------------------
# 模板注册与形状
# --------------------------------------------------------------------------

def test_dissection_template_registered():
    """dissection 必须注册进 NOTE_TEMPLATES，且 name/desc/prompt 三字段齐全。"""
    assert "dissection" in NOTE_TEMPLATES
    t = NOTE_TEMPLATES["dissection"]
    assert t["name"]
    assert t["desc"]
    assert len(t["prompt"]) > 500


def test_get_note_prompt_dissection():
    """get_note_prompt 按 key 取到 dissection 模板，且回退逻辑不受影响。"""
    assert get_note_prompt("dissection") == NOTE_TEMPLATES["dissection"]["prompt"]
    assert get_note_prompt("不存在") == NOTE_TEMPLATES["structured"]["prompt"]


# --------------------------------------------------------------------------
# 模板核心内容：可复用结构模具字段（移植自 note_dissection_sop.md）
# --------------------------------------------------------------------------

def test_dissection_prompt_has_mold_fields():
    """核心区块齐全：标题公式 / 开头钩子 / 正文节奏 / 结尾 CTA / 禁区。"""
    p = NOTE_TEMPLATES["dissection"]["prompt"]
    assert "标题公式" in p
    assert "钩子" in p
    assert "节奏" in p
    assert "CTA" in p
    assert "禁区" in p


def test_dissection_prompt_placeholder_rule():
    """标题公式必须占位符化（[ ] 形式），保证模具换主题仍可用。"""
    p = NOTE_TEMPLATES["dissection"]["prompt"]
    assert "占位符" in p
    assert "[数字" in p or "[痛点" in p or "[结果" in p


def test_dissection_prompt_copyright_boundary():
    """版权边界（灵活版 · 2026-08-26 用户定调）：红线是「不侵权」而非「不借鉴」。

    - 微创新（套模具换主题/平台/人群再产出）被明确鼓励
    - 大众/公开素材人人可用，如实标注来源
    - 硬禁只剩两条：原创文案整段照搬当自己作品发布、直接搬运独家素材
    - 引用原句做学习证据（模具表「原文依据」列、金句摘录）不受限
    """
    p = NOTE_TEMPLATES["dissection"]["prompt"]
    assert "微创新" in p
    assert "侵权" in p
    assert "大众" in p
    assert "独家" in p


def test_dissection_prompt_universal_rules_merged():
    """与 key_points/case/opinion 同路径：UNIVERSAL_RULES 必须拼接进模板尾部。"""
    tail = UNIVERSAL_RULES[-40:]
    assert tail in NOTE_TEMPLATES["dissection"]["prompt"]


def test_dissection_prompt_credibility_annotation():
    """可信度标注约定：模板内出现『笔记者推断』（UNIVERSAL_RULES 或正文均可）。"""
    assert "笔记者推断" in NOTE_TEMPLATES["dissection"]["prompt"]


def test_dissection_prompt_reflection_zone():
    """须有「延伸思考 + 我的想法」留白区（全线统一）。"""
    p = NOTE_TEMPLATES["dissection"]["prompt"]
    assert "延伸思考" in p
    assert "我的想法" in p


# --------------------------------------------------------------------------
# 分类：内容创作域信号 → dissection
# --------------------------------------------------------------------------

def test_classify_daishou_dissection():
    """带货视频拆解 -> dissection。"""
    title = "拆解一条百万播放的带货视频：钩子到底放在第几秒"
    content = "这条视频我们逐帧拆解它的开头钩子、正文节奏和结尾CTA。"
    assert classify_note_type(title, content) == "dissection"


def test_classify_account_growth_fupan():
    """账号运营复盘（涨粉）-> dissection。"""
    title = "小红书账号运营复盘：30天涨粉10万，我做了什么"
    content = "这篇复盘我起号的完整过程：选题、发布节奏、评论区运营。"
    assert classify_note_type(title, content) == "dissection"


def test_classify_baokan_note_dissection():
    """爆款笔记拆解 -> dissection（不被 structured 的「拆解」抢走）。"""
    title = "爆款笔记拆解：这篇为什么能爆"
    content = "从标题公式到封面钩子，完整拆解这篇笔记的结构。"
    assert classify_note_type(title, content) == "dissection"


def test_classify_daishou_fupan():
    """带货营收复盘 -> dissection（不被 case 的「复盘」抢走）。"""
    title = "视频号图书带货单号营收50万复盘"
    content = "回顾这个带货账号从0到50万的全过程，包括选品和话术。"
    assert classify_note_type(title, content) == "dissection"


# --------------------------------------------------------------------------
# 分类护栏：既有类型优先级不受影响
# --------------------------------------------------------------------------

def test_guard_tutorial_super_signal_wins():
    """教学超信号优先：涨粉保姆级教程 -> structured（方法论教程，非拆解复盘）。"""
    title = "涨粉保姆级教程：从0到1做起一个账号"
    content = "本文手把手教你如何规划选题和发布节奏。"
    assert classify_note_type(title, content) == "structured"


def test_guard_interview_wins():
    """访谈优先：访谈涨粉话题 -> interview。"""
    assert classify_note_type("访谈：聊聊涨粉这件事", "我们访谈了一位涨粉很快的博主。") == "interview"


def test_guard_roundup_wins():
    """盘点优先：带货工具盘点 -> roundup（多对象并列比较，非拆解）。

    注：标题不含「直播」——「直播」是既有 key_points 信号（直播回放课），
    该既有路由与本次改动无关，此用例只锁定 roundup 与 dissection 的优先级。
    """
    title = "带货工具盘点：5个提效神器横评"
    content = "对比5个工具的优劣，给出选购建议。"
    assert classify_note_type(title, content) == "roundup"


def test_guard_plain_business_case_stays_case():
    """普通商业案例（含「案例」信号、无内容创作域信号）-> 仍为 case。"""
    title = "创业失败案例复盘：我烧光了500万"
    content = "复盘这次创业从融资到失败的完整过程。"
    assert classify_note_type(title, content) == "case"


def test_guard_generic_article_stays_structured():
    """普通干货文章（无内容创作域信号）-> 仍为 structured。"""
    title = "如何用第一性原理做产品决策"
    content = "本文讲方法论：如何拆解问题到基本要素。"
    assert classify_note_type(title, content) == "structured"


def test_guard_scys_boilerplate_not_dissection():
    """scys boilerplate 回归：导航噪声不触发 dissection；无特化信号时兜底 general（P1-4）。"""
    title = "我用Cursor做了一个DeepSeek提示词管理工具"
    content = (
        "---\n\n"
        "首页\n项目\n航海\n聚会\n圈友\n寻鲸之旅\nAI问答\n"
        "靓仔\n关注\n2025-04-14 18:11\n精华\n"
        "零基础用Cursor + Claude3.7做了一个 DeepSeek 提示词管理工具\n"
        "首先要感谢生财3月份AI网站开发航海，让我这个零基础的人也能做出产品。"
    )
    assert classify_note_type(title, content) != "dissection"
    assert classify_note_type(title, content) == "general"


# --------------------------------------------------------------------------
# P0-1 修复：纯领域词不触发 dissection（须配动作词）
# --------------------------------------------------------------------------

def test_guard_pure_domain_not_dissection():
    """纯领域词「私域」单独出现（无动作词）不触发 dissection -> 落到 structured。

    P0-1 根因回归：「私域运营方法论」曾被 step9 的纯领域词「私域」抢成 dissection，
    正确应走 structured（方法论复盘）。
    """
    title = "私域运营方法论：从0搭建你的私域池"
    content = "本文讲私域运营的底层方法论与实操步骤。"
    assert classify_note_type(title, content) != "dissection"
    assert classify_note_type(title, content) == "structured"


def test_guard_domain_without_action_falls_through():
    """领域词「带货」+ 方法论，但无动作词（拆解/复盘…）-> 不 dissection。"""
    title = "带货方法论：如何搭建带货团队"
    content = "本文讲带货业务的整体思路与组织方式，不涉及具体执行步骤。"
    assert classify_note_type(title, content) != "dissection"


def test_cooccurrence_domain_plus_action_still_dissection():
    """领域词 ∩ 动作词共现仍判 dissection（如「带货复盘」）。"""
    title = "视频号图书带货单号营收50万复盘"
    content = "回顾这个带货账号从0到50万的全过程，包括选品和话术。"
    assert classify_note_type(title, content) == "dissection"


# --------------------------------------------------------------------------
# P1-4：零信号兜底为 general
# --------------------------------------------------------------------------

def test_default_general_when_no_signal():
    """无任何特化信号 -> general（而非强塞 structured）。"""
    assert classify_note_type("今天天气真好") == "general"
    assert classify_note_type("随便聊聊") == "general"


def test_general_template_registered():
    """general 必须注册进 NOTE_TEMPLATES，且能取到 prompt（不静默回退）。"""
    assert "general" in NOTE_TEMPLATES
    assert get_note_prompt("general") == NOTE_TEMPLATES["general"]["prompt"]
