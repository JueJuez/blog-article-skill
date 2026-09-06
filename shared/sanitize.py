"""shared/sanitize.py — 文件名安全化（单一真源）。

系列课去重依赖 base 字符串精确匹配（抓取层 mark_fetched / 总结层 mark_done /
series_state.get_pending 都用同一 base）。故 sanitize 必须全项目只用这一份实现，
任何地方改了都要同步，否则去重失效、整季重抓。
"""
import re

_INVALID = re.compile(r'[\\/:*?"<>|\n\r\t]')
_WS = re.compile(r'\s+')


def sanitize_filename(name: str) -> str:
    if not name:
        return "未命名"
    s = _INVALID.sub('_', name).strip()
    s = _WS.sub(' ', s)
    return s[:80] or "未命名"
