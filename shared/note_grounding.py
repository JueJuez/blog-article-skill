# -*- coding: utf-8 -*-
"""shared/note_grounding.py — 两类「语义层可定位」的只读审计（零 AI、零写入）

2026-10-19 留出轮实测暴露的两个检查盲区（证据见
`notes/_meta/eval/20261019-holdout-5/ROOT_CAUSE_REGRESSIONS.md`）：

1. **分析层数字不可溯源**：`笔记者分析 / 延伸思考 / 自测 / 讲出来` 里的
   「三年份素材」在源里不存在；中文数字不进入硬锚点集合，机械检查抓不到。
2. **表格数字挂错主体**：多人实录里「结果与数据」表把哥飞的排名因素写进阿彪行；
   现有证据契约明确「不识别多说话者归属」，锚点只查数字在不在、不查挂谁名下。

本模块只产出**报告项**，不参与落盘门禁、不改笔记、不调用模型。两项判据都先做
「可解释的保守匹配」，误报由 `scripts/audit_note_grounding.py` 原样列出，不静默吞掉。

设计边界：
- 分析层数字：只扫标题命中分析/元层的小节；列表编号（`1.`）与「第X」序数不算；
  就近标了「估算/推断/假设/约/可能」的跳过（那是合规的推断）。
- 表格归属：只对「首列是源里讲者名」的行做检查；4-gram 命中 ≥3 个讲者段落视为
  通用词跳过；拉丁词用 casefold，避免 `YouTube` vs `youtube` 的假阳性。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- 分析层数字

ANALYSIS_HEADING = re.compile(
    r"^#{1,6}\s*.*(笔记者分析|延伸思考|自测|讲出来|思维模型|我的想法|我的立场|个人观点|补充信息)")
_HEADING = re.compile(r"^#{1,6}\s")
_LIST_PREFIX = re.compile(r"^\s*(?:\d+[.、)]|[-*+])\s+")
_CN_NUM = "零一二三四五六七八九十百千万两半"
_NUM_TOKEN = re.compile(
    rf"(?:\d[\d,\.]*%?|[{_CN_NUM}]{{1,4}}(?:年|个月|月|天|倍|万|亿|%|块|元))")
_INFER_HINT = re.compile(r"(估算|估计|推断|假设|比如|例如|约|大概|可能|预计|测算|推测|如果|假如|若是)")

# ---------------------------------------------------------------- 表格归属

# 源里作为小节标题的通用词，不是讲者名
_NOT_SPEAKER = {
    "背景", "方法", "步骤", "结论", "原因", "建议", "注意", "问题", "答案", "总结",
    "金句", "观点", "案例", "数据", "核心", "定义", "原则", "结果", "目标", "现状",
    "策略", "风险", "备注", "作者", "来源", "时间", "标题", "开场", "下午场", "上午场",
    "主题", "分享", "内容", "产品", "工具", "项目", "复盘", "答疑", "提问", "回答",
}
_SPEAKER_LINE = re.compile(r"^\s*([\w\u4e00-\u9fff·]{2,12})\s*[：:]\s*")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_CJK_TERM = re.compile(r"[\u4e00-\u9fff]{4,}")
_LATIN_TERM = re.compile(r"[A-Za-z][A-Za-z0-9_.+#/-]{3,}")


def analysis_sections(note: str):
    """→ [(heading, body)]，只保留分析/元层小节。"""
    cur, buf, out = None, [], []
    for line in (note or "").splitlines():
        if _HEADING.match(line):
            if cur is not None:
                out.append((cur, "\n".join(buf)))
            cur, buf = (line if ANALYSIS_HEADING.match(line) else None), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out.append((cur, "\n".join(buf)))
    return out


def analysis_number_flags(note: str, source: str) -> list:
    """分析层里「源里找不到、又没标推断/估算」的数字。

    Returns: [{"number","section","context"}]。`number` 可能是中文数字（如「三年」）。
    """
    src = (source or "").replace(",", "").replace("，", "")
    out = []
    for head, body in analysis_sections(note):
        for m in _NUM_TOKEN.finditer(body):
            tok = m.group(0).replace(",", "")
            core = re.match(r"[\d\.]+", tok)
            probe = core.group(0) if core else tok
            if probe and probe in src:
                continue
            if tok in src:
                continue
            line_start = body.rfind("\n", 0, m.start()) + 1
            line = body[line_start:body.find("\n", m.end()) if body.find("\n", m.end()) >= 0 else len(body)]
            if _LIST_PREFIX.match(line):
                continue
            # 自测/追问里的数字是假设或问题，不是事实断言
            if "？" in line or "?" in line:
                continue
            # 「第一、二个月」是序数，不是事实数字
            if re.search(r"第[一二三四五六七八九十百]*[、,，]?\s*$", body[max(0, m.start() - 6):m.start()]):
                continue
            if _INFER_HINT.search(body[max(0, m.start() - 12):m.start()]):
                continue
            out.append({
                "number": tok,
                "section": head.strip("# ").strip(),
                "context": body[max(0, m.start() - 30):m.end() + 30].replace("\n", " "),
            })
    return out


def speaker_sections(source: str, names=None) -> dict:
    """按源里的 `名字：` 行切段 → {名字: [段文本...]}。

    `names` 给了就只认这些名字（表格首列候选）。不传时按 `_looks_speaker` 过滤，
    避免把「第一步：」「画像名称：」这类小标题当成讲者（2026-10-19 实测 H05 会识别出
    85 个假讲者，全部误报）。
    """
    secs, cur, buf = {}, None, []
    for line in (source or "").splitlines():
        m = _SPEAKER_LINE.match(line)
        name = m.group(1).strip() if m else ""
        ok = bool(name) and len(name) >= 2 and name not in _NOT_SPEAKER
        if ok and names is not None:
            ok = name in names
        if ok and names is None:
            ok = _looks_speaker(source, name)
        if ok:
            if cur is not None:
                secs.setdefault(cur, []).append("\n".join(buf))
            cur, buf = name, [line]
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        secs.setdefault(cur, []).append("\n".join(buf))
    return secs


_GENERIC_NAME = re.compile(r"^(?:第[一二三四五六七八九十0-9]+[步节讲集]|这个|那个|一个|什么|怎么|为什么|如何|具体|接下来|首先|其次|最后|另外|其中|所以|但是|如果|因为)")


def _looks_speaker(source: str, name: str) -> bool:
    """讲者名的最小判据：源里有独立 `名字：` 小节、像人名、不是「第一步/这个」这类小节名。"""
    if _GENERIC_NAME.match(name):
        return False
    if len(name) > 8:
        return False
    if not re.search(rf"(?m)^\s*{re.escape(name)}\s*[：:]", source):
        return False
    return source.count(name) >= 2


def table_rows(note: str):
    for line in (note or "").splitlines():
        if not _TABLE_ROW.match(line) or _TABLE_SEP.match(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and any(cells):
            yield cells


def _terms(text: str) -> set:
    out = set()
    for t in _CJK_TERM.findall(text):
        for i in range(len(t) - 3):
            out.add(t[i:i + 4])
    for t in _LATIN_TERM.findall(text):
        out.add(t.casefold())
    return out


def table_attribution_flags(note: str, source: str) -> list:
    """表格行主体与源内讲者段落不一致的数字/术语。

    Returns: [{"subject","term","owners","cell"}]；owners = 该术语在源里出现的讲者。
    只对「首列在源里有 `名字：` 小节、且在源里反复出现」的行检查，避免把普通表头
    （看爆款 / 定时任务）当成讲者。判据是报告项，误报由调用方原样展示。
    """
    rows = list(table_rows(note))
    subjects = {c[0].strip() for c in rows if c and c[0].strip()}
    names = {s for s in subjects if _looks_speaker(source, s)}
    if len(names) < 2:
        return []
    secs = speaker_sections(source, names=names)
    if len(secs) < 2:
        return []
    folded = {name: "\n".join(bodies).casefold() for name, bodies in secs.items()}
    out = []
    for cells in rows:
        subj = cells[0].strip()
        if subj not in names:
            continue
        cell_text = " ".join(cells[1:])
        for term in sorted(_terms(cell_text), key=len, reverse=True):
            key = term.casefold()
            owners = [n for n, body in folded.items() if key in body]
            if not owners or subj in owners:
                continue
            # 出现 ≥3 段视为通用词；只出现在 1~2 个其他讲者段落里，才是归属信号
            if len(owners) >= 3:
                continue
            out.append({"subject": subj, "term": term, "owners": owners, "cell": cell_text[:80]})
            break
    return out


def audit(note: str, source: str) -> dict:
    an = analysis_number_flags(note, source)
    at = table_attribution_flags(note, source)
    return {
        "analysis_numbers": an,
        "table_attribution": at,
        "summary": {"analysis_numbers": len(an), "table_attribution": len(at)},
    }
