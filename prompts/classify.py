"""基于内容主线与组合证据的确定性选型；字符串接口保持兼容。

专用结构证据不足时保守回退；理由由 classify_note_type_with_evidence 返回。
规则只是可复现的启发式，不代替语义适配复核。
"""
import re

CLASSIFIER_VERSION = "20261015-evidence-v1"

# 候选信号词：需按下方主线证据组合，不能孤词决定；原分类为「要点提炼」的信号词（公开课/讲座/演讲/播客等口播类）
# 注意："访谈"已移出（见 INTERVIEW_KEYWORDS），避免访谈被误判为口播要点；
# "视频"也已移除--任何 B站链接都是视频，URL 本身已说明载体，不该当类型信号
# （否则字幕里一句"这个视频"就会把盘点/访谈抢成口播要点，见 2026-07-21 复盘）。
# "分享"已移除（2026-08-21）：过于泛化，scys 正文高频出现，导致大量误判。
KEY_POINTS_KEYWORDS = [
    "公开课", "讲座", "演讲", "分享会", "talk", "ted", "播客",
    "直播", "圆桌", "峰会", "论坛", "keynote", "口播", "发言技巧",
]

# 候选信号词：需按下方主线证据组合，不能孤词决定；原分类为「观点卡」的信号词（评论/立场/随笔类）
OPINION_KEYWORDS = [
    "评论", "观点", "立场", "辩论", "犀利", "我反对", "我支持",
    "为什么我觉得", "说几句", "杂谈", "站队",
]

# 候选信号词：需按下方主线证据组合，不能孤词决定；原分类为「案例拆解」的信号词（案例/复盘/产品拆解类）
CASE_KEYWORDS = [
    "案例", "商业故事", "产品拆解", "实战记录", "商业案例", "复盘案例",
    "失败故事", "成功故事", "踩坑", "增长故事",
]

# 候选信号词：需按下方主线证据组合，不能孤词决定；原分类为「结构化复盘」的信号词（教程/方法论类）
STRUCTURED_KEYWORDS = [
    "教程", "课程", "指南", "方法论", "实操", "从零", "如何", "干货",
    "复盘", "拆解", "保姆", "手把手", "入门", "进阶",
]

# 创作解剖（dissection）触发需「领域词 ∩ 动作词」共现（P0-1 修复，2026-08-27）：
# 纯领域词（带货/私域/起号/涨粉…）单独出现不触发——否则「私域运营方法论」会被
# 抢成 dissection；必须与动作词（拆解/复盘/打法/钩子…）共现才判 dissection。
# 定位：内容创作域的拆解/复盘——case 总结「发生了什么」，dissection 额外提炼
# 「可复用结构模具」（标题公式/钩子/节奏/CTA），模板移植自 ppt-master 的
# note_dissection_sop.md。排在 opinion 之后、case 之前：抢走「带货复盘」「爆款拆解」
# 这类创作域复盘，但不抢真观点文与普通商业案例。
# 词表纪律（见 2026-08-21 教训）：只收内容创作域专有词，禁泛化词——
# 「引流」有医学引流术义、「变现」「粉丝」「选题」过于泛化，均不收。
DISSECTION_DOMAIN = [
    "带货", "私域", "起号", "账号运营",
    "内容创作", "小红书运营", "抖音运营", "公众号运营", "自媒体运营", "涨粉",
]
DISSECTION_ACTION = [
    "拆解", "复盘", "打法", "公式", "钩子", "节奏", "CTA", "选题",
    "起号逻辑", "运营sop", "创作sop", "拆爆款", "找对标",
]
# 本身即「领域+动作」合一，单独出现即触发 dissection（避免「爆款拆解」因拆词而漏判）。
DISSECTION_COMBINED = [
    "爆款拆解", "爆款笔记", "爆款视频", "爆款文案", "爆款标题",
]

# 教学/教程类超信号：命中即判 structured，优先级高于 KEY_POINTS 的"视频"匹配。
# 解决「教学视频」被误判为口播要点（key_points）的问题（见 DECISION-20260721-note-quality）。
TUTORIAL_SUPER_SIGNALS = ["手把手", "保姆", "实操", "从零", "教程", "课程", "step by step"]

# 候选信号词：需按下方主线证据组合，不能孤词决定；原分类为「访谈 / 对话」的信号词（专访/对谈/播客访谈/Q&A）
# 注意：已从 KEY_POINTS 移出"访谈"，避免访谈被误判为口播要点（见 2026-07-21 三类新模板）。
# "问答"已移除（2026-08-21）：scys 页面 boilerplate 导航含「AI问答」导致全部文章
# 命中误判；真访谈靠「访谈/对谈/专访/对话」标题关键词 + 内容级兜底。
INTERVIEW_KEYWORDS = [
    "访谈", "对话", "对谈", "专访", "q&a", "qa", "深度对话",
]

def _content_looks_interview(content: str) -> bool:
    """探针必须伴随可识别的问答双方；单人模拟与二人称独白不足以证明访谈。"""
    labels = re.findall(r"(?:^|\n)\s*([^：:\n]{1,16})[：:]", content)
    speakers = set(labels) - {"标题", "作者", "来源", "日期"}
    host = any(x in {"主持人", "记者", "问", "Q", "q"} for x in speakers)
    return host and len(speakers) >= 2 and len(labels) >= 3


def _has_dissection(text: str) -> bool:
    """dissection 触发判定：领域词与动作词共现（或命中「领域+动作」合一词）。

    P0-1 修复（2026-08-27）：纯领域词（如「私域」「带货」）单独出现不触发，
    必须搭配动作词（拆解/复盘/打法…），否则 fall through 到后续类别。
    """
    dom = any(kw.lower() in text for kw in DISSECTION_DOMAIN) or any(
        kw.lower() in text for kw in DISSECTION_COMBINED
    )
    act = any(kw.lower() in text for kw in DISSECTION_ACTION) or any(
        kw.lower() in text for kw in DISSECTION_COMBINED
    )
    return dom and act

