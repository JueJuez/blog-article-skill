# -*- coding: utf-8 -*-
"""落盘时复用 index 分类器，给新笔记追加「命名空间嵌套 hashtag」语义标签（方案 A）。

设计目标（与 _tmp/classify_v4d.py 同源，单一真源收敛于此）：
- 新抓取的总结笔记，落盘时自动带上四维度语义标签，既能进 index 快照，
  也能在 Obsidian 内按维度检索（嵌套 hashtag 原生层级检索）。
- 不回填存量 1087 篇（靠 index 表覆盖）；本模块只作用于「未来新抓的笔记」。

标签命名约定（见 _tmp/new_template_proposal.md）：
- 父领域/子领域： `#父/子`  —— 只输出含 `/` 的子领域标签，Obsidian 层级 `#父` 自动命中，
  且不会抢「分类」（category 推算跳过含 `/` 的标签）。
- 主题实体：       `#topic/...`
- 用途：           `#用途/...`
- 来源账号：       `#来源/...`
- 笔记类型：       `#类型/...`

纯函数模块，不 import 任何笔记落盘/IO 代码，避免循环依赖。
"""
import re

# ---------------------------------------------------------------------------
# 父领域映射（与 classify_v4d 同源）
# ---------------------------------------------------------------------------
INVEST_UPS = {"价投小猪仔", "Mark__Huang", "土斯土耶夫斯基", "笨笨的韭菜", "舟亦横", "青枫浦上Q",
              "DeepVan的逃生地牢", "中金点睛", "南风", "史诗级韭菜", "趋势浪子", "李自然", "作者",
              "奇衡DK-CAPITAL"}
SCYS_PARENT = {
    "副业增长": "商业/搞钱", "虚拟产品": "商业/搞钱", "出海": "商业/搞钱", "小程序": "商业/搞钱",
    "AI产品开发": "AI科技", "自媒体": "内容创作", "垂直小号": "内容创作", "AI自媒体": "内容创作",
}
PIG_SUBMAP = {"小猪仔拆公司": "公司分析", "小猪仔产业地图": "行业分析", "小猪仔聊宏观经济": "宏观/周期",
              "小猪仔聊指数": "指数/基金", "小猪仔学堂": "投资理念", "小猪仔与生活": "投资·综合"}

MONITOR_ROOT = "【监控】"
MYNOTES_ROOT = "【我的总结】"
SCYS_ZONE = "生财有术"

# scys 子文件夹 → 子领域（落盘时 folder 末级）
SCYS_SUBMAP = {"副业增长": "副业增长", "虚拟产品": "虚拟产品", "出海": "出海/独立站",
               "小程序": "小程序", "AI产品开发": "AI产品", "自媒体": "平台运营",
               "垂直小号": "平台运营", "AI自媒体": "平台运营"}

NOTE_TYPE_LABEL = {
    "structured": "结构化复盘", "key_points": "要点提炼", "interview": "访谈实录",
    "roundup": "选题合集", "reading": "读书笔记", "case": "案例拆解",
    "opinion": "观点随笔", "dissection": "创作解剖",
}


