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

from prompts.templates import (
    NOTE_TEMPLATES, UNIVERSAL_RULES,
    normalize_note_metadata, format_note_with_prompt,
    parse_quality_gate, should_gate_retry, build_gate_critique,
    QUALITY_GATE_PROMPT,
)
from prompts.classify import classify_note_type
from shared.subtitle_clean import preprocess_segments, preprocess_text


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


def test_reading_book_title_heuristic():
    """书名号《》强信号 → reading（标题无「读书/书评」词也能识别，如《当下的力量》）。"""
    assert classify_note_type("《当下的力量》——你是来享受生命的，而不是来演绎完美的。") == "reading"


def test_roundup_bang_keyword():
    """「榜/榜单/红黑榜」等横评信号 → roundup（标题无「测评」也能识别，如「零食夯榜」）。"""
    assert classify_note_type("无广！砸2800元试遍全网零食，半年筛出的高吃商零食夯榜！") == "roundup"
    assert classify_note_type("2024 年度效率工具红黑榜") == "roundup"


def test_interview_content_level_without_title_cue():
    """内容级访谈识别：标题无「访谈/对话」词，但正文是主持人向特定嘉宾的人生探针 → interview。
    回归 2026-07-21 实战：真实链接「95后女老板Judy」标题无 cue，靠内容级命中。"""
    title = "95后女老板Judy的创业故事"
    content = (
        "你好像都是很知道自己想要什么\n"
        "你当时是怎么决定休学创业的\n"
        "你后来卖掉公司的时候\n"
        "你选择创业这条路我觉得很酷"
    )
    assert classify_note_type(title, content) == "interview"


def test_video_keyword_no_longer_forces_key_points():
    """回归：「视频」已从 KEY_POINTS 移除（URL 本身即载体，不该当类型信号）。
    仅含「视频」而无其他 cue 的，不再被抢成 key_points。"""
    # 教学类视频 → structured（教学超信号拦截，而非被「视频」抢成口播）
    assert classify_note_type("从零搭建个人笔记系统 视频") == "structured"
    # 纯「视频」无其它 cue → 落入默认兜底 general（P1-4，不再误归 key_points/structured）
    assert classify_note_type("某产品发布视频", "") == "general"


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


# --------------------------------------------------------------------------
# C：思维模型透镜「强制证据」（每条适用模型须给 洞察+原文证据句）
# --------------------------------------------------------------------------

def test_thinking_lens_requires_evidence():
    """THINKING_LENS 须含「强制证据」红线：每条适用模型须给 洞察+原文证据句，
    且禁止只写「用了X模型」这类空话。"""
    u = UNIVERSAL_RULES
    assert "强制证据" in u
    assert "原文证据句" in u
    assert "用了" in u and "空话" in u


def test_all_templates_require_evidence():
    """全部模板的「延伸思考 / 思维模型透镜」须要求「洞察 + 原文证据句（「」括起）」。
    （7 模板均内嵌 THINKING_LENS / UNIVERSAL_RULES §九，该要求对所有模板成立。）
    """
    for t in NOTE_TEMPLATES:
        p = NOTE_TEMPLATES[t]["prompt"]
        assert "原文证据句" in p, t
        assert "洞察" in p, t


# --------------------------------------------------------------------------
# E：读书模板「争议与不同声音」维度
# --------------------------------------------------------------------------

def test_reading_has_controversy_section():
    """READING_PROMPT 须含「争议与不同声音」段：作者回避点 / 不同声音 / 与已知冲突。"""
    p = NOTE_TEMPLATES["reading"]["prompt"]
    assert "争议与不同声音" in p
    assert "作者回避" in p
    assert "不同声音" in p
    assert "与你的冲突" in p or "与已知事实" in p


# --------------------------------------------------------------------------
# F：元数据格式统一（井号标签）
# --------------------------------------------------------------------------

