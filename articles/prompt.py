"""文章笔记 Prompt 模块（兼容层）

实际模板来自 prompts 共享模块，这里只做再导出，保证旧调用
`from articles.prompt import CONTENT_SUMMARY_PROMPT` 继续可用，
同时暴露新的 note_type 相关接口。
"""
from prompts.templates import (
    CONTENT_SUMMARY_PROMPT,
    KEY_POINTS_PROMPT,
    NOTE_TEMPLATES,
    get_note_prompt,
    list_note_types,
    format_note_with_prompt,
)
from prompts.classify import classify_note_type


def get_content_summary_prompt():
    """获取结构化总结 Prompt（兼容旧调用）"""
    return CONTENT_SUMMARY_PROMPT


__all__ = [
    "CONTENT_SUMMARY_PROMPT",
    "KEY_POINTS_PROMPT",
    "NOTE_TEMPLATES",
    "get_note_prompt",
    "get_content_summary_prompt",
    "list_note_types",
    "format_note_with_prompt",
    "classify_note_type",
]
