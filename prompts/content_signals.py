"""prompts/content_signals.py — 落盘门禁的「内容判据」（零 AI · 只读 · 可单测）

设计依据：`docs/decisions/DECISION-20260915-content-first-gate.md`
用户硬约束：**绝不用绝对值字数区间**（会导致「为压缩而压缩」、语义破碎、丢原意）；
**内容完整第一**；字数只做兜底与失控保护，不承担质量判定。

本模块提供四类**内容判据**，替代原来的「笔记字数 ÷ 源长」比例带：

  ① anchors_recall     硬锚点召回 —— 源里的数字/术语/公司名/代码，笔记丢了多少
  ② form_signals       破碎形态     —— A 超长句 / C 逗号密度 / D 相邻段高相似
  ③ structure_missing  结构缺失     —— 显式 SECTION_CONTRACTS 硬必备模块有没有缺
  ④ verbatim_ratio     照搬重合     —— 笔记 20 字滑窗片段出现在源里的占比

被 `prompts/verifier.py`（落盘门禁）与 `scripts/audit_gate_signals.py`（离线审计）共用，
单一真源，避免两处实现漂移。

## 被数据推翻、已废弃的判据（勿再引入）

- ❌ **「括号」类信号**（2026-09-15 离线审计实证）：模板本身要求「专业名词首现必须附
  大白话解释」，括号是规定动作，不是缺陷。「单段括号≥3 处」命中 39.8%、「括号文字占
  段落比≥30%」命中 63.2%，两版均为假阳性。
- ❌ **字数比例带**（`源长×0.25~0.45`）：与源质量无稳定因果。实测同为语音识别稿，
  长源批次比值 0.20（报「偏短」）、短源批次 1.93（报「超参考值」），近 10 倍跨度；
  同一 structured 模板下笔记长度差 2.8 倍。

## 精度约束（踩过的坑）

- 数字锚点必须剔引流话术（666/888/999…）——那是 UP 让观众留言领资料，不是内容事实。
- 拉丁术语锚点**只取已知白名单**：ASR 伪术语（rig=RAG、cash=cache、jason=JSON）
  无法机械与真术语区分，用「源内复现次数」也挡不住。
- 词典锚点**必须用其正则去匹配笔记**，不能用标签字面量（`LLM/大模型` 这种标签
  笔记里永远不会这么写，字面匹配必然假阳性）；且源内复现 ≥2 次才算本文核心。
- 锚点判据**有适用性门槛**：口语观点型内容（恋爱/职场）几乎没有硬事实，源锚点中位仅
  2~3 个。源锚点数 < `MIN_ANCHORS` 时该判据不适用，不得报缺。
"""
from __future__ import annotations

import collections
import difflib
import re

# ---------------------------------------------------------------- 阈值（集中声明，便于单测与调参）

MIN_ANCHORS = 4            # 源锚点少于该值 → 锚点判据不适用（宁可不判，不可乱报）
RECALL_THRESHOLD = 0.60    # 锚点召回率低于该值 → 疑遗漏核心事实
LONG_SENTENCE = 140        # 单句去空白超过该字数 → 疑压碎（把多件事塞进一句）
COMMA_RATIO = 0.085        # 逗号数/总字数 超过该比例 → 疑长句堆砌
ADJACENT_SIM = 0.85        # 相邻段落相似度超过该值 → 疑同义反复/注水
VERBATIM_N = 20            # 照搬检测的滑窗长度
VERBATIM_THRESHOLD = 0.20  # 滑窗重合占比超过该值 → 疑逐字搬运未合成

# ---------------------------------------------------------------- 归一化

_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]+')
FRONT_HEADER_RE = re.compile(r"^>.*?\n---\n", re.S)
TAGLINE_RE = re.compile(r"^\s*#[^\n]*\n")
SRC_LINK_RE = re.compile(r"^.*来源链接.*$\n?", re.M)


def sanitize_title(t: str) -> str:
    """文件名安全化（与落盘侧一致，用于把标题回查 raw 文件）。"""
    return _ILLEGAL.sub("_", (t or "").strip())[:80]


def strip_source(raw: str) -> str:
    """去掉 raw 文件的『> 原始文章内容（自动暂存）』头部。"""
    return FRONT_HEADER_RE.sub("", raw, count=1).strip()


