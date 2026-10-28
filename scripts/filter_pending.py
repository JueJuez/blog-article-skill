"""待总结队列过滤与派单刷新（现有入口）。

默认：清已总结项；仅对已知auto/manual且源可读的条目刷新派单。
auto重算type，manual保留；legacy不改，类型建议差异进入报告。
--dry-run：仅预览type/prompt，不运行去重、不写队列/登记表；包含已总结条目。
`NOTE_DISTILL_DISPATCH=1`：刷新后给「长源且已有有效提炼包」的条目追加材料包块（S9，默认关）。
"""
import json
import os
import sys
import time

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


def _read_source(e):
    path = e.get("raw_file") or e.get("output")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeError):
        return None


def type_difference(e):
    from prompts.classify import classify_note_type_with_evidence
    source = _read_source(e)
    if source is None:
        return None
    suggested = classify_note_type_with_evidence(e.get("title", ""), source)
    if suggested["note_type"] == e.get("note_type"):
        return None
    return {"title": e.get("title", ""), "url": e.get("url", ""),
            "type_origin": e.get("generation_context", {}).get("type_origin", "legacy"),
            "old_type": e.get("note_type"), "suggested_type": suggested["note_type"],
            "reason": suggested["reason"]}


def refresh_prompt(e: dict) -> str:
    """仅刷新已知auto/manual；legacy保持原样，建议通过type_difference只读报告。"""
    from prompts.provenance import dispatch_fields
    origin = e.get("generation_context", {}).get("type_origin", "legacy")
    if origin not in ("auto", "manual") or not e.get("note_type"):
        return "skipped"
    source = _read_source(e)
    if not source:
        return "skipped"
    fields = dispatch_fields(e.get("title", ""), source,
                             e["note_type"] if origin == "manual" else "", origin)
    changed = any(e.get(k) != v for k, v in fields.items())
    e.update(fields)
    return "refreshed" if changed else "kept"


def distill_prepare(e: dict) -> str:
    """S9 长源两阶段装配（`NOTE_DISTILL_DISPATCH=1` 才生效，默认关闭）。

    只有**已存在有效 pack** 的条目会被装配材料包块；短源/数值密集/无 pack/版本失配
    一律降级单轮（`PrepareIneligible`），不阻塞批次。
    必须排在 `refresh_prompt` 之后——刷新会重建 prompt 主体，先装配会被覆盖。

    **回退也可能改写条目**（§8.5 第 1 条）：源变短/变数字密集/源不可读时，
    `prepare_entry` 会把残留的旧材料包块剥掉，此时 `PrepareIneligible.stripped=True`
    → 返回 `stripped`，`_run_queue` 必须写回队列；**不能仅依赖 refresh_prompt 先行
    重建来掩盖**（那条路径只在 type/version 变化时重写 prompt）。
    """
    if os.environ.get("NOTE_DISTILL_DISPATCH") != "1":
        return "off"
    from shared import distill_dispatch as DD
    log = os.environ.get("NOTE_DISTILL_DISPATCH_LOG") or os.path.join(
        ROOT, "notes", "_meta", "distill_dispatch",
        "dispatch_%s.jsonl" % time.strftime("%Y%m%d"))
    try:
        rep = DD.prepare_entry(e, log_path=log)
    except DD.PrepareIneligible as exc:
        if exc.stripped:
            return "stripped"
        # 有 pack 但未完整覆盖（缺块/部分读取）：单独计数，别和「没有 pack」混成一个
        # fallback——前者要补块重提炼，后者多半只是源不适配两阶段。
        if str(exc.reason).startswith("incomplete_coverage"):
            return "incomplete"
        return "fallback"
    if rep["status"] == "prepared":
        return "prepared"
    return "stale" if rep["status"] == "stale_stripped" else "already"


def _run_queue(path: str, filt, refresh: bool = True, dry_run: bool = False) -> dict:
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
    # 只读预览不调用可能迁移旧登记表的去重读取入口。
    keep, dropped = (entries, []) if dry_run else filt(entries)
    differences = [d for e in keep if (d := type_difference(e))]
    refreshed = skipped = 0
    if refresh:
        for e in keep:
            r = refresh_prompt(e)
            if r == "refreshed":
                refreshed += 1
            elif r == "skipped":
                skipped += 1
    distill = {"prepared": 0, "fallback": 0, "already": 0, "stale": 0, "off": 0,
               "stripped": 0, "incomplete": 0}
    if refresh and not dry_run:
        for e in keep:
            r = distill_prepare(e)
            distill[r] = distill.get(r, 0) + 1
    # stripped 也要写回：条目里的失效材料包块被剥掉了，不落盘等于白剥
    if not dry_run and (dropped or refreshed or distill["prepared"] or distill["stale"]
                        or distill["stripped"]):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(keep, f, ensure_ascii=False, indent=2)
    return {"kept": len(keep), "dropped": len(dropped),
            "prompt_refreshed": refreshed, "prompt_skipped": skipped,
            "distill": distill,
            "type_differences": differences, "dry_run": dry_run,
            "titles": [e.get("title", "") for e in keep]}


def main(argv=None) -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只读报告，不清队列、不刷新派单")
    args = parser.parse_args(argv)
    result = {
        "monitors": _run_queue(PENDING_PATH, filter_monitors, dry_run=args.dry_run),
        "scys": _run_queue(SCYS_PENDING_PATH, filter_scys, dry_run=args.dry_run),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