# ---------------------------------------------------------------------------
# 父/子领域
# ---------------------------------------------------------------------------
def parent_and_subfolder(parts):
    """输入文件路径或 folder 的 parts（不含文件名末尾也兼容），返回 (zone, source, subfolder, parent)。

    与 classify_v4d.parent_and_subfolder 完全一致；落盘时 folder 缺文件名，但只用 parts[0..3]。
    """
    parts = [p for p in parts if p]
    zone = parts[0] if parts else "?"
    source = parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 else "?")
    subfolder = parts[3] if len(parts) > 3 else None
    parent = None
    if zone == MONITOR_ROOT:
        if len(parts) > 1 and parts[1] == "B站":
            parent = "投资"
        elif len(parts) > 1 and parts[1] == "公众号":
            nm = parts[2] if len(parts) > 2 else "?"
            if nm in ("哥飞", "生财有术"):
                parent = "商业/搞钱"
            elif nm in INVEST_UPS:
                parent = "投资"
        elif len(parts) > 1 and parts[1] == "生财有术":
            cat = parts[2] if len(parts) > 2 else "?"
            parent = SCYS_PARENT.get(cat, "商业/搞钱")
    elif zone == SCYS_ZONE:
        cat = parts[1] if len(parts) > 1 else "?"
        parent = SCYS_PARENT.get(cat, "商业/搞钱")
    elif zone == MYNOTES_ROOT:
        nm = parts[1] if len(parts) > 1 else "?"
        if nm == "作者":
            acct = parts[2] if len(parts) > 2 else "?"
            if acct == "Datawhale":
                parent = "AI科技"
            elif acct in INVEST_UPS:
                parent = "投资"
            else:
                parent = "投资"  # 离线粗糙兜底；落盘时由 parent_from_lede 二次校验
        else:
            # 【我的总结】/<分类>：不硬归，交给 parent_from_lede 校验
            parent = None
    return zone, source, subfolder, parent


def parent_from_lede(text):
    """folder 解析不到 parent 时（如【待归类】/【我的总结】/<分类>），从导语粗判一级领域。"""
    lede = lede_from_text(text)
    s = (lede + " " + text[:2000]).lower()
    if re.search(r"股票|个股|财报|基金|估值|美联储|仓位|交易|投资|a股|港股|美股|etf|分红|护城河|止损|宏观|经济", s):
        return "投资"
    if re.search(r"副业|虚拟产品|出海|小程序|赚钱|变现|ai产品|知识付费|搞钱|商业|电商", s):
        return "商业/搞钱"
    if re.search(r"编程|代码|claude|cursor|rag|agent|大模型|llm|ai工具|api|前端|后端", s):
        return "AI科技"
    if re.search(r"小红书|视频号|公众号|抖音|涨粉|选题|账号运营|带货|自媒体|直播", s):
        return "内容创作"
    return "综合"


def lede_from_text(text):
    m = re.search(r"\*\*30秒速览\*\*[：:]\s*(.+?)(?:\n|>)", text)
    if m:
        return m.group(1).strip()
    m = re.search(r">\s*([^\n>]{12,200})", text)
    if m:
        return m.group(1).strip()
    parts = text.split("---", 2)
    body = parts[2] if len(parts) == 3 else text
    for ln in body.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and not ln.startswith("**"):
            return ln[:200]
    return ""


def subdomain_from_lede(parent, lede):
    s = lede.lower()
    if parent == "投资":
        if re.search(r"财报|营收|净利|市值|个股|公司|护城河|估值|商业模式|分红|现金流", s):
            return "公司分析"
        if re.search(r"行业|赛道|产业链|供需|产能|周期股", s):
            return "行业分析"
        if re.search(r"体系|系统|框架|仓位|交易纪律|风控|流程", s):
            return "投资系统"
        if re.search(r"哲学|心态|认知|理念|思维|人性|贪婪|恐惧", s):
            return "投资理念"
        if re.search(r"美联储|利率|通胀|宏观|经济|gdp|货币|央行|降息|加息", s):
            return "宏观/周期"
        if re.search(r"指数|etf|基金|宽基|定投", s):
            return "指数/基金"
        return "投资·综合"
    if parent == "商业/搞钱":
        if re.search(r"虚拟产品|资料|电子|模板|数字产品|知识产品|课程售卖", s):
            return "虚拟产品"
        if re.search(r"出海|独立站|跨境|亚马逊|海外|shopify|tiktok", s):
            return "出海/独立站"
        if re.search(r"小程序|微信小程序", s):
            return "小程序"
        if re.search(r"ai产品|ai应用|做产品|ai工具|智能体", s):
            return "AI产品"
        if re.search(r"副业|兼职|搞钱|赚钱项目|小众项目|变现项目", s):
            return "副业增长"
        if re.search(r"知识付费|训练营|社群|课程", s):
            return "知识付费"
        return "商业·综合"
    if parent == "AI科技":
        if re.search(r"编程|代码|claude code|脚本|开发|api|前端|后端", s):
            return "AI编程"
        if re.search(r"大模型|llm|微调|rag|embedding", s):
            return "大模型/基础"
        return "AI应用/工具"
    if parent == "内容创作":
        if re.search(r"小红书|视频号|公众号|抖音|账号|涨粉|运营", s):
            return "平台运营"
        if re.search(r"选题|文案|标题|脚本", s):
            return "选题/文案"
        if re.search(r"变现|带货|广告|收入|流量主", s):
            return "账号变现"
        if re.search(r"短视频|直播", s):
            return "短视频/直播"
        return "平台运营"
    return "综合"


