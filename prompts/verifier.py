"""笔记机械质量门禁（零 AI 依赖）。

## 判据演进（务必先读）

- 2026-09-05（`DECISION-20260905-mechanical-verifier.md`）：引入机械门禁，字数用**固定区间**硬拦。
- 2026-09-13（`DECISION-20260913-gate-source-aware-review.md`）：固定区间导致「为压缩而压缩」
  （语义破碎、多句压成一句、丢原意、读不懂），改为**「源长×(0.25,0.45)」参考带** + 父 Agent 抽检。
- **2026-09-15（`DECISION-20260915-content-first-gate.md`）：比例带同样被数据推翻，本文件改为内容判据。**

## 现行规则（2026-09-15 起）

**硬拦（issues，不落盘）**——只留确定性的与极端情形：
1. 正文出现一级标题（标题由文件名承担）
2. 正文写了来源链接行（formatter 会权威追加，重复写会出现两个链接）
3. 正文出现原文 URL
4. 字数 `< MIN_WORDS`（300）——内容缺失兜底
5. 字数 `> max(8000, 源长×3)`——**失控保护**，纯防程序跑飞，不承担质量含义

**触发抽检（review_flags，不拦落盘）**——来自 `prompts/content_signals.py`：
① 硬锚点召回 < 60%（源锚点 < 4 时该判据不适用）
② 破碎形态：A 超长句 / C 逗号密度 / D 相邻段高相似（括号类信号已废弃）
③ 结构缺失：模板声明的「必备」模块没写
④ 照搬重合：与源 20 字片段重合超阈值

## 已被数据否定、禁止复活的机制

- ❌ **任何形式的「字数上限压制」**：只要存在「不能比 X 长」的线，子 Agent 就会去贴它，
  必然产生「为过门禁而砍内容」。实测同一 structured 模板下笔记长度差 2.8 倍，
  比值判据主要在测「子 Agent 这次写得长不长」，与源质量无关。
- ❌ **「偏短」比例信号**（`count < 源长×0.25 且 <1500`）：同一批语音识别稿里，
  长源批次 0.20 报偏短、短源批次 1.93 报超参考值，近 10 倍跨度。
- ❌ **括号类形态信号**：模板本身要求「专业名词首现必须附大白话解释」，括号是规定动作。

**用户硬约束（不得违背）**：内容完整第一，绝不用绝对值字数区间压制篇幅。
"""
import re

from . import content_signals as CS

# 各模板单篇正文字数区间 —— 仅作「参考值」记录用途，**不再参与任何拦截或抽检触发**。
# 保留常量是为了：
#   ① 与 prompts/templates.py 的「风格/字数」声明对齐（tests/test_note_mechanical_gate.py 防漂移）；
#   ② 后续若要按模板生成参考意见，仍可取用。
NOTE_WORD_LIMITS = {
    "structured": (1500, 5000),
    "general": (1500, 5000),  # 兜底通用版，与 structured 同 prompt 同区间
    "key_points": (800, 1500),
    "case": (900, 1800),
    "opinion": (500, 1000),
    "interview": (1000, 2000),
    "roundup": (1000, 2000),
    "reading": (1000, 2200),
    "dissection": (800, 1500),
}

# 内容缺失兜底：低于此视为「几乎没写」。用户决策 300（2~3 分钟、只讲一两个知识点的短视频
# 本来就写不出多少字，下限不能一刀切偏高）。
MIN_WORDS = 300
# 失控保护：超过即视为程序跑飞。取「绝对值 8000」与「源长×3」的较大者——
# ×3 而非旧版的 ×0.7，正是不让上限重新变成压缩压力。
SANITY_CEILING_ABS = 8000
SANITY_CEILING_RATIO = 3.0

_H1_RE = re.compile(r"^# .+", re.MULTILINE)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)  # H1 扫描前剥离，围栏内 # 注释不算标题
_SOURCE_LINK_LINE_RE = re.compile(r"^\s*\**\s*来源链接\s*\**\s*[：:]", re.MULTILINE)

# 兼容旧引用：旧的动态扩容档位已无意义（上限不再压制篇幅），保留空位以免 ImportError。
DYNAMIC_WORD_TIERS = [3000, 4000, 5000]
WORD_HARD_LOW_RATIO = 0.6
WORD_HARD_HIGH_RATIO = 1.5