# 抓取注入的 API/SDK payload（scys 的 Coze 响应、图片元数据、笔记 ID）不是正文事实。
# 2026-10-19 实测：H05 源里嵌了整段 Coze JSON，锚点因此多出 1440/1920/3370、logid、
# note_id、execute_id；一篇 15/15 的稿被 58% 召回误拦（过滤后 82%）。
_PAYLOAD_MARK = re.compile(
    r'"(?:logid|execute_id|note_id|xsec_token|image_list|debug_url|interact_info|'
    r'user_id|model_type|cover|avatar|nickname)"\s*:')
_URL_RE = re.compile(r"https?://\S+")
_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
# scys 页面 chrome：评论区输入框占位符「0 / 1200 发布」被拆成多行抓进正文（2026-10-25 实测
# 4 篇源同现，纯数字 1200 因此进锚点分母误拦好稿）。只删「数字/数字/发布」紧邻序列，不删正文数字。
_COMMENT_BOX_RE = re.compile(r"^\d+\n/\n\d+\n发布$", re.M)


def strip_source_payload(text: str) -> str:
    """剥离抓取注入的 JSON/媒体元数据，保留正文与代码示例。

    只删「有 payload 特征」的行（Coze 响应字段、超长单行 JSON），不盲删所有 JSON——
    教程里的配置示例可能是正文内容。也**不**按「12 位以上数字」删正文：真实金额
    可能写成 4793700000000（无千分位），误删会制造新的漏锚点。
    """
    text = _COMMENT_BOX_RE.sub("", text)
    out = []
    for line in (text or "").splitlines():
        if _PAYLOAD_MARK.search(line):
            continue
        if len(line) > 2000 and ('{"' in line or '\\"' in line):
            continue
        line = _URL_RE.sub(" ", line)
        line = _MD_IMG_RE.sub(" ", line)
        out.append(line)
    return "\n".join(out)


def strip_note(note: str) -> str:
    """去掉系统权威追加的标签行与来源链接行，只留模型正文。"""
    body = TAGLINE_RE.sub("", note, count=1)
    return SRC_LINK_RE.sub("", body)


def nows(s: str) -> str:
    return re.sub(r"\s", "", s or "")


def count_words(s: str) -> int:
    return len(nows(s))


# ---------------------------------------------------------------- ① 硬锚点

NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_UNITS = ("%", "％", "亿美元", "美元", "美金", "万亿", "亿", "万元", "万", "千", "百", "元", "倍")
# 引流话术数字（UP 让观众「留言 666 领资料」），不是内容事实
_JUNK_NUMS = {"666", "6666", "888", "8888", "999", "9999", "123", "1234", "520", "111", "222", "333"}
LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.+#-]{1,}")
_EN_STOP = {
    # 英语功能词
    "the", "and", "for", "with", "you", "are", "but", "not", "all", "can", "has", "was", "our",
    "out", "one", "new", "get", "how", "why", "what", "when", "this", "that", "from", "they",
    "have", "will", "your", "his", "her", "its", "about", "into", "over", "more", "some", "than",
    "then", "them", "were", "been", "said", "does", "did", "just", "only", "also", "very", "much",
    "most", "such", "even", "back", "still", "there", "here", "where", "which", "who", "whom",
    # 口语/寒暄/泛词（转录稿高频，但非术语）
    "ok", "okay", "yes", "no", "yeah", "nice", "good", "great", "hello", "hi", "bye", "thanks",
    "pls", "thx", "aa", "cp", "dd", "emmm", "oh", "wow", "haha", "lol", "app", "web", "pro",
    "max", "min", "top", "api", "url", "id", "ai",
}
# 泛概念标签：UP 说的主题词，笔记用同义表达不算丢——不算硬锚点
_SOFT_LABELS = {
    "心理学", "依恋类型", "大五人格", "九型人格", "人格分析", "亲密关系", "原生家庭", "情绪管理",
    "出海", "外贸", "虚拟产品", "知识付费", "私域", "个人IP", "SaaS", "广告投放", "增长黑客",
    "英语", "日语", "雅思", "托福", "MBTI", "技术分析", "基本面分析", "资产配置", "仓位管理",
    "出海/独立站", "增长",
}


def _latin_whitelist() -> set:
    """已知工具/术语标签（纯拉丁），出现即算锚点。"""
    from shared import note_classify as nc
    wl = set()
    for label in list(getattr(nc, "TOPIC_PATTERNS", {})):
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+#/-]*", label):
            wl.add(label.lower())
    for t in getattr(nc, "TAG_TOOL_SET", set()):
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+#/-]*", t):
            wl.add(t.lower())
    return wl