def resolve_subdomain(parent, lede, parts, source, subfolder):
    if parent in (None, "待定"):
        return "综合"
    if (len(parts) > 1 and parts[0] == MONITOR_ROOT and parts[1] == "B站"
            and source == "价投小猪仔" and subfolder in PIG_SUBMAP):
        return PIG_SUBMAP[subfolder]
    if subfolder and subfolder in SCYS_SUBMAP:
        return SCYS_SUBMAP[subfolder]
    if subfolder and source == "奇衡DK-CAPITAL" and subfolder == "千刀千法":
        return "投资系统"
    return subdomain_from_lede(parent, lede)


# ---------------------------------------------------------------------------
# 用途
# ---------------------------------------------------------------------------
def note_type_from_content(text):
    m = re.search(r"^#(\w+)", text, re.M)
    return m.group(1) if m else ""


def purpose(nt, lede, body):
    p = set()
    t = (nt or "").lower()
    b = (lede + " " + body[:4000]).lower()
    if t == "roundup":
        p.add("选题方向")
    elif t == "interview":
        p.add("金句观点")
    elif t == "opinion":
        p.add("灵感启发")
    elif t in ("case", "dissection"):
        p.add("素材可用")
    elif t in ("reading", "key_points", "structured"):
        p.add("教学可用")
    else:
        p.add("素材可用")
    if t in ("case", "dissection") or len(re.findall(
            r"第[一二三四五六七八九十\d]+步|步骤\s*\d|清单|sop|流程图|工具：|模板：|公式：|方法论：|框架：", b)) >= 2:
        p.add("方法论可复用")
    if t == "case" or len(re.findall(
            r"案例[：]|复盘|实测|数据[：]|同比|环比|增长\d+%|gmv|流水|营收|收入", b)) >= 2:
        p.add("素材可用")
    if t in ("opinion", "interview") and len(re.findall(
            r"“[^”]{8,50}”|金句|观点|本质上|我认为|核心在于", b)) >= 2:
        p.add("金句观点")
    if "选题方向" not in p and len(re.findall(
            r"启发|思考|反思|感悟|认知升级|换个角度", b)) >= 3:
        p.add("灵感启发")
    if p == {"素材可用"}:
        p.add("教学可用")
    return p


# ---------------------------------------------------------------------------
# topics 词表
# ---------------------------------------------------------------------------
COMPANY_NAMES = {
    "伊利股份", "分众传媒", "宁波银行", "贵州茅台", "中国移动", "中国电信", "中国联通",
    "腾讯控股", "阿里巴巴", "美团", "拼多多", "比亚迪", "宁德时代", "隆基绿能",
    "招商银行", "中国平安", "五粮液", "泸州老窖", "海康威视", "恒瑞医药", "迈瑞医疗",
    "海天味业", "金龙鱼", "农夫山泉", "华润啤酒", "青岛啤酒", "泡泡玛特", "理想汽车",
    "蔚来", "小鹏汽车", "美的集团", "格力电器", "海尔智家", "顺丰控股", "京东", "携程",
    "百度", "网易", "小米集团", "中芯国际", "药明康德", "中国海油", "中国石油", "中国石化",
    "长江电力", "中国神华", "中国中铁", "中国铁建", "中国建筑", "中国交建", "海螺水泥",
    "万华化学", "上汽集团", "天味食品", "恒生科技", "佰仁医疗", "长实集团", "洽洽食品",
    "公牛集团", "安井食品", "国药股份", "航民股份", "重庆啤酒", "爱玛科技", "华域汽车",
    "大秦铁路", "新华保险",
}
COMPANY_RE = re.compile("|".join(sorted(COMPANY_NAMES, key=len, reverse=True)))

