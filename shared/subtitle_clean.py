"""shared/subtitle_clean.py — 字幕轻量清洗（离线、纯函数、可测）

目标：在不依赖模型的前提下，去掉自动字幕（B站 AI 字幕 / YouTube 自动生成）
常见的噪声，直接抬高所有视频类总结的天花板（投入小、收益大）。

做（保守、不伤实义）：
- 剔除片段文本里的**纯语气词口头禅**（嗯/啊/呃/哦…），仅在片段开头、
  且为独立语气词时删；不删「然后/那个/这个」等可能承载语义的词。
- 合并**相邻近重复片段**（B站 AI 字幕偶发同句跨时间戳重复，归一相似度 ≥0.9 即跳过）。
- **短片段聚合**：当前片段 ≤8 字且与上一片段相连 → 并入上一片段，减少 ASR 碎片。
- **长句全局去重**：同一长句（归一 ≥10 字）在字幕里反复出现，仅保留首次。

不做（避免误伤）：
- 不做标点恢复（无模型易误伤，留给上游/人工）。
- 不做语义改写、不删实义词。
"""

import re
from typing import List, Dict, Any, Optional

# 仅作「口头禅」删的安全语气词（出现在片段开头、且为独立语气词时删；
# 出现在句中则保留，避免误删「然后他就…」这类连语境）。
_INTERJECTIONS = (
    "嗯嗯", "嗯", "啊啊", "啊", "呃呃", "呃", "噢", "哦",
    "哎", "诶", "额", "恩", "唔", "呣", "嗻", "诶",
)

_SEP = "，,。、！？!?；;：:""'‘’“”()（）[]【】~～\\-—/|｜+="


def _norm(s: str) -> str:
    """归一：去所有空白与标点，便于近重/长句判定。"""
    return re.sub(r"[\s" + re.escape(_SEP) + "]+", "", s or "")


def _similar(a: str, b: str) -> float:
    """两段归一文本的相似度（0~1）。"""
    import difflib
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def clean_fillers(text: str) -> str:
    """剔除片段文本里的纯语气词口头禅（只在片段开头、独立语气词时删）。

    保守原则：只删无实义的感叹/迟疑词（嗯/啊/呃/哦…），
    绝不删「然后/那个/这个」等可能承载语义的词。
    """
    if not text:
        return text
    s = text.strip()
    if not s:
        return s
    changed = True
    while changed:
        changed = False
        for w in _INTERJECTIONS:
            if s == w:
                return ""
            if s.startswith(w):
                rest = s[len(w):]
                # 仅当语气词后紧跟标点/空白（确属独立口头禅）才删
                if rest and rest[0] in _SEP:
                    s = rest.lstrip(_SEP + " ").strip()
                    changed = True
                    break
    return s


def _split_sentences(text: str) -> List[str]:
    """按句界切分（保留句末标点）。"""
    return [p for p in re.split(r"([。！？!?；;])", text) if p != ""]


def _dedup_sentences(text: str, seen: set) -> str:
    """句级全局去重：同一长句（归一 ≥10 字）出现多次，仅保留首次。

    `seen` 在多次调用间共享，实现跨片段/跨段全局去重。
    """
    parts = _split_sentences(text)
    out: List[str] = []
    i = 0
    n = len(parts)
    while i < n:
        seg = parts[i]
        is_sep = (i + 1 < n) and (parts[i + 1] in "。！？!?；;")
        sent = (seg + (parts[i + 1] if is_sep else "")).strip()
        if is_sep:
            i += 2
        else:
            i += 1
        if not sent:
            continue
        key = _norm(sent)
        if len(key) >= 8 and key in seen:
            continue
        if len(key) >= 8:
            seen.add(key)
        out.append(sent)
    return "".join(out)


def preprocess_segments(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """清洗字幕片段列表。

    依次：过滤空/填充词 → 相邻近重合并 → 短片段聚合 → 长句全局去重。
    保留原片段的 start/duration 等字段；聚合时顺带更新上一片段的时间窗。

    Args:
        segments: 字幕片段 [{'start','duration','text'}, ...]
    Returns:
        清洗后的片段列表（可能比输入短）。
    """
    if not segments:
        return []
    cleaned: List[Dict[str, Any]] = []
    seen: set = set()
    last_norm: Optional[str] = None

    for seg in segments:
        t = clean_fillers(seg.get("text", "").strip())
        if not t:
            continue
        n = _norm(t)
        # 相邻近重：与上一片段归一近乎相同 → 跳过（B站 AI 字幕偶发同句跨时间戳重复）
        if last_norm and _similar(n, last_norm) >= 0.9:
            continue
        # 短片段聚合：当前 ≤8 字且与上一片段相连 → 并入上一片段（减少碎片）
        if cleaned and len(t) <= 8:
            prev = cleaned[-1]
            prev["text"] = (prev["text"] + " " + t).strip()
            prev["duration"] = max(
                prev.get("duration", 0.0),
                (seg.get("start", 0.0) + seg.get("duration", 0.0)) - prev.get("start", 0.0),
            )
            last_norm = _norm(prev["text"])
            continue
        # 长句全局去重（句级）
        t = _dedup_sentences(t, seen)
        if not t:
            continue
        item = {**seg, "text": t}
        cleaned.append(item)
        last_norm = _norm(t)
    return cleaned


def preprocess_text(text: str) -> str:
    """清洗纯文本字幕（YouTube CDP 路径等）：填充词 + 长句全局去重。

    按换行分段处理（每段独立去重，避免跨段误删）。
    """
    if not text or not text.strip():
        return text
    seen: set = set()
    out_lines: List[str] = []
    for line in text.split("\n"):
        line = clean_fillers(line.strip())
        if not line:
            continue
        line = _dedup_sentences(line, seen)
        if line:
            out_lines.append(line)
    return "\n".join(out_lines)
