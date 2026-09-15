"""prompts/content_signals.py — 落盘门禁的「内容判据」（零 AI · 只读 · 可单测）

设计依据：`docs/decisions/DECISION-20260915-content-first-gate.md`
用户硬约束：**绝不用绝对值字数区间**（会导致「为压缩而压缩」、语义破碎、丢原意）；
**内容完整第一**；字数只做兜底与失控保护，不承担质量判定。

本模块提供四类**内容判据**，替代原来的「笔记字数 ÷ 源长」比例带：

  ① anchors_recall     硬锚点召回 —— 源里的数字/术语/公司名/代码，笔记丢了多少
  ② form_signals       破碎形态     —— A 超长句 / C 逗号密度 / D 相邻段高相似
  ③ structure_missing  结构缺失     —— 模板声明的「必备」模块有没有缺
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
    """构造锚点检查项 [(kind, display, matcher)]，matcher(note) -> 笔记是否保留。"""
    from shared import note_classify as nc
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
        for code in sorted(set(nc.STOCK_CODE_RE.findall(source))):
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

_CONTAINER_HINT = ("模块", "骨架", "每篇", "结构", "章节", "范畴")
# 模板模块名 → 笔记里的可能叫法（模板的文字是给人看的，笔记措辞会变）
SECTION_ALIAS = {
    "分层速览": ["速览", "TL;DR", "30秒"],
    "正反例对照": ["正反例", "对照"],
    "延伸思考": ["延伸思考", "我的想法"],
    "我的想法": ["延伸思考", "我的想法"],   # 模板行写作「延伸思考 + 我的想法（必备）」，解析会截到后半段
    "问答精华": ["问答", "Q：", "话题"],
    "核心定义与痛点": ["核心定义", "痛点"],
    "分维度拆解": ["分维度", "拆解"],
    "小结闭环": ["小结", "闭环"],
    "核心公式": ["核心公式", "公式"],
    "全书地图": ["全书地图", "章节脉络"],
    "核心论点拆解": ["核心论点", "论点"],
    "对比矩阵": ["对比矩阵", "矩阵"],
    "横向结论": ["横向结论", "结论"],
    "可复用结构模具": ["模具", "可复用结构"],
}


def required_sections(note_type: str) -> list:
    """从 `prompts/templates.py` 的模板文字里解析出『（…必备…）』声明的模块名。

    模板把「必备」写在散文里，机器看不见；这里把它变成可判定的清单。
    """
    try:
        from prompts.templates import NOTE_TEMPLATES
    except Exception:
        return []
    entry = NOTE_TEMPLATES.get(note_type) or {}
    text = entry.get("prompt", "") if isinstance(entry, dict) else str(entry)
    out = []
    for line in text.splitlines():
        if "必备" not in line:
            continue
        m = re.search(r"([^\s（(]{2,24}?)\s*[（(]([^）)]*必备[^）)]*)[）)]", line)
        if not m:
            continue
        name = re.sub(r"^#+\s*", "", m.group(1))
        name = re.sub(r"^\S{0,3}[、.：:]\s*", "", name)
        name = re.sub(r"^\d+\s*", "", name).strip(" *`：:-")
        if not name or any(h in name for h in _CONTAINER_HINT):
            continue
        if name not in out:
            out.append(name)
    return out


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

def content_flags(note: str, source: str = "", note_type: str = "",
                  min_anchors: int = MIN_ANCHORS,
                  recall_threshold: float = RECALL_THRESHOLD) -> dict:
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

    s = structure_missing(body, note_type)
    details["structure"] = s
    if s["missing"]:
        flags.append(f"结构缺失：缺必备模块 {'、'.join(s['missing'])}")

    return {"flags": flags, "details": details, "applicable": applicable}