def _numeric_anchors(text: str) -> set:
    from .evidence import prose_lines_for_numbers
    text = '\n'.join(line for _, line in prose_lines_for_numbers(text))
    out = set()
    for m in NUM_RE.finditer(text):
        digits = m.group(0)
        if digits in _JUNK_NUMS:
            continue
        tail = text[m.end():m.end() + 4]
        unit = ""
        for u in _UNITS:
            if tail.startswith(u):
                unit = u
                break
        try:
            val = float(digits)
        except ValueError:
            continue
        is_year = "." not in digits and len(digits) == 4 and 1900 <= val <= 2100
        if unit or val >= 1000 or is_year:
            out.add(digits)
    return out


def _anchor_specs(source: str) -> list:
    """构造锚点检查项 [(kind, display, matcher)]，matcher(note) -> 笔记是否保留。

    ⚠️ 先在**去掉抓取 payload** 的源上构造（2026-10-19 P0）：JSON 里的图片尺寸/ID
    不是内容事实，留在分母里会把好稿误判成漏内容。
    """
    from shared import note_classify as nc
    source = strip_source_payload(source)
    specs = []

    for d in sorted(_numeric_anchors(source)):
        specs.append(("数", d, lambda note, d=d: d in nows(note)))

    wl = _latin_whitelist()
    for w in sorted({t.lower() for t in LATIN_RE.findall(source)}):
        if w in wl:
            specs.append(("术语", w, lambda note, w=w: w in note.lower()))

    for name in sorted(getattr(nc, "COMPANY_NAMES", set())):
        if source.count(name) >= 2:      # 只提一次多为随口举例
            specs.append(("公司", name, lambda note, n=name: n in note))

    try:
        from .evidence import prose_lines_for_numbers
        code_prose = '\n'.join(line for _, line in prose_lines_for_numbers(source))
        for code in sorted(set(nc.STOCK_CODE_RE.findall(code_prose))):
            specs.append(("代码", code, lambda note, c=code: c in note))
    except Exception:
        pass

    for label, rx in getattr(nc, "topic_compiled", {}).items():
        if label in _SOFT_LABELS:
            continue
        try:
            if len(rx.findall(source)) >= 2:
                specs.append(("主题", label, lambda note, rx=rx: bool(rx.search(note))))
        except Exception:
            pass

    for tool in sorted(getattr(nc, "TAG_TOOL_SET", set())):
        if tool not in _SOFT_LABELS and source.count(tool) >= 2:
            specs.append(("工具", tool, lambda note, t=tool: t in note))

    return specs


def anchors_recall(note: str, source: str) -> dict:
    """源里的高精度锚点，笔记覆盖了多少。

    Returns: {total, matched, missed[], recall|None, kinds{}}
    `total < MIN_ANCHORS` 时 recall 判据不适用（调用方据此跳过）。
    """
    specs = _anchor_specs(source)
    missed = []
    kinds = collections.Counter()
    for kind, disp, matcher in specs:
        kinds[kind] += 1
        try:
            ok = matcher(note)
        except Exception:
            ok = True
        if not ok:
            missed.append(f"{kind}:{disp}")
    total = len(specs)
    matched = total - len(missed)
    return {
        "total": total,
        "matched": matched,
        "missed": sorted(missed)[:10],
        "recall": (matched / total) if total else None,
        "kinds": dict(kinds),
    }


# ---------------------------------------------------------------- ② 破碎形态

_SENT_SPLIT = re.compile(r"[。！？\n]")


def _adjacent_para_similarity(note: str) -> float:
    paras = [nows(p) for p in re.split(r"\n\s*\n", note) if len(nows(p)) >= 40]
    worst = 0.0
    for a, b in zip(paras, paras[1:]):
        worst = max(worst, difflib.SequenceMatcher(None, a, b).ratio())
    return worst


def form_signals(note: str) -> dict:
    """形态级「疑压碎 / 疑注水」信号。返回 {signals[], max_sentence, comma_ratio, adjacent_sim}。

    括号类信号已废弃（见模块 docstring）。
    """
    sigs = []
    longest = max((len(nows(s)) for s in _SENT_SPLIT.split(note)), default=0)
    if longest > LONG_SENTENCE:
        sigs.append("A超长句")
    total = count_words(note)
    commas = len(re.findall(r"[，,]", note))
    ratio = (commas / total) if total else 0.0
    if ratio > COMMA_RATIO:
        sigs.append("C逗号密度")
    d = _adjacent_para_similarity(note)
    if d > ADJACENT_SIM:
        sigs.append("D相邻段重复")
    return {"signals": sigs, "max_sentence": longest,
            "comma_ratio": round(ratio, 4), "adjacent_sim": round(d, 3)}