def test_normalize_tag_line_to_hashtags():
    """normalize_note_metadata 把 `**标签**：访谈笔记 · 创业 · 人生决策` 转成井号标签行。"""
    md = "# 标题\n\n**标签**：访谈笔记 · 创业 · 人生决策\n\n正文…"
    out = normalize_note_metadata(md)
    assert "#访谈笔记 #创业 #人生决策" in out
    assert "**标签**" not in out


def test_format_with_prompt_normalizes_metadata():
    """format_note_with_prompt（add_metadata=False，常见保存路径）须把错误标签行归一为井号。"""
    md = "# 标题\n\n**标签**：案例拆解 · 创业\n\n**作者**：张三 | **来源链接**：[x](u)\n\n正文"
    out = format_note_with_prompt(md, add_metadata=False)
    assert "#案例拆解 #创业" in out
    assert "**标签**" not in out


# --------------------------------------------------------------------------
# A：质量闸门（纯函数层，离线可测）
# --------------------------------------------------------------------------

def test_quality_gate_prompt_exists():
    """QUALITY_GATE_PROMPT 须定义且涵盖 6 条评分维度与 JSON 输出格式。"""
    assert QUALITY_GATE_PROMPT
    assert "score" in QUALITY_GATE_PROMPT
    assert "issues" in QUALITY_GATE_PROMPT


def test_parse_quality_gate_json():
    """parse_quality_gate 能从 JSON 块解析 score/issues/pass。"""
    raw = '闲话\n{"score": 72, "pass": false, "issues": ["篡改观点", "标题级空话"]}\n结束'
    g = parse_quality_gate(raw)
    assert g["score"] == 72
    assert g["passed"] is False
    assert "篡改观点" in g["issues"]


def test_parse_quality_gate_fallback_regex():
    """JSON 失败时，parse_quality_gate 用正则兜底取 score。"""
    raw = '评分 score: 90，整体不错，无大问题'
    g = parse_quality_gate(raw)
    assert g["score"] == 90
    assert g["passed"] is True


def test_should_gate_retry_logic():
    """should_gate_retry：闸门开启时，未达标（score<阈值）才 True；达标/无闸门数据则 False。"""
    import prompts.templates as _t
    orig = _t.QUALITY_GATE_ENABLED
    try:
        _t.QUALITY_GATE_ENABLED = True  # 逻辑测试与默认开关解耦
        assert _t.should_gate_retry({"score": 70, "passed": False, "issues": ["x"]}) is True
        assert _t.should_gate_retry({"score": 92, "passed": True, "issues": []}) is False
        assert _t.should_gate_retry(None) is False
    finally:
        _t.QUALITY_GATE_ENABLED = orig


def test_quality_gate_default_off():
    """决策(2026-07-21)：质量闸门默认关闭（NOTE_QUALITY_GATE=1 才开），闸门关时不触发重试。"""
    import prompts.templates as _t
    orig = _t.QUALITY_GATE_ENABLED
    try:
        _t.QUALITY_GATE_ENABLED = False
        assert _t.should_gate_retry({"score": 10, "passed": False, "issues": ["x"]}) is False
    finally:
        _t.QUALITY_GATE_ENABLED = orig


def test_build_gate_critique():
    """build_gate_critique 把问题拼成可追加到 prompt 的反馈段。"""
    g = {"score": 68, "issues": ["篡改观点", "缺证据句"]}
    c = build_gate_critique(g)
    assert "68" in c
    assert "篡改观点" in c
    assert "缺证据句" in c


# --------------------------------------------------------------------------
# D：分类边界用例（辩论/混合/独白 vs 访谈/纯播客 等）
# --------------------------------------------------------------------------

def test_debate_routes_to_opinion_not_interview():
    """辩论（有「辩论」词、无访谈 cue）→ opinion，不被误判 interview。"""
    assert classify_note_type("关于远程办公的辩论：利大于弊？", "我们今天辩论…") == "opinion"


