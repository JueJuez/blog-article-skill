"""按天滚动的追加日志 + 惰性过期清理（全项目日志防膨胀统一机制）。

命名规则：base_path 扩展名前插入日期后缀，如 foo.log -> foo.20260907.log、
foo.jsonl -> foo.20260907.jsonl。写入当天文件前自动清理同前缀下 mtime 超过
LOG_KEEP_DAYS（env，默认 7 天）的过期日期文件；仅删除文件名内嵌合法日期
的文件，避免误删同名模式的其他产物。
"""

import glob
import os
import re
import time
from datetime import datetime
from typing import Optional

LOG_KEEP_DAYS = int(os.environ.get("LOG_KEEP_DAYS", "7"))

_DATE_IN_NAME_RE = re.compile(r"\.(\d{8})\.")


def _split_base(base_path: str) -> tuple:
    """拆 base_path 为 (目录, 词干, 扩展名)；无扩展名时扩展名返回空串。"""
    directory, filename = os.path.split(base_path)
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        return directory, filename, ""
    return directory, stem, ext


def _is_valid_daily_name(filename: str) -> bool:
    """文件名形如 <stem>.YYYYMMDD.<ext> 且日期合法才允许清理。"""
    m = _DATE_IN_NAME_RE.search(filename)
    if not m:
        return False
    try:
        datetime.strptime(m.group(1), "%Y%m%d")
    except ValueError:
        return False
    return True


def rolling_log_path(base_path: str, ts: Optional[datetime] = None) -> str:
    """返回 base_path 的当天滚动文件路径。"""
    ts = ts or datetime.now()
    directory, stem, ext = _split_base(base_path)
    name = f"{stem}.{ts.strftime('%Y%m%d')}" + (f".{ext}" if ext else "")
    return os.path.join(directory, name)


def cleanup_expired(base_path: str, keep_days: Optional[int] = None,
                    now: Optional[float] = None) -> int:
    """删除同前缀滚动文件中 mtime 超过 keep_days 天的旧文件，返回删除数。

    目录不存在返回 0；单个文件删除失败跳过不影响其余文件。
    """
    directory, stem, ext = _split_base(base_path)
    if not directory or not os.path.isdir(directory):
        return 0
    keep = LOG_KEEP_DAYS if keep_days is None else keep_days
    cutoff = (now if now is not None else time.time()) - keep * 86400
    pattern = os.path.join(directory, f"{stem}.*" + (f".{ext}" if ext else ""))
    removed = 0
    for path in glob.glob(pattern):
        try:
            if not _is_valid_daily_name(os.path.basename(path)):
                continue
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed


def append_rolling(base_path: str, text: str, keep_days: Optional[int] = None) -> str:
    """追加写当天滚动文件，写入前顺手清理过期文件，返回实际写入路径。"""
    path = rolling_log_path(base_path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        cleanup_expired(base_path, keep_days=keep_days)
    except Exception:
        pass
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)
    return path