# ---------------------------------------------------------------- ③ 结构缺失

# 模板模块名 → 笔记里的可能叫法（模板的文字是给人看的，笔记措辞会变）
SECTION_ALIAS = {
    "分层速览": ["速览", "TL;DR", "30秒"],
    "正反例对照": ["正反例", "对照"],
    "延伸思考": ["延伸思考", "我的想法"],
    "我的想法": ["延伸思考", "我的想法"],   # 兼容历史模块名；当前硬必备以显式契约为准
    "问答精华": ["问答", "Q：", "话题"],
    "核心定义与痛点": ["核心定义", "痛点"],
    "分维度拆解": ["分维度", "拆解"],
    "小结闭环": ["小结", "闭环"],
    "核心公式": ["核心公式", "公式"],
    "全书地图": ["全书地图", "章节脉络"],
    "核心论点拆解": ["核心论点", "论点"],
    "对比矩阵": ["对比矩阵", "矩阵"],
    "横向结论": ["横向结论", "结论"],
    "结果与数据": ["结果与数据", "结果数据", "数据与结果"],
    "可复用结构模具": ["模具", "可复用结构"],
}


def required_sections(note_type: str) -> list:
    """读取显式无条件结构；源相关模块由写作自检/语义审查核对。

    不能从prompt中的“必备”散文推导硬拦，否则源不支持的模块会驱动编造。
    返回副本，避免调用方改变后续笔记契约；未知类型不施加结构假设。
    """
    from prompts.templates import SECTION_CONTRACTS
    return list(SECTION_CONTRACTS.get(note_type, {}).get("required", ()))


def structure_missing(note: str, note_type: str) -> dict:
    """检查模板声明的必备模块是否在笔记里缺失。

    Returns: {required[], missing[], unmapped[]}（unmapped = 别名表未覆盖、不参与判定）
    """
    req = required_sections(note_type)
    missing, unmapped = [], []
    for name in req:
        keys = SECTION_ALIAS.get(name)
        if keys is None:
            unmapped.append(name)
            continue
        if not any(k in note for k in keys):
            missing.append(name)
    return {"required": req, "missing": missing, "unmapped": unmapped}


# ---------------------------------------------------------------- ③之二 条件必备结构（T9 / A6）
#
# 背景（PLAN-20261018 §6 A6 / §12.3 #6）：`required` 是**无条件**必备（现只有「分层速览」等少数），
# `source_dependent` 是「源确有关键模块时仍须完整保留」的写作义务，但**没有机械判据**，靠模型自觉。
# T9 补中间一档：`conditional` —— 由**源侧机械信号**判定「源里确实有这类内容」，此时该小节成为
# 必备（防「源不支持的模块驱动编造」的反面：源支持却被省略）。
#
# 纪律：这一档是**硬拦**，所以阈值只能从严（宁可不触发，不可误伤）。每个信号都只认
# 明确标记/可数结构，不认语感；上线前必须先在存量语料上离线测命中率与误报率（§8）。

_COUNTEREXAMPLE_MARKERS = (
    "反例", "例外", "恰恰相反", "与之相反", "正好相反", "误以为",
    "常见误区", "常见错误", "踩坑", "雷区", "并不成立", "经不起推敲",
)
_FORMULA_MARKERS = ("公式", "算式")
_CHAPTER_MARKERS = ("目录", "全书", "本书", "章节")   # 单章名由 `第N章` 计数判，不在这里重复
_SENT_SPLIT_RE = re.compile(r"[。！？；!?\n]+")
# 数字+单位：与 _UNITS 同口径（口径一致才谈得上「同一单位的两个数」）
_NUM_UNIT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(%|％|亿美元|美元|美金|万亿|亿|万元|万|千|百|元|倍)")


def _units_of_sentence(sent: str) -> dict:
    """句内「单位 → 该单位下的不同数值集合」。"""
    by_unit = {}
    for m in _NUM_UNIT_RE.finditer(sent):
        num, unit = m.group(1), m.group(2)
        if num in _JUNK_NUMS:
            continue
        by_unit.setdefault(unit, set()).add(num)
    return by_unit


