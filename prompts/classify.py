"""按内容自动判定笔记类型

优先级：要点提炼（口播）> 观点卡 > 案例拆解 > 结构化复盘（兜底）。
都不命中时默认 structured（最完整、最安全的兜底形态）。
"""

# 命中即归为「要点提炼」的信号词（公开课/讲座/演讲/播客等口播类）
KEY_POINTS_KEYWORDS = [
    "公开课", "讲座", "演讲", "分享会", "分享", "talk", "ted", "播客",
    "访谈", "直播", "圆桌", "峰会", "论坛", "keynote", "口播", "视频",
]

# 命中即归为「观点卡」的信号词（评论/立场/随笔类）
OPINION_KEYWORDS = [
    "评论", "观点", "立场", "辩论", "犀利", "我反对", "我支持",
    "为什么我觉得", "说几句", "杂谈", "站队",
]

# 命中即归为「案例拆解」的信号词（案例/复盘/产品拆解类）
CASE_KEYWORDS = [
    "案例", "商业故事", "产品拆解", "实战记录", "商业案例", "复盘案例",
    "失败故事", "成功故事", "踩坑", "增长故事",
]

# 命中即归为「结构化复盘」的信号词（教程/方法论类）
STRUCTURED_KEYWORDS = [
    "教程", "课程", "指南", "方法论", "实操", "从零", "如何", "干货",
    "复盘", "拆解", "保姆", "手把手", "入门", "进阶",
]

# 教学/教程类超信号：命中即判 structured，优先级高于 KEY_POINTS 的"视频"匹配。
# 解决「教学视频」被误判为口播要点（key_points）的问题（见 DECISION-20260721-note-quality）。
TUTORIAL_SUPER_SIGNALS = ["手把手", "保姆", "实操", "从零", "教程", "课程", "step by step"]


def classify_note_type(title: str = "", content: str = "") -> str:
    """根据标题与正文开头，自动判定笔记类型。

    Args:
        title: 文章/视频标题
        content: 正文或字幕（只需前若干字符即可）

    Returns:
        "key_points" / "opinion" / "case" / "structured"
    """
    text = f"{title}\n{(content or '')[:800]}".lower()
    # 教学超信号优先：教学/教程类视频（手把手/保姆/实操/从零/教程/课程）应走
    # structured（方法论复盘），而非被 KEY_POINTS 的"视频"误判为口播要点。
    if any(kw.lower() in text for kw in TUTORIAL_SUPER_SIGNALS):
        return "structured"
    for kw in KEY_POINTS_KEYWORDS:
        if kw.lower() in text:
            return "key_points"
    for kw in OPINION_KEYWORDS:
        if kw.lower() in text:
            return "opinion"
    for kw in CASE_KEYWORDS:
        if kw.lower() in text:
            return "case"
    for kw in STRUCTURED_KEYWORDS:
        if kw.lower() in text:
            return "structured"
    return "structured"
