"""shared/sanitize.py — 文件名安全化（单一真源）。

系列课笔记文件名依赖 base 字符串稳定（「第NN集_标题」装饰前缀由调用方拼接）。
故 sanitize 必须全项目只用这一份实现，任何地方改了都要同步，否则同名文件互相覆盖。
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