def signal_counterexample(source: str) -> dict:
    """反例/例外信号：明确的「反例/例外/误以为/踩坑」类标记 ≥2 处（或「反例」≥1 处）。"""
    hits = [m for m in _COUNTEREXAMPLE_MARKERS if m in (source or "")]
    strong = [m for m in ("反例",) if m in (source or "")]
    return {"fired": len(hits) >= 2 or bool(strong), "hits": hits}


def signal_formula(source: str) -> dict:
    """公式信号：出现「公式/算式」≥2 次，或有 3 处以上等式号（防单次提及即触发）。"""
    text = source or ""
    markers = sum(text.count(m) for m in _FORMULA_MARKERS)
    eqs = text.count("=") + text.count("＝")
    return {"fired": markers >= 2 or eqs >= 3, "markers": markers, "eqs": eqs}


def signal_chapters(source: str) -> dict:
    """章节/全书信号：出现「第N章」≥2 处，或有「目录/全书/本书」标记。"""
    text = source or ""
    chaps = len(re.findall(r"第[一二三四五六七八九十百\d]{1,3}章", text))
    marks = [m for m in _CHAPTER_MARKERS if m in text]
    return {"fired": chaps >= 2 or bool(marks), "chapters": chaps, "marks": marks}


def signal_comparison(source: str) -> dict:
    """对比信号：≥2 个句子里各自出现「同一单位的 ≥2 个不同数值」（多对象同口径对比）。"""
    groups = 0
    for sent in _SENT_SPLIT_RE.split(source or ""):
        if any(len(v) >= 2 for v in _units_of_sentence(sent).values()):
            groups += 1
    return {"fired": groups >= 2, "groups": groups}


def signal_numeric_dense(source: str) -> dict:
    """数值密集信号：源里「数字+单位」配对 ≥12 处（案例类讲结果/数据的下限）。"""
    n = len(_NUM_UNIT_RE.findall(source or ""))
    return {"fired": n >= 12, "pairs": n}


CONDITIONAL_SIGNALS = {
    "counterexample": signal_counterexample,
    "formula": signal_formula,
    "chapters": signal_chapters,
    "comparison": signal_comparison,
    "numeric_dense": signal_numeric_dense,
}


def conditional_sections(note_type: str, source: str) -> dict:
    """按源侧信号算出本篇**应无条件具备**的条件小节：{小节名: 信号名}。

    注意：只返回信号 firing 的小节；信号不触发时该小节仍可作为「源支持就写」的可选模块，
    只是不再硬拦（这正是 T9 要的「防源不支持的模块驱动编造」的另一半）。
    """
    from prompts.templates import SECTION_CONTRACTS
    spec = (SECTION_CONTRACTS.get(note_type) or {}).get("conditional") or {}
    out = {}
    for section, sig_name in spec.items():
        fn = CONDITIONAL_SIGNALS.get(sig_name)
        if fn and fn(source or "").get("fired"):
            out[section] = sig_name
    return out


def structure_obligations(note: str, note_type: str, source: str = "") -> dict:
    """无条件必备 + 信号触发的条件必备，一起判缺。

    Returns: {required[], conditional{}, missing[], missing_conditional[], unmapped[]}
    """
    base = structure_missing(note, note_type)
    fired = conditional_sections(note_type, source)
    missing_cond, unmapped = [], list(base["unmapped"])
    for section, sig in fired.items():
        keys = SECTION_ALIAS.get(section)
        if keys is None:
            unmapped.append(section)
            continue
        if not any(k in note for k in keys):
            missing_cond.append({"section": section, "signal": sig})
    return {**base, "conditional": fired,
            "missing_conditional": missing_cond, "unmapped": unmapped}


# ---------------------------------------------------------------- ④ 照搬重合

def verbatim_ratio(note: str, source: str, n: int = VERBATIM_N) -> float:
    """笔记 n 字滑窗片段出现在归一化源文本中的占比。

    用「重合度」而非「长度」判照搬：转录稿重构后必然变长，写长不等于照搬；
    逐字搬运才会命中。（对本类语音识别稿源实测几乎不触发，保留用于文章类源。）
    """
    src = nows(source)
    body = nows(strip_note(note))
    if len(body) < n:
        return 0.0
    shingles = [body[i:i + n] for i in range(0, len(body) - n + 1)]
    if not shingles:
        return 0.0
    return sum(1 for s in shingles if s in src) / len(shingles)


