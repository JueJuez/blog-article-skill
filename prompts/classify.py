"""按内容自动判定笔记类型

优先级（命中即返回，前者优先）：
教学超信号(structured) > 访谈/对话(interview) > 要点提炼(key_points) >
盘点/横评(roundup) > 读书/书摘(reading) > 观点卡(opinion) > 创作解剖(dissection) >
案例拆解(case) > 结构化复盘(structured, 兜底)。
都不命中时默认 structured（最完整、最安全的兜底形态）。

补充：
- 访谈除标题关键词（访谈/对谈/专访/Q&A）外，支持「内容级兜底」：标题无 cue 时
  （如「95后女老板Judy」式创业对谈），用正文前 2500 字判别「主持人向特定嘉宾的
  人生/状态探针（你当时/你后来/你是怎么/你创业…）且观众独白口吻不占主导」。
- 「视频」已从 KEY_POINTS 移除：URL 本身即说明载体，不该当类型信号
  （否则字幕里一句「这个视频」会把盘点/访谈抢成口播要点）。
- 「问答」已从 INTERVIEW_KEYWORDS 移除（2026-08-21）：scys 页面 boilerplate 导航
  含「AI问答」，导致全部文章命中「问答」被误判 interview；真访谈靠「访谈/对谈/
  专访/对话」标题关键词 + 内容级兜底。
- 「分享」已从 KEY_POINTS_KEYWORDS 移除（2026-08-21）：过于泛化，scys 正文高频
  出现（185/309 篇命中），导致大量 structured 被误判 key_points；真口播靠
  「公开课/讲座/演讲/播客/直播」等更精确的信号。
"""

