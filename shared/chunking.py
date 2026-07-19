"""shared/chunking.py — 预处理/分块模块（C1）

被 videos 复用；后续 articles 长文亦可复用。

设计原则（PRD 架构约束 2：长度不进总结，先切后总）：
- 输入任意长度 transcript / playlist 列表，输出一串标准长度小块。
- 优先按章节/时间戳切分（有 CC/章节时质量最高），否则按固定字符窗切分。
- 提供两段式总结编排：逐块小结 → 二次合并，保证单篇笔记长度受控、不爆上下文。
"""

from typing import List, Dict, Callable, Optional, Any


# ---------------------------------------------------------------------------
# 基础分块
# ---------------------------------------------------------------------------

def chunk_text(text: str, max_chars: int = 4000, overlap: int = 200) -> List[str]:
    """把纯文本按字符窗切分（带 overlap 避免句子被切断导致语义丢失）。

    Args:
        text: 待切分文本
        max_chars: 单块最大字符数（软上限，最后一块可能略小）
        overlap: 相邻块之间的重叠字符数，缓解边界割裂

    Returns:
        切块文本列表（每块 ≤ max_chars + overlap）
    """
    if not text or not text.strip():
        return []
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        # 若未到文末，尽量在句号/换行处断句，避免硬切
        if end < n:
            cut = _find_break_point(text, end, min(max_chars, 400))
            if cut > start:
                end = cut
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        # 下一块从 end - overlap 起步
        start = max(end - overlap, start + 1)
    return chunks


def _find_break_point(text: str, preferred: int, lookback: int) -> int:
    """在 preferred 附近向前找合适的断句点（换行 / 句号 / 空格）。"""
    lo = max(0, preferred - lookback)
    window = text[lo:preferred]
    # 优先换行
    idx = window.rfind("\n")
    if idx != -1:
        return lo + idx + 1
    # 其次句子结束符
    for sep in ("。", ".", "！", "!", "？", "?", "；", ";"):
        idx = window.rfind(sep)
        if idx != -1:
            return lo + idx + 1
    # 再次空格
    idx = window.rfind(" ")
    if idx != -1:
        return lo + idx + 1
    return preferred


def chunk_segments(
    segments: List[Dict[str, Any]],
    window_seconds: int = 600,
    max_chars: int = 4000,
) -> List[Dict[str, Any]]:
    """按时间窗把 transcript 片段聚合成块。

    优先用章节/时间戳信息；单窗内若仍超长，再按 max_chars 细切。

    Args:
        segments: 字幕片段列表，每项含 {'start': float, 'duration': float, 'text': str}
        window_seconds: 时间窗大小（秒），默认 10 分钟
        max_chars: 单块字符软上限（用于时间窗内二次细分）

    Returns:
        块列表，每项 {'index': int, 'start': float, 'end': float, 'text': str}
    """
    if not segments:
        return []

    # 把片段按时间窗分组
    groups: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    window_start = segments[0].get("start", 0.0)
    for seg in segments:
        start = seg.get("start", 0.0)
        if current and (start - window_start) > window_seconds:
            groups.append(current)
            current = []
            window_start = start
        current.append(seg)
    if current:
        groups.append(current)

    chunks: List[Dict[str, Any]] = []
    index = 0
    for group in groups:
        text = "\n".join(s.get("text", "").strip() for s in group if s.get("text", "").strip())
        start = group[0].get("start", 0.0)
        end = group[-1].get("start", 0.0) + group[-1].get("duration", 0.0)
        if len(text) <= max_chars:
            chunks.append({"index": index, "start": start, "end": end, "text": text})
            index += 1
        else:
            # 时间窗内仍超长，再按字符窗细分
            for sub in chunk_text(text, max_chars=max_chars):
                chunks.append({"index": index, "start": start, "end": end, "text": sub})
                index += 1
    return chunks


def segments_to_text(segments: List[Dict[str, Any]], with_timestamps: bool = False) -> str:
    """把字幕片段拼成纯文本。

    Args:
        segments: 字幕片段列表
        with_timestamps: 是否带 [mm:ss] 时间戳前缀
    """
    lines = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        if with_timestamps:
            ts = _fmt_ts(seg.get("start", 0.0))
            lines.append(f"[{ts}] {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


def _fmt_ts(seconds: float) -> str:
    seconds = int(seconds or 0)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# 两段式总结编排
# ---------------------------------------------------------------------------

def two_stage_summarize(
    chunks: List[Dict[str, Any]],
    summarize_fn: Callable[[str, int, int], Optional[str]],
    merge_fn: Optional[Callable[[List[str]], Optional[str]]] = None,
    max_final_chunks: int = 8,
) -> Optional[str]:
    """两段式总结：逐块小结 → 二次合并。

    Args:
        chunks: chunk_segments / chunk_text 产出的块（dict 含 'text'，或纯 str）
        summarize_fn: 单块总结函数，签名 (text, index, total) -> str|None
        merge_fn: 合并函数，签名 (partials: List[str]) -> str|None；
                  不传则直接拼接各块小结
        max_final_chunks: 块数 ≤ 此值时走单段总结（不触发二次合并）

    Returns:
        最终总结文本；任意块失败返回 None
    """
    if not chunks:
        return None

    total = len(chunks)
    partials: List[str] = []
    for i, ch in enumerate(chunks):
        text = ch["text"] if isinstance(ch, dict) else str(ch)
        sub = summarize_fn(text, i, total)
        if not sub:
            # 任一关键块失败则整体失败（由上层降级处理）
            return None
        partials.append(sub.strip())

    if total == 1:
        # 单块无需合并
        return partials[0]

    if total <= max_final_chunks and merge_fn is None:
        # 块很少且无需二次合并：各块小结直接作为章节拼接
        return "\n\n---\n\n".join(partials)

    if merge_fn is not None:
        merged = merge_fn(partials)
        return merged

    # 无 merge_fn 但块较多：直接拼接各块小结
    return "\n\n---\n\n".join(partials)
