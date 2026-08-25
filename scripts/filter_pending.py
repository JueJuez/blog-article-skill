"""scripts/filter_pending.py — 待总结队列前置过滤器（三层去重第②层 · DECISION-20260825）。

派子 Agent 总结**之前**跑一次，机械清掉「已无需总结」的条目，避免已总结内容
再消耗 AI 总结 token / 重复落飞书。写不写由代码决定，AI 只交总结。

用法：
    python scripts/filter_pending.py

规则（纯代码判断）：
- monitors/pending_summaries.json：URL 命中 dedup 索引（已总结过）→ 出队
- notes/_scraped/scys/pending_summaries.json：summarized:true 或 URL 命中 dedup 索引 → 出队

输出 JSON 摘要（kept/dropped/剩余标题），编排方只对剩余条目派子 Agent。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PENDING_PATH = os.path.join(ROOT, "monitors", "pending_summaries.json")
SCYS_PENDING_PATH = os.path.join(ROOT, "notes", "_scraped", "scys", "pending_summaries.json")


def filter_monitors(entries: list) -> tuple:
    from articles import dedup
    keep, dropped = [], []
    for e in entries:
        url = e.get("url", "")
        if url and dedup.is_summarized(url=url):
            dropped.append(e)
        else:
            keep.append(e)
    return keep, dropped


def filter_scys(entries: list) -> tuple:
    from articles import dedup
    keep, dropped = [], []
    for e in entries:
        url = e.get("url", "")
        if e.get("summarized") or (url and dedup.is_summarized(url=url)):
            dropped.append(e)
        else:
            keep.append(e)
    return keep, dropped


def _run_queue(path: str, filt) -> dict:
    if not os.path.exists(path):
        return {"kept": 0, "dropped": 0, "titles": []}
    try:
        entries = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {"kept": 0, "dropped": 0, "titles": []}
    if not isinstance(entries, list):
        return {"kept": 0, "dropped": 0, "titles": []}
    keep, dropped = filt(entries)
    if dropped:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(keep, f, ensure_ascii=False, indent=2)
    return {"kept": len(keep), "dropped": len(dropped),
            "titles": [e.get("title", "") for e in keep]}


def main() -> None:
    result = {
        "monitors": _run_queue(PENDING_PATH, filter_monitors),
        "scys": _run_queue(SCYS_PENDING_PATH, filter_scys),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