# ---------------------------------------------------------------- 汇总

# 内容判据分级：哪些算「硬失败（落盘门禁应拦 + 触发重试）」，哪些只做「软抽检（不拦盘）」。
# 设计：硬失败 = 确定性、可修、误报低的核心缺陷；形态信号（压碎/堆砌/注水）误报相对高，
# 且属表达层，交给 DISPATCH_PROMPT / QUALITY_GATE_SELFCHECK 软提示自修，不进硬拦。
_HARD_FLAG_KEYS = (
    "疑遗漏核心事实",   # ① 硬锚点召回 < 阈值（仅 applicable 时由调用方决定是否计入）
    "疑照搬未合成",     # ④ 逐字搬运未合成
    "结构缺失",         # ③ 模板必备模块缺失
)


def classify_content_flags(flags: list) -> dict:
    """把 content_flags 的 flags 分成硬失败 / 软抽检两类。

    Returns: {"hard": [...], "soft": [...]}
    - hard：落盘门禁应拦 + 交外层模型带反馈重试（源于 ①锚点召回 / ④照搬 / ③结构缺失）。
    - soft：只交父 Agent 抽检，不拦盘（形态信号 ②A/C/D）。
    """
    hard, soft = [], []
    for f in flags:
        if any(k in f for k in _HARD_FLAG_KEYS):
            hard.append(f)
        else:
            soft.append(f)
    return {"hard": hard, "soft": soft}


def content_flags(note: str, source: str = "", note_type: str = "",
                  min_anchors: int = MIN_ANCHORS,
                  recall_threshold: float = RECALL_THRESHOLD, source_author: str = "") -> dict:
    """四类内容判据汇总。返回 {flags[], details{}, applicable{}}。

    调用方（verifier）把 flags 非空视为「需父 Agent 抽检」，**不拦落盘**。
    """
    flags, details, applicable = [], {}, {}
    body = strip_note(note)
    src = strip_source(source) if source else ""

    if src:
        a = anchors_recall(body, src)
        details["anchors"] = a
        applicable["anchors"] = a["total"] >= min_anchors
        if applicable["anchors"] and a["recall"] is not None and a["recall"] < recall_threshold:
            flags.append(
                f"疑遗漏核心事实：硬锚点召回 {a['recall']:.0%} < {recall_threshold:.0%}"
                f"（{a['matched']}/{a['total']}，丢了 {'、'.join(a['missed'][:5])}）"
            )
        v = verbatim_ratio(body, src)
        details["verbatim"] = round(v, 3)
        if v > VERBATIM_THRESHOLD:
            flags.append(f"疑照搬未合成：与源 {VERBATIM_N} 字片段重合 {v:.0%} > {VERBATIM_THRESHOLD:.0%}")

    f = form_signals(body)
    details["form"] = f
    if "A超长句" in f["signals"]:
        flags.append(f"疑压碎：最长句 {f['max_sentence']} 字 > {LONG_SENTENCE}，"
                     "可能是把多件事压进一句导致语义残缺")
    if "C逗号密度" in f["signals"]:
        flags.append(f"疑长句堆砌：逗号密度 {f['comma_ratio']} > {COMMA_RATIO}")
    if "D相邻段重复" in f["signals"]:
        flags.append(f"疑同义反复：相邻段落相似度 {f['adjacent_sim']} > {ADJACENT_SIM}")

    s = structure_obligations(body, note_type, source) if source else structure_missing(body, note_type)
    details["structure"] = s
    if s["missing"]:
        flags.append(f"结构缺失：缺必备模块 {'、'.join(s['missing'])}")
    # T9 / A6：信号触发时该小节升为必备（源支持却被省略 = 确定性内容缺陷）
    for mc in s.get("missing_conditional") or []:
        flags.append(f"条件结构缺失：源含「{mc['signal']}」信号，但缺必备模块「{mc['section']}」")

    from .evidence import check_note_evidence
    evidence = check_note_evidence(note, source, source_author=source_author)
    details["evidence"] = evidence
    flags.extend(f"证据待复核[{f['code']}] 第{f['note']['line_start']}行：{f['reason']}"
                 for f in evidence["findings"] if f["decision"] == "review")
    return {"flags": flags, "details": details, "applicable": applicable}
