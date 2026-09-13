"""笔记机械质量门禁（零 AI 依赖）。

背景（DECISION-20260905 / DECISION-20260907）：FORCE_AGENT_MODE=1 主路径上，模型输出直接经
save_summary_only 落盘，历史上偶发正文写来源链接（formatter 权威追加后出现两个
链接）、篇幅严重偏离模板区间；2026-09-07 起去 H1，正文出现一级标题改为拦截
（标题由文件名/飞书节点标题承担，代码围栏内 # 注释不算）。AI 审核员
（NOTE_QUALITY_GATE）默认关且无外部 AI 时返回 None，兜不住，故提供机械基线：
- 8 模板字数区间与模板「风格/字数」行声明一一对应（tests 防漂移）；
- 硬阈值（<min*0.6 或 >max*1.5）拦截，轻微越界仅 warning 不拦；
- note_type 不在字数表内时跳过篇幅检查（自定义类型不误伤）。

本模块不受 NOTE_QUALITY_GATE 开关控制：它是落盘前的机械底线，不是可选 AI 审核。
"""
import re

# 各模板单篇正文字数区间（与 prompts/templates.py 中「单篇正文 X～Y 字」声明同步维护；
# tests/test_note_mechanical_gate.py::TestWordLimitsConsistency 防漂移）。
# 说明（2026-09-13 决策·source-aware 参考值）：
#   字数不再是「硬拦截区间」，而是「参考值」——具体目标由原文长度决定（详见 verify_note_mechanical）。
#   机械门禁只在两种极端拦截：① 字数极低（疑似内容缺失）；② 字数畸高（超 sanity 天花板，疑似原文照搬）。
#   处于参考带之外但非畸高 → 不拦，仅返回 review_flags 供父 Agent 抽检。
#   无源长时（纯文章/旧队列）退回固定区间兜底。
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

# 参考值比例带（源长 × 比例）：仅作触发，非硬拦。水货多的源压到 0.25 也 OK；干货多的源压到 0.45 也可能丢。
REFERENCE_RATIO_BAND = (0.25, 0.45)
# 字数畸高硬天花板：超过即视为原文照搬/未合成，落盘前必拦（有源长时取 max(绝对值, 源长×0.7)）。
SANITY_CEILING_ABS = 8000
# 字数极低硬下限：低于此视为内容缺失，必拦（有源长时取 max(绝对值, 源长×0.25×0.6)）。
SANITY_FLOOR_ABS = 400
# 偏短触发抽检的软阈值：低于此且低于 ref_lo 才提示父 Agent 复核是否遗漏（水货源压更短也放行）。
SHORT_REVIEW_THRESHOLD = 1500

# 动态扩容档位（兼容无源长场景的兜底上限；有源长时优先用 source-aware 参考带）
DYNAMIC_WORD_TIERS = [3000, 4000, 5000]

# 字数硬阈值（无源长兜底 + 地板计算用）：低于下限 60% 或高于上限 150% 判为缺失/失败
WORD_HARD_LOW_RATIO = 0.6
WORD_HARD_HIGH_RATIO = 1.5

_H1_RE = re.compile(r"^# .+", re.MULTILINE)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)  # H1 扫描前剥离，围栏内 # 注释不算标题
_SOURCE_LINK_LINE_RE = re.compile(r"^\s*\**\s*来源链接\s*\**\s*[：:]", re.MULTILINE)


def count_note_words(note: str) -> int:
    """去空白后的字符数（与中文写作习惯对齐，markdown 符号计入）。"""
    return len(re.sub(r"\s", "", note))


# ---- 过度压缩启发式（零 AI，纯形态检测；命中视为「该扩容」信号，不拦但报告） ----
# 设计动机（2026-09-13）：机械门禁判不了语义，但能抓「形式的过度压缩」——
# 即把多件事压进一个超长复句、用连环括号堆术语。这些形态是语义残缺的高频伴随特征。
_COMPRESS_LONG_SENT = 140      # 单句（以 。！？\n 切分）去空白后超过此字数，疑似压碎
_COMPRESS_BRACKET_PAIRS = 2    # 单段落内出现 ≥ 此值的括号对，疑似术语堆砌
_COMPRESS_COMMA_RATIO = 0.085  # 逗号数 / 总字数 超过此比例，疑似长句堆砌（每 ~12 字一个逗号）


def detect_compression_issues(note: str) -> list[str]:
    """检测「疑似为了压缩而压碎语义」的形态信号。返回 warning 文案列表（空=无信号）。

    仅作信号，不拦截落盘——命中时由父 Agent 决策是否让子 Agent 用更高字数目标重写。
    """
    warnings: list[str] = []
    prose = _CODE_FENCE_RE.sub("", note)

    # 信号 A：超长句
    sentences = re.split(r"[。！？\n]", prose)
    longest = max((len(re.sub(r"\s", "", s)) for s in sentences), default=0)
    if longest > _COMPRESS_LONG_SENT:
        warnings.append(
            f"疑似过度压缩（信号A·超长句）：最长句 {longest} 字 > {_COMPRESS_LONG_SENT}，"
            "可能是把多件事压进一句导致语义残缺，建议拆句或扩容重写"
        )

    # 信号 B：连环括号（术语堆砌）
    for para in prose.split("\n"):
        if not para.strip():
            continue
        pairs = len(re.findall(r"[（(][^()（）]*[)）]", para))
        if pairs >= _COMPRESS_BRACKET_PAIRS:
            warnings.append(
                f"疑似过度压缩（信号B·连环括号）：段落内 {pairs} 处括号解释，"
                "疑似术语堆砌未展开，建议把括号内容写成完整句"
            )
            break  # 一段命中即报，避免重复刷屏

    # 信号 C：逗号密度异常
    total = count_note_words(prose)
    commas = len(re.findall(r"[，,]", prose))
    if total > 0 and commas / total > _COMPRESS_COMMA_RATIO:
        warnings.append(
            f"疑似过度压缩（信号C·逗号密度 {commas / total:.2f} > {_COMPRESS_COMMA_RATIO}）："
            "疑似长句堆砌，建议拆成短句"
        )

    return warnings