def count_note_words(note: str) -> int:
    """去空白后的字符数（与中文写作习惯对齐，markdown 符号计入）。"""
    return len(re.sub(r"\s", "", note))


def detect_compression_issues(note: str) -> list[str]:
    """「疑似为了压缩而压碎语义」的形态信号。委托 `content_signals.form_signals`。

    括号类信号已废弃（见模块 docstring）。本函数保留原名以兼容旧调用方。
    """
    sigs = CS.form_signals(note)["signals"]
    out = []
    if "A超长句" in sigs:
        out.append(f"疑似过度压缩（信号A·超长句）：最长句超 {CS.LONG_SENTENCE} 字，"
                   "可能是把多件事压进一句导致语义残缺，建议拆句或扩容重写")
    if "C逗号密度" in sigs:
        out.append(f"疑似过度压缩（信号C·逗号密度 > {CS.COMMA_RATIO}）：疑似长句堆砌，建议拆成短句")
    if "D相邻段重复" in sigs:
        out.append(f"疑似注水（信号D·相邻段相似度 > {CS.ADJACENT_SIM}）：相邻段落近乎同义，建议合并")
    return out


def verify_note_mechanical(note: str, note_type: str = "", source_url: str = "",
                           max_words: int = None, source_chars: int = None,
                           source_text: str = "") -> dict:
    """机械校验：卫生类硬拦 + 极端字数硬拦 + 内容判据触发抽检。

    Args:
        note: 模型输出的笔记正文。
        note_type: 模板类型（用于结构完整性判据）。
        source_url: 原文 URL（用于「正文不得含 URL」）。
        source_chars: 原文去空白字符数（仅用于失控上限 `源长×3`）。
        max_words: **已废弃**（保留形参以免旧调用方报错；上限不再压制篇幅）。
        source_text: 原文全文。给了才能跑「硬锚点召回」与「照搬重合」；没给则只跑
            形态 + 结构判据（调用方应从 raw_file 传入）。

    Returns:
        {"passed", "issues", "warnings", "compression_warnings", "review_flags", "review_details"}
        - issues 非空 → 硬拦，子 Agent 修复后重交
        - review_flags 非空 → **不拦落盘**，交父 Agent 按五维 rubric 抽检
    """
    issues: list[str] = []
    warnings: list[str] = []
    review_flags: list[str] = []
    review_details: dict = {}

    prose = _CODE_FENCE_RE.sub("", note)  # 围栏内 # 注释不算一级标题
    h1_list = _H1_RE.findall(prose)
    if h1_list:
        issues.append(f"去 H1 约束：正文出现 {len(h1_list)} 个一级标题（# ），"
                      "标题由文件名/飞书节点标题承担，须删除后重交")
    if _SOURCE_LINK_LINE_RE.search(note):
        issues.append("正文不得写来源链接行（**来源链接**：...）：由系统 formatter 权威追加，"
                      "重复写入会导致落盘后出现两个链接")
    if source_url and source_url in note:
        issues.append("正文不得出现原文 URL（来源链接由系统权威追加，模型写的 URL 不可信）")

    count = count_note_words(note)
    if count < MIN_WORDS:
        issues.append(f"内容缺失：字数 {count} 低于下限 {MIN_WORDS}，疑似几乎未写内容，须补全后重交")
    else:
        ceiling = max(SANITY_CEILING_ABS, int((source_chars or 0) * SANITY_CEILING_RATIO))
        if count > ceiling:
            issues.append(f"失控保护：字数 {count} 超上限 max(8000, 源长×{SANITY_CEILING_RATIO:g})={ceiling}，"
                          "疑似程序跑飞或整段照搬，须核查后重交")
    # 注：上述两条刻意不以「字数」开头——`articles/main.py` 的「重试放行」逃生舱只豁免
    # 旧的区间类越界（源本身撑不起区间）；内容缺失与失控保护属确定性缺陷，永不放行。

    # 内容判据（取代旧「字数比例带」）。flags 非空只触发抽检，不拦落盘。
    cf = CS.content_flags(note, source_text or "", note_type)
    review_flags.extend(cf["flags"])
    review_details = cf["details"]

    compression_warnings = detect_compression_issues(note)
    warnings.extend(compression_warnings)

    return {
        "passed": not issues,
        "issues": issues,
        "warnings": warnings,
        "compression_warnings": compression_warnings,
        "review_flags": review_flags,
        "review_details": review_details,
        "word_count": count,
    }