def test_methodology_plus_case_to_structured():
    """方法论+案例混合且含教学超信号（手把手）→ structured（超信号优先于 case）。"""
    assert classify_note_type("手把手搭建独立站：附我的失败案例复盘") == "structured"


def test_solo_monologue_not_interview():
    """纯独白演讲（含「你有没有/你是否」但无主持人人生探针）→ key_points，非 interview。"""
    assert classify_note_type("如何摆脱精神内耗（演讲）", "你有没有发现… 你是否也曾…") == "key_points"


def test_podcast_solo_vs_interview():
    """播客 solo（无访谈 cue）→ key_points；播客访谈（含「访谈」）→ interview。"""
    assert classify_note_type("播客 Vol.5：和老王聊 AI") == "key_points"
    assert classify_note_type("播客访谈：和老王的深度对话") == "interview"


def test_failure_case_to_case():
    """失败案例复盘（无教学超信号）→ case（复盘案例信号命中）。"""
    assert classify_note_type("失败案例复盘：我的 SaaS 踩坑") == "case"


def test_opinion_debate_phrase():
    """观点文含「我反对」→ opinion。"""
    assert classify_note_type("我反对：过度自律是一种病") == "opinion"


def test_speech_plus_case_to_key_points():
    """演讲+案例（无超信号）→ key_points（演讲优先于 case）。"""
    assert classify_note_type("公开演讲：用我创业失败的案例讲透坚持") == "key_points"


def test_reading_book_title_heuristic_variant():
    """《》书名号强信号（另一本书）→ reading。"""
    assert classify_note_type("读《纳瓦尔宝典》后，我悟了") == "reading"


def test_roundup_redblack_list():
    """「红黑榜」横评信号 → roundup。"""
    assert classify_note_type("2024 年度效率 App 红黑榜") == "roundup"


def test_tutorial_video_explicit():
    """教程视频（课程超信号）→ structured。"""
    assert classify_note_type("Python 数据分析课程视频") == "structured"


def test_interview_content_level_host_probe():
    """内容级访谈：标题无 cue，但正文是主持人向嘉宾的人生探针（你创业/你休学）→ interview。"""
    title = "一个普通人的十年"
    content = (
        "你当时是怎么决定休学的\n"
        "你后来创业的时候\n"
        "你是怎么熬过那段日子的"
    )
    assert classify_note_type(title, content) == "interview"


# --------------------------------------------------------------------------
# B：字幕轻量清洗（离线单测）
# --------------------------------------------------------------------------

def test_subtitle_clean_fillers_and_dedup():
    """preprocess_segments：剔除空、合并相邻近重、短片段聚合、长句全局去重。"""
    segs = [
        {"start": 0.0, "duration": 2.0, "text": "我们看第一点"},
        {"start": 2.0, "duration": 2.0, "text": "我们看第一点"},  # 相邻近重 → 跳过
        {"start": 4.0, "duration": 2.0, "text": "然后他就决定创业了"},
        {"start": 6.0, "duration": 1.0, "text": "创业了"},          # ≤8 字 → 并入上一片段
        {"start": 7.0, "duration": 3.0,
         "text": "这个模型核心是复用。这个模型核心是复用。这个模型核心是复用。"},  # 长句去重
    ]
    out = preprocess_segments(segs)
    assert len(out) == 3, out
    # 短片段已并入上一片段
    assert "创业了" in out[1]["text"]
    # 长句去重：9 字重复句只保留一次
    assert out[2]["text"].count("这个模型核心是复用") == 1


def test_subtitle_clean_text():
    """preprocess_text：纯文本字幕的长句全局去重（≥8 字）+ 空行丢弃。"""
    t = "坚持长期主义才是真护城河。坚持长期主义才是真护城河。\n\n这就是答案。"
    out = preprocess_text(t)
    assert out.count("坚持长期主义才是真护城河") == 1
    assert "\n\n" not in out


