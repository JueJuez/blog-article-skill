"""prompts — 共享笔记模板模块（被 articles 与 videos 复用）

提供：
- NOTE_TEMPLATES: 笔记类型注册表（结构化复盘 / 要点提炼 ...）
- get_note_prompt(note_type): 按类型取模板
- list_note_types(): 列出所有可用类型
- classify_note_type(title, content): 按标题/内容自动判定类型
- format_note_with_prompt(...): 给笔记加元数据头（标签/作者/来源）
"""
from .templates import (
    NOTE_TEMPLATES,
    CONTENT_SUMMARY_PROMPT,
    KEY_POINTS_PROMPT,
    get_note_prompt,
    list_note_types,
    format_note_with_prompt,
)
from .classify import classify_note_type

__all__ = [
    "NOTE_TEMPLATES",
    "CONTENT_SUMMARY_PROMPT",
    "KEY_POINTS_PROMPT",
    "get_note_prompt",
    "list_note_types",
    "format_note_with_prompt",
    "classify_note_type",
]