# 候选信号词：需按下方主线证据组合，不能孤词决定；原分类为「盘点 / 横评」的信号词（N个最佳X/横向测评/工具对比/选购）
ROUNDUP_KEYWORDS = [
    "盘点", "横评", "测评", "评测", "对比评测", "横向评测",
    "选购指南", "哪个值得", "值得买", "推荐清单",
    "榜单", "红黑榜", "种草", "闭眼入", "抄作业", "排行", "榜",
]

# 候选信号词：需按下方主线证据组合，不能孤词决定；原分类为「读书 / 书摘」的信号词（书评/拆书/读后感/读书笔记）
READING_KEYWORDS = [
    "读书", "书摘", "书评", "读后感", "拆书", "读书笔记",
    "荐书", "书单", "读这本书", "读完",
]


def classify_note_type_with_evidence(title: str = "", content: str = "") -> dict:
    """返回实际类型、可解释理由、规则版本和保守回退状态。"""
    title = (title or "").lower()
    body = (content or "").lower()

    def result(kind, reason):
        return {"note_type": kind, "reason": reason, "classifier_version": CLASSIFIER_VERSION,
                "needs_review": kind == "general"}

    def has(words, value):
        return any(w.lower() in value for w in words)

    # 原著单集仍可以书为中心；“第N集”本身不能推断它是课程。
    book_body = sum(body.count(w) for w in ["本书", "这本书", "书中", "书作者", "章节", "原著"]) >= 2
    original_book = has(["原著精讲", "原著解读", "原著讲解"], title) and book_body
    if original_book:
        return result("reading", "标题原著解说与正文围绕书本的证据共现，范围限本集")
    # 主线标题优于正文局部关键词；章节名/课程名的书名号不是书评证据。
    lesson = bool(re.search(r"第[一二三四五六七八九十百0-9]+[课讲集]|[0-9]+[.、].*篇", title))
    formulas = bool(re.search(r"[一二三四五六七八九十0-9]+(?:大|个)?公式", title))
    if has(TUTORIAL_SUPER_SIGNALS, title) or lesson or formulas:
        return result("structured", "标题明确教学/课程主线")

    simulated = bool(re.search(r"模拟(?:领导)?(?:对话|情景|场景)|假设.{0,12}(?:领导|对话)", title + body[:1200]))
    interview_title = bool(re.search(r"(?:访谈|专访|对谈|对话)[：:、\s]*[\w\u4e00-\u9fff]{2,}|[\w\u4e00-\u9fff]{2,}(?:对谈|对话)[：:]", title))
    if not simulated and (interview_title or _content_looks_interview(body)):
        return result("interview", "标题有访谈对象/主题或正文有主持人与回答方的多轮发言")

    # 横评要有多对象与比较任务的组合；不是孤立“抄作业/榜”。
    multi = bool(re.search(r"[两三四五六七八九十0-9]+\s*(?:款|个|种|大)|甲.*乙|a\s*(?:vs|与|和)\s*b", title))
    compare = has(["横评", "测评", "评测", "盘点", "对比", "榜", "清单", "最佳"], title)
    if (multi and compare) or ("试遍" in title and "榜" in title) or bool(re.search(r"(?:年度|效率|工具|app|软件).*(?:红黑榜|推荐清单)", title)):
        return result("roundup", "标题多对象与比较/盘点任务共现（条件可比性仍须正文复核）")

    book_title = bool(re.search(r"《[^》]+》", title))
    book_task = has(["书评", "书摘", "读书笔记", "读后感", "拆书", "荐书", "书单"], title)
    if book_task or (book_title and (book_body or bool(re.search(r"读《.+》后", title)))):
        return result("reading", "标题明确读书任务，或书名与围绕书本的论述共现")

    if has(KEY_POINTS_KEYWORDS, title):
        return result("key_points", "标题明确演讲/口播场景")
    if has(OPINION_KEYWORDS, title.replace("评论区", "")):
        return result("opinion", "标题明确立场/评论主线")
    if _has_dissection(title):
        return result("dissection", "标题创作领域与拆解动作共现")
    if has(CASE_KEYWORDS, title) or ("复盘" in title and has(["失败", "项目", "产品", "小程序"], title)):
        return result("case", "标题具体案例/项目复盘主线")
    if has(STRUCTURED_KEYWORDS + ["方法"], title):
        return result("structured", "标题明确方法/流程主线")

    # 无明确标题时才使用正文组合结构；局部提及不能覆盖明确主线。
    experience = body[:3500]
    narrative = bool(re.search(r"复盘|完整经历|真实案例|我是真的在干", experience))
    if (narrative and re.search(r"(?:我|我们).{0,30}(?:做|用|开始|尝试|在干)", experience)
            and re.search(r"(?:个月|收入|流水|营收|付费|售卖|卖到|售后|失败|亏损)", experience)):
        return result("case", "正文自述复盘、行动和结果/期间组合，保留完整案例主线")
    if not simulated and _has_dissection(body[:800]):
        return result("dissection", "正文创作领域与拆解动作共现")
    if has(TUTORIAL_SUPER_SIGNALS, body[:800]):
        return result("structured", "正文有教学任务")
    return result("general", "缺少可靠专用结构证据，保守通用整理，待适配复核")


def classify_note_type(title: str = "", content: str = "") -> str:
    return classify_note_type_with_evidence(title, content)["note_type"]