# 命中即归为「要点提炼」的信号词（公开课/讲座/演讲/播客等口播类）
# 注意："访谈"已移出（见 INTERVIEW_KEYWORDS），避免访谈被误判为口播要点；
# "视频"也已移除--任何 B站链接都是视频，URL 本身已说明载体，不该当类型信号
# （否则字幕里一句"这个视频"就会把盘点/访谈抢成口播要点，见 2026-07-21 复盘）。
# "分享"已移除（2026-08-21）：过于泛化，scys 正文高频出现，导致大量误判。
KEY_POINTS_KEYWORDS = [
    "公开课", "讲座", "演讲", "分享会", "talk", "ted", "播客",
    "直播", "圆桌", "峰会", "论坛", "keynote", "口播",
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

# 命中即归为「创作解剖」的信号词（爆款拆解/带货复盘/账号运营类，2026-08-26 新增）
# 定位：内容创作域的拆解/复盘——case 总结「发生了什么」，dissection 额外提炼
# 「可复用结构模具」（标题公式/钩子/节奏/CTA），模板移植自 ppt-master 的
# note_dissection_sop.md。排在 opinion 之后、case 之前：抢走「带货复盘」「爆款拆解」
# 这类创作域复盘，但不抢真观点文与普通商业案例。
# 词表纪律（见 2026-08-21 教训）：只收内容创作域专有词，禁泛化词——
# 「引流」有医学引流术义、「变现」「粉丝」「选题」过于泛化，均不收。
DISSECTION_KEYWORDS = [
    "爆款拆解", "爆款笔记", "爆款视频", "爆款文案", "爆款标题",
    "涨粉", "起号", "带货", "账号运营", "私域",
    "内容创作", "小红书运营", "抖音运营", "公众号运营", "自媒体运营",
]

# 教学/教程类超信号：命中即判 structured，优先级高于 KEY_POINTS 的"视频"匹配。
# 解决「教学视频」被误判为口播要点（key_points）的问题（见 DECISION-20260721-note-quality）。
TUTORIAL_SUPER_SIGNALS = ["手把手", "保姆", "实操", "从零", "教程", "课程", "step by step"]

# 命中即归为「访谈 / 对话」的信号词（专访/对谈/播客访谈/Q&A）
# 注意：已从 KEY_POINTS 移出"访谈"，避免访谈被误判为口播要点（见 2026-07-21 三类新模板）。
# "问答"已移除（2026-08-21）：scys 页面 boilerplate 导航含「AI问答」导致全部文章
# 命中误判；真访谈靠「访谈/对谈/专访/对话」标题关键词 + 内容级兜底。
INTERVIEW_KEYWORDS = [
    "访谈", "对话", "对谈", "专访", "q&a", "qa", "深度对话",
]

# 内容级访谈识别（标题无 cue 时的兜底，如「95后女老板Judy」式创业对谈）。
# 原理：访谈 = 主持人向「特定嘉宾」反复用人生/状态探针（你当时/你后来/你是怎么/你创业…），
# 且几乎没有「观众独白口吻」（你有没有/你是否/你可以…——读书/教程独白的特征）。
# 合并字幕通常无说话人标签、无问号，故用「第二人称 + 探针词」做内容级判别，并加 aud==0 护栏防独白误判。
INTERVIEW_HOST_PROBES = [
    "你当时", "你后来", "你最早", "你第一次", "你之前", "你是怎么", "你是如何",
    "你为什么", "你怎么看", "你怎么想", "你选择", "你决定", "你开始", "你遇到",
    "你家人", "你父母", "你孩子", "你伴侣", "你老公", "你老婆", "你男友", "你女友",
    "你休学", "你裸辞", "你创业", "你打工", "你上班", "你跳槽", "你卖掉", "你卖掉了",
    "你掌舵", "你人生", "你好像", "你觉得呢", "你说", "你眼中", "你眼中的",
]
INTERVIEW_AUDIENCE = [
    "你有没有", "你是否", "你会不会", "你不妨", "你可以试着", "你可能会",
    "大家看", "我们不妨", "你可以",
]

def _content_looks_interview(content: str) -> bool:
    """内容级访谈识别：主持人向特定嘉宾的人生探针，且无观众独白口吻。"""
    if not content:
        return False
    t = content[:2500]
    host = sum(t.count(p) for p in INTERVIEW_HOST_PROBES)
    aud = sum(t.count(p) for p in INTERVIEW_AUDIENCE)
    # 强信号：主持人向特定嘉宾的人生/状态探针（≥2 次），且观众独白口吻不占主导
    # （aud<=host+1 允许少量噪声，同时排除读书/教程式「你有没有/你是否」独白——它们 host≈0）
    if host >= 2 and aud <= host + 1:
        return True
    if host >= 4:
        return True
    return False

# 命中即归为「盘点 / 横评」的信号词（N个最佳X/横向测评/工具对比/选购）
ROUNDUP_KEYWORDS = [
    "盘点", "横评", "测评", "评测", "对比评测", "横向评测",
    "选购指南", "哪个值得", "值得买", "推荐清单",
    "榜单", "红黑榜", "种草", "闭眼入", "抄作业", "排行", "榜",
]

# 命中即归为「读书 / 书摘」的信号词（书评/拆书/读后感/读书笔记）
READING_KEYWORDS = [
    "读书", "书摘", "书评", "读后感", "拆书", "读书笔记",
    "荐书", "书单", "读这本书", "读完",
]


def classify_note_type(title: str = "", content: str = "") -> str:
    """根据标题与正文开头，自动判定笔记类型。

    Args:
        title: 文章/视频标题
        content: 正文或字幕（只需前若干字符即可）

    Returns:
        "structured" / "key_points" / "opinion" / "case" /
        "interview" / "roundup" / "reading" / "dissection"
    """
    text = f"{title}\n{(content or '')[:800]}".lower()
    # 「评论区」是账号运营内容的高频实体（如「评论区运营」），不是观点信号；
    # 剔除后再匹配 OPINION，避免带货/涨粉复盘被「评论」二字误抢成 opinion
    #（同类教训见 2026-08-21 的「问答」「分享」清理）。
    text_no_comment_section = text.replace("评论区", "")
    # 教学超信号优先：教学/教程类视频（手把手/保姆/实操/从零/教程/课程）应走
    # structured（方法论复盘），而非被 KEY_POINTS 的"视频"误判为口播要点。
    if any(kw.lower() in text for kw in TUTORIAL_SUPER_SIGNALS):
        return "structured"
    # 访谈/对话：双（多）方问答节奏，独立成类，优先于 KEY_POINTS 的"视频/播客"。
    for kw in INTERVIEW_KEYWORDS:
        if kw.lower() in text:
            return "interview"
    # 内容级访谈兜底：标题无 cue（如「95后女老板Judy」式创业对谈），
    # 用更多正文做问答节奏判别（主持人向特定嘉宾的人生探针，且无观众独白口吻）。
    if _content_looks_interview(f"{title}\n{(content or '')[:2500]}"):
        return "interview"
    for kw in KEY_POINTS_KEYWORDS:
        if kw.lower() in text:
            return "key_points"
    # 盘点/横评：多对象并列比较，独立成类。
    for kw in ROUNDUP_KEYWORDS:
        if kw.lower() in text:
            return "roundup"
    # 读书/书摘：围绕一本书，独立成类（书名号《》强信号，如「《当下的力量》」）。
    if "《" in (title or "") and "》" in (title or ""):
        return "reading"
    for kw in READING_KEYWORDS:
        if kw.lower() in text:
            return "reading"
    for kw in OPINION_KEYWORDS:
        if kw.lower() in text_no_comment_section:
            return "opinion"
    # 创作解剖：内容创作域的拆解/复盘（带货复盘、爆款拆解、涨粉起号等）。
    for kw in DISSECTION_KEYWORDS:
        if kw.lower() in text:
            return "dissection"
    for kw in CASE_KEYWORDS:
        if kw.lower() in text:
            return "case"
    for kw in STRUCTURED_KEYWORDS:
        if kw.lower() in text:
            return "structured"
    return "structured"
