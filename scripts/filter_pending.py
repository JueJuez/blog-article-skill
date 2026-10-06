"""scripts/filter_pending.py — 待总结队列前置过滤器（三层去重第②层 · DECISION-20260825）。

派子 Agent 总结**之前**跑一次，机械清掉「已无需总结」的条目，避免已总结内容
再消耗 AI 总结 token / 重复落飞书。写不写由代码决定，AI 只交总结。

用法：
    python scripts/filter_pending.py

规则（纯代码判断）：
- monitors/pending_summaries.json：URL 命中 dedup 索引（已总结过）→ 出队
- notes/_scraped/scys/pending_summaries.json：summarized:true 或 URL 命中 dedup 索引 → 出队
- **prompt 刷新（默认开，2026-10-06）**：长跑补齐进程启动时加载的模板可能落后于
  仓库当前模板（实例：UP 补齐 16:56 启动，错过 18:46 质量纪律提交）。入队时预计算
  的 prompt 在消费前按当前模板机械重算（`get_note_prompt(note_type, 源长)` +
  `QUALITY_GATE_SELFCHECK`，源长取 raw_file / chars），保证老条目也用新版规范。
  条目缺 note_type 或源长不可得 → 跳过保留原 prompt（不猜）。

输出 JSON 摘要（kept/dropped/prompt_refreshed/剩余标题），编排方只对剩余条目派子 Agent。
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


def refresh_prompt(e: dict) -> str:
    """按当前模板重算单条 prompt；返回 'refreshed' / 'kept' / 'skipped'。

    与三个入队写点（monitors/_queue_pending_summary、scys build_pending_entry、
    fetch_up_range）同一口径：get_note_prompt(note_type, 源长) + QUALITY_GATE_SELFCHECK。
    源长来源：monitors/UP 队列读 raw_file；scys 队列用入队时存的 chars。
    """
    note_type = e.get("note_type") or ""
    if not note_type:
        return "skipped"
    from articles.prompt import get_note_prompt, source_chars_of
    from prompts.templates import QUALITY_GATE_SELFCHECK
    raw_file = e.get("raw_file") or ""
    if raw_file:
        if not os.path.exists(raw_file):
            return "skipped"
        with open(raw_file, encoding="utf-8") as f:
            chars = source_chars_of(f.read())
    else:
        try:
            chars = int(e.get("chars") or 0)
        except (TypeError, ValueError):
            chars = 0
        if chars <= 0:
            return "skipped"
    new_prompt = get_note_prompt(note_type, chars) + QUALITY_GATE_SELFCHECK
    if new_prompt != e.get("prompt"):
        e["prompt"] = new_prompt
        return "refreshed"
    return "kept"


def _run_queue(path: str, filt, refresh: bool = True) -> dict:
    if not os.path.exists(path):
        return {"kept": 0, "dropped": 0, "prompt_refreshed": 0, "prompt_skipped": 0,
                "titles": []}
    try:
        entries = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {"kept": 0, "dropped": 0, "prompt_refreshed": 0, "prompt_skipped": 0,
                "titles": []}
    if not isinstance(entries, list):
        return {"kept": 0, "dropped": 0, "prompt_refreshed": 0, "prompt_skipped": 0,
                "titles": []}
    keep, dropped = filt(entries)
    refreshed = skipped = 0
    if refresh:
        for e in keep:
            r = refresh_prompt(e)
            if r == "refreshed":
                refreshed += 1
            elif r == "skipped":
                skipped += 1
    if dropped or refreshed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(keep, f, ensure_ascii=False, indent=2)
    return {"kept": len(keep), "dropped": len(dropped),
            "prompt_refreshed": refreshed, "prompt_skipped": skipped,
            "titles": [e.get("title", "") for e in keep]}


def main() -> None:
    result = {
        "monitors": _run_queue(PENDING_PATH, filter_monitors),
        "scys": _run_queue(SCYS_PENDING_PATH, filter_scys),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
