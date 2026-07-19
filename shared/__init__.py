"""shared — 跨模块基础能力

被 articles 与 videos 复用：
- chunking: 预处理/分块模块（C1），任意长度 transcript/playlist → 标准长度小块
- wb_ai:    可选 WorkBuddy 内置 AI 适配层（C2），best-effort 调用，绝不阻断主流程
"""
from .chunking import (
    chunk_text,
    chunk_segments,
    segments_to_text,
    two_stage_summarize,
)
from .wb_ai import (
    register_wb_ai,
    clear_wb_ai,
    call_wb_ai,
)

__all__ = [
    "chunk_text",
    "chunk_segments",
    "segments_to_text",
    "two_stage_summarize",
    "register_wb_ai",
    "clear_wb_ai",
    "call_wb_ai",
]