def verify_note_mechanical(note: str, note_type: str = "", source_url: str = "",
                           max_words: int = None, source_chars: int = None) -> dict:
    """机械校验模型输出的笔记：无一级标题 / 来源链接卫生 / 字数（source-aware 参考值）/ 压缩启发式。

    返回 {"passed", "issues", "warnings", "compression_warnings", "review_flags"}：
    - issues 非空 → 必须拦截（H1/来源链接/URL/畸高照搬/极低缺失），子 Agent 修复后重试；
    - warnings 仅提示，不拦；
    - compression_warnings：过度压缩启发式信号，不拦，供父 Agent 决策是否扩容；
    - review_flags：超出「参考值」但非畸高时返回，提示父 Agent 抽检（不拦落盘）；
    - source_chars：原文（转录稿）去空白字符数，有则按 源长×比例带 作参考值；无则退回固定区间兜底。
    - max_words：无源长时的动态扩容覆盖（4000/5000）。
    """
    issues: list[str] = []
    warnings: list[str] = []
    review_flags: list[str] = []
    prose = _CODE_FENCE_RE.sub("", note)  # 剥离代码围栏，围栏内 # 注释不算一级标题
    h1_list = _H1_RE.findall(prose)
    if h1_list:
        issues.append(f"去 H1 约束：正文出现 {len(h1_list)} 个一级标题（# ），"
                      "标题由文件名/飞书节点标题承担，须删除后重交")
    if _SOURCE_LINK_LINE_RE.search(note):
        issues.append("正文不得写来源链接行（**来源链接**：...）：由系统 formatter 权威追加，"
                      "重复写入会导致落盘后出现两个链接")
    if source_url and source_url in note:
        issues.append("正文不得出现原文 URL（来源链接由系统权威追加，模型写的 URL 不可信）")

    limits = NOTE_WORD_LIMITS.get(note_type)
    count = count_note_words(note)
    if limits:
        lo, hi = limits
        hi = max(hi, max_words) if max_words else hi  # 无源长时动态扩容覆盖上限
        if source_chars:
            # —— source-aware 参考值（优先）——
            ref_lo = int(source_chars * REFERENCE_RATIO_BAND[0])
            ref_hi = int(source_chars * REFERENCE_RATIO_BAND[1])
            ceiling = max(SANITY_CEILING_ABS, int(source_chars * 0.7))
            # 极低硬地板：仅极端缺失（< 绝对 400 字）拦截；更短者交由「偏短」抽检信号覆盖
            floor = SANITY_FLOOR_ABS
            if count > ceiling:
                issues.append(f"字数 {count} 超畸高天花板（源长 {source_chars}×0.7={int(source_chars*0.7)}，"
                              f"绝对 {SANITY_CEILING_ABS}），疑似原文照搬/未合成，须重做压缩后重交")
            elif count < floor:
                issues.append(f"字数 {count} 低于硬下限（源长 {source_chars}×{REFERENCE_RATIO_BAND[0]}×"
                              f"{WORD_HARD_LOW_RATIO:.0%}={floor}），疑似内容缺失，须补全后重交")
            elif count > ref_hi:
                review_flags.append(f"超参考值：{count} > 源长{source_chars}×0.45={ref_hi}，"
                                    f"疑未充分合成，父 Agent 需抽检")
            elif count < ref_lo and count < SHORT_REVIEW_THRESHOLD:
                review_flags.append(f"偏短：{count} < 源长{source_chars}×0.25={ref_lo} 且<{SHORT_REVIEW_THRESHOLD}，"
                                    f"疑遗漏核心，父 Agent 需抽检")
        else:
            # —— 无源长兜底：固定区间 + 弹性上限 ——
            if count < lo * WORD_HARD_LOW_RATIO:
                issues.append(f"字数 {count} 低于 {note_type} 模板硬下限（区间 {lo}～{hi} 字的"
                              f"{WORD_HARD_LOW_RATIO:.0%}），疑似内容缺失，须补全后重交")
            elif count > hi * WORD_HARD_HIGH_RATIO:
                issues.append(f"字数 {count} 超出 {note_type} 模板硬上限（区间 {lo}～{hi} 字的"
                              f"{WORD_HARD_HIGH_RATIO:.0%}），须压缩或按模板规则拆分后重交")
            elif count > hi:
                warnings.append(f"字数 {count} 略高于 {note_type} 模板区间上限 {hi}，建议复核是否堆砌/照搬")
            elif count < lo:
                warnings.append(f"字数 {count} 略低于 {note_type} 模板区间下限 {lo}，建议复核内容完整度")
    compression_warnings = detect_compression_issues(note)
    return {
        "passed": not issues,
        "issues": issues,
        "warnings": warnings,
        "compression_warnings": compression_warnings,
        "review_flags": review_flags,
    }