TOPIC_PATTERNS = {
    "MACD": r"\bMACD\b|macd",
    "K线": r"\bK线\b|k线",
    "均线系统": r"\b均线\b|MA5|MA10|MA20|MA60",
    "价格行为学": r"\b价格行为\b|Price Action|\bPA\b",
    "缠论": r"缠论",
    "波浪理论": r"波浪理论|艾略特波浪",
    "量价分析": r"\b量价\b|成交量|成交额",
    "支撑位": r"支撑位|压力位|阻力位",
    "趋势线": r"趋势线|通道线",
    "布林带": r"布林带|BOLL",
    "RSI": r"\bRSI\b",
    "KDJ": r"\bKDJ\b",
    "换手率": r"换手率",
    "市盈率": r"市盈率|市净率|市销率",
    "PE/PB": r"\bPE\b|\bPB\b",
    "ROE": r"\bROE\b",
    "DCF": r"\bDCF\b|现金流折现",
    "自由现金流": r"自由现金流",
    "护城河": r"护城河",
    "安全边际": r"安全边际",
    "能力圈": r"能力圈",
    "资产配置": r"资产配置",
    "仓位管理": r"仓位管理",
    "止损": r"止损",
    "止盈": r"止盈",
    "定投": r"定投|基金定投",
    "网格交易": r"网格交易",
    "趋势交易": r"趋势交易",
    "技术分析": r"技术分析|技术面",
    "基本面分析": r"基本面分析|基本面",
    "量化交易": r"量化交易|量化投资|多因子|回测",
    "ETF": r"\bETF\b|指数基金",
    "可转债": r"可转债",
    "期权": r"期权",
    "期货": r"期货",
    "债券": r"债券|国债|美债",
    "黄金": r"黄金",
    "原油": r"原油",
    "比特币": r"比特币|BTC",
    "以太坊": r"以太坊|ETH",
    "小红书": r"小红书",
    "抖音": r"抖音",
    "视频号": r"视频号",
    "公众号": r"公众号",
    "B站": r"\bB站\b|哔哩哔哩",
    "知乎": r"知乎",
    "微博": r"微博",
    "快手": r"快手",
    "今日头条": r"今日头条",
    "YouTube": r"YouTube|油管",
    "TikTok": r"TikTok|抖音国际版",
    "Instagram": r"Instagram|ins",
    "Facebook": r"Facebook|脸书",
    "Twitter": r"Twitter|推特",
    "淘宝": r"淘宝",
    "天猫": r"天猫",
    "京东": r"京东",
    "拼多多": r"拼多多",
    "闲鱼": r"闲鱼",
    "1688": r"1688",
    "亚马逊": r"亚马逊|Amazon",
    "Shopify": r"Shopify",
    "Temu": r"Temu",
    "SHEIN": r"SHEIN|希音",
    "速卖通": r"速卖通|AliExpress",
    "Shopee": r"Shopee",
    "Lazada": r"Lazada",
    "Etsy": r"Etsy",
    "独立站": r"独立站|自建站",
    "WordPress": r"WordPress",
    "Google": r"Google|谷歌",
    "Bing": r"Bing",
    "ChatGPT": r"ChatGPT|GPT-4|GPT-4o|GPT-5",
    "Claude": r"\bClaude\b",
    "Claude Code": r"Claude Code",
    "Cursor": r"Cursor",
    "Midjourney": r"Midjourney",
    "Stable Diffusion": r"Stable Diffusion",
    "ComfyUI": r"ComfyUI",
    "Runway": r"Runway",
    "Suno": r"Suno",
    "Notion": r"Notion",
    "Obsidian": r"Obsidian",
    "飞书": r"飞书",
    "RAG": r"\bRAG\b",
    "Agent/智能体": r"\bAgent\b|智能体",
    "LLM/大模型": r"大模型|\bLLM\b|语言模型",
    "AI编程": r"AI编程|AI 编程",
    "Sora": r"Sora",
    "FDE": r"\bFDE\b",
    "SEO": r"\bSEO\b|搜索引擎优化",
    "SEM": r"\bSEM\b",
    "出海": r"出海|海外市场",
    "外贸": r"外贸|进出口",
    "虚拟产品": r"虚拟产品|虚拟资料|数字产品",
    "知识付费": r"知识付费|付费社群|训练营",
    "私域": r"私域",
    "个人IP": r"个人IP|个人品牌",
    "SaaS": r"SaaS|微SaaS",
    "广告投放": r"广告投放|信息流广告|Google Ads|Facebook Ads",
    "增长黑客": r"增长黑客",
    "MCP": r"\bMCP\b",
    "微信小程序": r"微信小程序",
    "英语": r"英语|学英语|英语口语",
    "日语": r"日语|学日语",
    "雅思": r"雅思",
    "托福": r"托福",
    "MBTI": r"\bMBTI\b",
    "依恋类型": r"依恋类型|依恋理论|依恋风格",
    "大五人格": r"大五人格|五因素模型",
    "九型人格": r"九型人格",
    "人格分析": r"人格分析|性格分析|性格测试",
    "心理学": r"心理学",
    "亲密关系": r"亲密关系|婚姻|恋爱",
    "原生家庭": r"原生家庭",
    "情绪管理": r"情绪管理|情绪价值",
}
SOFT_TOPICS = {
    "价值投资": r"价值投资|价投",
    "自媒体": r"自媒体",
    "副业": r"副业|兼职",
    "个人品牌": r"个人品牌|个人IP",
}

