"""机械质量门禁拦截台账：三条归档链路的统一拦截事件持久化。

- 接线点 1：articles/main.py save_summary_only（单篇队列/文章路径）
- 接线点 2：videos/main.py _save_series_note（系列课 apply + 直连共用）
- 落点：monitors/gate_blockers.YYYYMMDD.jsonl（JSONL 单行事件，按天滚动，
  LOG_KEEP_DAYS 默认 7 天惰性清理）
- 重试放行（2026-09-10）：count_blocks 按 URL 统计历史拦截次数——同 URL
  首次字数违规拦截让模型重改，再次仍纯字数违规则放行落盘（压缩已到头）。
- 记录失败绝不抛异常：台账是观测辅助，不能影响落盘主流程。
"""

import glob
import json
import os
import time
from typing import Optional

from shared.rolling_log import append_rolling

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE_BLOCKERS_BASE = os.path.join(_PROJECT_ROOT, "monitors", "gate_blockers.jsonl")


def log_gate_block(source: str, note_type: str, url: str, title: str,
                   issues: list, warnings: Optional[list] = None,
                   action: str = "blocked") -> Optional[str]:
    """记录一次机械门禁拦截/放行事件，返回写入路径；失败返回 None。

    action="blocked"（默认，拦截）/ "bypassed_retry"（字数重试放行）。
    """
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": source,
        "note_type": note_type,
        "url": url,
        "title": title,
        "issues": list(issues),
        "warnings": list(warnings or []),
        "action": action,
    }
    try:
        return append_rolling(GATE_BLOCKERS_BASE,
                              json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        return None


def count_blocks(url: str) -> int:
    """统计台账中同 URL 的历史拦截次数（跨全部滚动文件），坏行跳过、异常返 0。

    重试放行依据：返回 >=1 表示该 URL 已被拦过一次（模型已重改过仍违规）。
    台账滚动文件默认 7 天过期，重试窗口与之对齐——隔久重遇同 URL 会再拦一次，
    属预期行为（留观而不静默放行陈旧内容）。
    """
    if not url:
        return 0
    total = 0
    try:
        pattern = os.path.join(os.path.dirname(GATE_BLOCKERS_BASE),
                               "gate_blockers*.jsonl")
        for path in glob.glob(pattern):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        if json.loads(line).get("url") == url:
                            total += 1
                    except Exception:
                        continue
    except Exception:
        return 0
    return total
