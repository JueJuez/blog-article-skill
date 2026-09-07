"""机械质量门禁拦截台账：三条归档链路的统一拦截事件持久化。

- 接线点 1：articles/main.py save_summary_only（单篇队列/文章路径）
- 接线点 2：videos/main.py _save_series_note（系列课 apply + 直连共用）
- 落点：monitors/gate_blockers.YYYYMMDD.jsonl（JSONL 单行事件，按天滚动，
  LOG_KEEP_DAYS 默认 7 天惰性清理）
- 记录失败绝不抛异常：台账是观测辅助，不能影响落盘主流程。
"""

import json
import os
import time
from typing import Optional

from shared.rolling_log import append_rolling

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE_BLOCKERS_BASE = os.path.join(_PROJECT_ROOT, "monitors", "gate_blockers.jsonl")


def log_gate_block(source: str, note_type: str, url: str, title: str,
                   issues: list, warnings: Optional[list] = None) -> Optional[str]:
    """记录一次机械门禁拦截事件，返回写入路径；失败返回 None。"""
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": source,
        "note_type": note_type,
        "url": url,
        "title": title,
        "issues": list(issues),
        "warnings": list(warnings or []),
    }
    try:
        return append_rolling(GATE_BLOCKERS_BASE,
                              json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        return None