topic_compiled = {k: re.compile(v, re.I) for k, v in TOPIC_PATTERNS.items()}
soft_compiled = {k: re.compile(v, re.I) for k, v in SOFT_TOPICS.items()}
STOCK_CODE_RE = re.compile(r"\b(\d{6})\b")

TAG_TOOL_SET = {"小红书", "抖音", "视频号", "公众号", "B站", "知乎", "微博", "快手", "今日头条", "YouTube",
                "TikTok", "Instagram", "Facebook", "Twitter", "淘宝", "天猫", "京东", "拼多多", "闲鱼",
                "亚马逊", "Shopify", "Temu", "SHEIN", "独立站", "ChatGPT", "Claude", "Cursor", "Midjourney",
                "Stable Diffusion", "Notion", "Obsidian", "飞书", "RAG", "Agent", "FDE", "SEO", "MBTI",
                "出海", "私域", "个人IP", "自媒体", "副业", "虚拟产品"}


def title_from_text(text):
    m = re.search(r"^title:\s*\"?(.+?)\"?\s*$", text, re.M)
    if m:
        return m.group(1).strip()
    m = re.search(r"^#\s*(.+)", text, re.M)
    if m:
        return m.group(1).strip()
    return ""


def extract_hashtags(text):
    tags = set()
    for m in re.finditer(r"#([\u4e00-\u9fffA-Za-z0-9_\-\/\.]{2,20})", text):
        t = m.group(1)
        if t in ("文章总结", "转载", "更早", "一句话核心结论", "30秒速览", "核心公式", "骨架"):
            continue
        tags.add(t)
    return tags


def extract_topics(fn, text):
    """从 title/lede/#标签 抽取主题实体（≤8 个）。fn 在落盘场景可传空串。"""
    title = title_from_text(text) if not fn else title_from_text_and_path(fn, text)
    lede = lede_from_text(text)
    tags = extract_hashtags(text)
    core_text = title + "\n" + lede + "\n" + " ".join(tags)

    topics = set()
    for m in COMPANY_RE.finditer(core_text):
        topics.add(m.group(0))
    for name, rx in topic_compiled.items():
        if rx.search(core_text):
            topics.add(name)
    for tag in tags:
        if tag in TAG_TOOL_SET:
            topics.add(tag)
        elif STOCK_CODE_RE.fullmatch(tag):
            topics.add(tag)
    for name, rx in soft_compiled.items():
        if rx.search(title + " " + lede):
            topics.add(name)

    merged = []
    for t in sorted(topics, key=lambda x: -len(x)):
        if any(t != other and t in other for other in merged):
            continue
        merged.append(t)
    return merged[:8]


