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
NOTE_WORD_LIMITS = {
    "structured": (1500, 3000),
    "general": (1500, 3000),  # 兜底通用版，与 structured 同 prompt 同区间
    "key_points": (800, 1500),
    "case": (900, 1800),
    "opinion": (500, 1000),
    "interview": (1000, 2000),
    "roundup": (1000, 2000),
    "reading": (1000, 2200),
    "dissection": (800, 1500),
}

# 字数硬阈值：低于下限 60% 或高于上限 150% 判为内容缺失/压缩失败，落盘前拦截
WORD_HARD_LOW_RATIO = 0.6
WORD_HARD_HIGH_RATIO = 1.5

_H1_RE = re.compile(r"^# .+", re.MULTILINE)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)  # H1 扫描前剥离，围栏内 # 注释不算标题
_SOURCE_LINK_LINE_RE = re.compile(r"^\s*\**\s*来源链接\s*\**\s*[：:]", re.MULTILINE)


def count_note_words(note: str) -> int:
    """去空白后的字符数（与中文写作习惯对齐，markdown 符号计入）。"""
    return len(re.sub(r"\s", "", note))


def verify_note_mechanical(note: str, note_type: str = "", source_url: str = "") -> dict:
    """机械校验模型输出的笔记：无一级标题 / 来源链接卫生 / 字数区间。

    返回 {"passed": bool, "issues": [...], "warnings": [...]}：
    - issues 非空 → 必须拦截，子 Agent 按 issues 修复后重试；
    - warnings 仅提示（轻微越界），不拦。
    """
    issues: list[str] = []
    warnings: list[str] = []
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
    if limits:
        lo, hi = limits
        count = count_note_words(note)
        if count < lo * WORD_HARD_LOW_RATIO:
            issues.append(f"字数 {count} 低于 {note_type} 模板硬下限（区间 {lo}～{hi} 字的"
                          f"{WORD_HARD_LOW_RATIO:.0%}），疑似内容缺失，须补全后重交")
        elif count > hi * WORD_HARD_HIGH_RATIO:
            issues.append(f"字数 {count} 超出 {note_type} 模板硬上限（区间 {lo}～{hi} 字的"
                          f"{WORD_HARD_HIGH_RATIO:.0%}），须压缩或按模板规则拆分后重交")
        elif count < lo:
            warnings.append(f"字数 {count} 略低于 {note_type} 模板区间下限 {lo} 字，建议复核内容完整度")
        elif count > hi:
            warnings.append(f"字数 {count} 略高于 {note_type} 模板区间上限 {hi} 字，建议复核是否堆砌")
    return {"passed": not issues, "issues": issues, "warnings": warnings}