def title_from_text_and_path(fn, text):
    title = title_from_text(text)
    if title:
        return title
    base = re.sub(r"-\d{8}-\d{6}", "", fn[:-3]) if fn.endswith(".md") else fn
    base = re.sub(r"^\d{8}_", "", base)
    base = re.sub(r"^第\d+集_", "", base)
    return base


# ---------------------------------------------------------------------------
# 对外单一入口
# ---------------------------------------------------------------------------
def infer_semantic_tags(content, folder="", author="", note_type="", url="", source_account=""):
    """返回新笔记应追加的命名空间语义标签列表（不含 # 前缀，不含 #文章总结/#转载 等遗留标签）。

    维度：父/子领域、主题实体、用途、来源账号、笔记类型。
    返回值形如 ``投资/公司分析``、``topic/伊利股份``、``用途/教学可用``、``来源/价投小猪仔``、
    ``类型/结构化复盘``——**不带 # 前缀**，由落盘模板 format_note_with_prompt 统一加 #，
    最终笔记里显示为 #投资/公司分析 等。这样：
    - Obsidian 层级标签 #投资 自动命中 #投资/公司分析；
    - category 推算跳过含 / 的标签，命名空间标签绝不抢「分类」（文件夹路由）。
    """
    parts = [p for p in (folder or "").split("/") if p]
    zone, source, subfolder, parent = parent_and_subfolder(parts)
    lede = lede_from_text(content)

    # parent 二次校验：【我的总结】/<分类> / 待定 / 通用兜底时，用导语信号覆盖
    if parent in (None, "待定") or (zone == MYNOTES_ROOT and len(parts) > 1 and parts[1] != "作者"):
        cand = parent_from_lede(content)
        if cand != "综合":
            parent = cand

    sub = resolve_subdomain(parent, lede, parts, source, subfolder)

    tags = []
    # 领域标签（仅含 / 的子领域）。注意：返回**不带 # 前缀**——落盘模板 format_note_with_prompt
    # 会统一给每个 tag 加 #，这里若带 # 会变成 ##xxx。最终笔记里显示为 #投资/公司分析。
    if parent and parent != "综合" and sub:
        root = parent.split("/")[0]
        encoded = sub.replace("·综合", "").replace(parent, "").strip("·/ ").strip()
        if not encoded:
            encoded = "综合"
        tags.append(f"{root}/{encoded}")

    # 主题实体
    for t in extract_topics("", content):
        tags.append("topic/" + t.replace("/", "·"))

    # 用途
    nt = (note_type or note_type_from_content(content)).lower()
    for p in purpose(nt, lede, content):
        tags.append("用途/" + p)

    # 来源账号
    src = (author or source_account or "").strip()
    if src and src not in ("未知", "匿名"):
        tags.append("来源/" + src)

    # 笔记类型
    if nt:
        label = NOTE_TYPE_LABEL.get(nt)
        if label:
            tags.append("类型/" + label)

    # 去重保序
    seen = set()
    out = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


if __name__ == "__main__":
    # 简单自测
    sample = (
        "# 拆解伊利股份：乳制品龙头的护城河\n\n"
        "**30秒速览**：伊利股份作为乳制品龙头，凭借品牌与渠道护城河维持高 ROE，"
        "本文从财报角度分析其估值与分红。\n\n"
        "## 一、公司概况\n伊利股份..."
    )
    print(infer_semantic_tags(sample, folder="【监控】/B站/价投小猪仔/小猪仔拆公司",
                              author="价投小猪仔", note_type="structured"))
