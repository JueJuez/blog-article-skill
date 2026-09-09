"""消费 migrate_gate v3 重抓队列（PLAN-20260908 阶段 5，D5）。

四步闭环：
  1. --plan        统计队列（total / kinds / registered / to_fetch）
  2. --fetch       逐条抓取未登记条目 → 降级产物入 staging（folder=folder_hint 落同目录，
                   prompt 预计算）；B站重传标题失配转 manual（风险 4）；412 熔断停轮
  3. --clean       机械清洗 staging：已总结移 done、raw 缺失/过短回重抓态（fails+1）
  4. --cleanup-old v3 铁律：新文已登记才删旧文（默认 dry-run，--apply 才真删）

断点续跑：registry（url + old_titles 双键）命中、staging/manual 已有 url、同条失败
3 次转人工（D8）全部自动跳过，可反复执行直至队列清空。manual_no_url 条目直接进
人工清单，等待人工补链接后重跑。
"""
import argparse
import difflib
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional, Union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from articles import dedup  # noqa: E402
from prompts.classify import classify_note_type  # noqa: E402
from prompts.templates import (  # noqa: E402
    NOTE_TEMPLATES,
    QUALITY_GATE_SELFCHECK,
    get_note_prompt,
)

STAGING_PATH = ROOT / "notes" / "_scraped" / "migrate" / "pending_summaries.json"
DONE_PATH = ROOT / "notes" / "_scraped" / "migrate" / "done_queue.json"
STATE_PATH = ROOT / "notes" / "_scraped" / "migrate" / "consume_state.json"
MANUAL_PATH = ROOT / "notes" / "_scraped" / "migrate" / "manual_queue.json"

TITLE_MATCH_RATIO = 0.55
RAW_MIN_CHARS = 200
FAIL_LIMIT = 3
RISK_412_MARKERS = ("412", "Precondition Failed")

_sleep = time.sleep


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path: Union[str, Path], default: Any) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Union[str, Path], data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def latest_scan(archive_dir: Union[str, Path]) -> Optional[str]:
    """取 archive 目录下文件名排序最新的 migrate_scan_*.json；目录/文件缺失返回 None。"""
    p = Path(archive_dir)
    if not p.is_dir():
        return None
    scans = sorted(p.glob("migrate_scan_*.json"))
    return str(scans[-1]) if scans else None


def load_queue(scan_path: Union[str, Path]) -> list:
    with open(scan_path, encoding="utf-8") as f:
        return json.load(f).get("queue", [])


def _registry_hits(entries: list) -> set:
    """批量查登记表；返回命中原文集合（url 或 old_title 字符串本身）。"""
    urls = [e.get("url", "") for e in entries if e.get("url")]
    titles = [t for e in entries for t in e.get("old_titles", []) if t]
    if not urls and not titles:
        return set()
    return set(dedup.batch_is_summarized(urls, titles))


def _is_registered(entry: dict, hits: set) -> bool:
    if entry.get("url") and entry["url"] in hits:
        return True
    return any(t in hits for t in entry.get("old_titles", []))


def _title_matched(fetched_title: str, old_titles: list) -> bool:
    """风险 4：B站重传后 URL 会变，用 old_titles 相似度确认抓回的是同一视频。"""
    if not old_titles:
        return True
    return any(
        difflib.SequenceMatcher(None, fetched_title or "", t).ratio() >= TITLE_MATCH_RATIO
        for t in old_titles
    )


def _build_staging_entry(entry: dict, res: dict) -> dict:
    """构造 staging 条目：folder=folder_hint 优先（v3 重抓产物落同目录），prompt 预计算。"""
    note_type = res.get("note_type", "")
    prompt = res.get("prompt", "")
    if note_type not in NOTE_TEMPLATES:
        note_type = classify_note_type(
            res.get("original_title", ""), res.get("article_content", "") or res.get("transcript", ""))
        prompt = get_note_prompt(note_type) + QUALITY_GATE_SELFCHECK
    return {
        "url": entry.get("url", ""),
        "title": res.get("original_title", ""),
        "author": res.get("author", ""),
        "note_type": note_type,
        "tags": res.get("tags", []),
        "publish_time": res.get("publish_time", 0),
        "folder": entry.get("folder_hint") or res.get("folder", ""),
        "raw_file": res.get("raw_file", ""),
        "prompt": prompt,
        "queued_at": _now(),
        "kind": entry.get("kind", "article"),
        "old_paths": entry.get("old_paths", []),
        "old_titles": entry.get("old_titles", []),
    }


def cmd_plan(scan_path: Union[str, Path]) -> dict:
    queue = load_queue(scan_path)
    hits = _registry_hits(queue)
    registered = sum(1 for e in queue if _is_registered(e, hits))
    to_fetch = sum(
        1 for e in queue if not _is_registered(e, hits) and e.get("kind") != "manual_no_url")
    return {
        "total": len(queue),
        "kinds": dict(Counter(e.get("kind", "?") for e in queue)),
        "registered": registered,
        "to_fetch": to_fetch,
    }


def _to_manual(manual: list, manual_urls: set, entry: dict, reason: str, result: dict, **extra) -> None:
    item = {**entry, "reason": reason, "added_at": _now(), **extra}
    manual.append(item)
    if entry.get("url"):
        manual_urls.add(entry["url"])
    result["to_manual"] += 1


def cmd_fetch(scan_path: Union[str, Path], limit: int = 15, sleep_s: float = 8.0) -> dict:
    """逐条抓取未登记条目；降级产物入 staging，失败/失配进 manual，412 熔断停轮。"""
    queue = load_queue(scan_path)
    staging = _read_json(STAGING_PATH, [])
    manual = _read_json(MANUAL_PATH, [])
    state = _read_json(STATE_PATH, {})
    hits = _registry_hits(queue)
    staged_urls = {e.get("url", "") for e in staging if e.get("url")}
    manual_urls = {e.get("url", "") for e in manual if e.get("url")}
    result = {"enqueued": 0, "to_manual": 0, "aborted": False}
    processed = 0

    for entry in queue:
        url = entry.get("url", "")
        if _is_registered(entry, hits):
            continue
        if url and url in staged_urls:
            continue
        if url and url in manual_urls:
            continue
        if url and state.get(url, {}).get("fails", 0) >= FAIL_LIMIT:
            continue
        if entry.get("kind") == "manual_no_url" or not url:
            _to_manual(manual, manual_urls, entry, "no_url", result)
            continue
        if processed >= limit:
            break
        processed += 1
        _sleep(sleep_s)
        if entry.get("kind") == "bili_video":
            res = _fetch_video(url, entry.get("folder_hint", ""))
        else:
            res = _fetch_article(url, entry.get("folder_hint", ""))

        if not res.get("success"):
            msg = str(res.get("message", ""))
            if any(m in msg for m in RISK_412_MARKERS):
                result["aborted"] = True
                break
            rec = state.setdefault(url, {})
            rec["fails"] = rec.get("fails", 0) + 1
            rec["last_error"] = msg
            rec["ts"] = _now()
            if rec["fails"] >= FAIL_LIMIT:
                _to_manual(manual, manual_urls, entry, "fail_3", result, last_error=msg)
            continue
        if not res.get("need_continue_summary"):
            continue  # 管线已自动保存并登记，无需入队
        if entry.get("kind") == "bili_video" and not _title_matched(
                res.get("original_title", ""), entry.get("old_titles", [])):
            _to_manual(manual, manual_urls, entry, "url_changed", result,
                       fetched_title=res.get("original_title", ""))
            continue
        staging.append(_build_staging_entry(entry, res))
        staged_urls.add(url)
        result["enqueued"] += 1

    _write_json(STAGING_PATH, staging)
    _write_json(MANUAL_PATH, manual)
    _write_json(STATE_PATH, state)
    return result


def cmd_clean() -> dict:
    """机械清洗 staging：已登记移 done；raw 缺失/过短出队回重抓态（fails+1）；其余保留。"""
    staging = _read_json(STAGING_PATH, [])
    done = _read_json(DONE_PATH, [])
    state = _read_json(STATE_PATH, {})
    hits = _registry_hits(staging)
    result = {"done": 0, "refetch": 0, "kept": 0}
    keep = []
    for entry in staging:
        if _is_registered(entry, hits):
            done.append(entry)
            result["done"] += 1
            continue
        raw = entry.get("raw_file", "")
        ok = False
        if raw and os.path.isfile(raw):
            try:
                with open(raw, encoding="utf-8", errors="ignore") as f:
                    ok = len(f.read()) >= RAW_MIN_CHARS
            except OSError:
                ok = False
        if ok:
            keep.append(entry)
            result["kept"] += 1
            continue
        url = entry.get("url", "")
        if url:
            rec = state.setdefault(url, {})
            rec["fails"] = rec.get("fails", 0) + 1
            rec["last_error"] = "raw_missing_or_short"
            rec["ts"] = _now()
        result["refetch"] += 1
    _write_json(STAGING_PATH, keep)
    _write_json(DONE_PATH, done)
    _write_json(STATE_PATH, state)
    return result


def cmd_cleanup_old(apply: bool = False) -> dict:
    """v3 铁律：仅当重抓新文已登记（url 或 old_titles 命中）才删旧文；默认 dry-run。"""
    done = _read_json(DONE_PATH, [])
    staging = _read_json(STAGING_PATH, [])
    hits = _registry_hits(done + staging)
    result = {"would_delete": 0, "deleted": 0, "kept": 0}

    def _process(entry: dict, is_done: bool) -> Optional[dict]:
        paths = [p for p in entry.get("old_paths", []) if p and os.path.isfile(p)]
        if not _is_registered(entry, hits):
            result["kept"] += 1
            return entry
        result["would_delete"] += len(paths)
        if apply:
            for p in paths:
                try:
                    os.remove(p)
                    result["deleted"] += 1
                except OSError:
                    pass
        if is_done and all(not (p and os.path.isfile(p)) for p in entry.get("old_paths", [])):
            return None  # 旧文已全部清理，done 条目完成使命
        return entry

    new_done = [e for e in (_process(e, True) for e in done) if e is not None]
    new_staging = [e for e in (_process(e, False) for e in staging) if e is not None]
    _write_json(DONE_PATH, new_done)
    _write_json(STAGING_PATH, new_staging)
    return result


def _fetch_article(url: str, folder: str) -> dict:
    """article 走 articles 管线（显式 folder 跳过 L7 路由，落 folder_hint 同目录）。"""
    from articles.main import skill_main
    return skill_main({"content": url, "folder": folder})


def _fetch_video(url: str, folder: str) -> dict:
    """bili_video 走 videos 管线（入参无 folder，落盘目录由子 Agent 保存时经 --folder 兑现）。"""
    from videos.main import summarize_video
    return summarize_video({"url": url})


def _default_archive_dir() -> str:
    vault = os.environ.get("OBSIDIAN_VAULT_PATH", "")
    if vault:
        return str(Path(vault).parent / "_migrate_gate_archive")
    return str(ROOT / "_migrate_gate_archive")


def main() -> None:
    ap = argparse.ArgumentParser(description="消费 migrate_gate v3 重抓队列（阶段 5）")
    ap.add_argument("--plan", action="store_true", help="只统计不抓取")
    ap.add_argument("--fetch", action="store_true", help="抓取未登记条目入 staging/manual")
    ap.add_argument("--clean", action="store_true", help="清洗 staging（已总结移 done / raw 缺失回重抓）")
    ap.add_argument("--cleanup-old", action="store_true", help="删除已登记重抓条目的旧文（默认 dry-run）")
    ap.add_argument("--apply", action="store_true", help="配合 --cleanup-old 真正删除旧文")
    ap.add_argument("--scan", default="", help="重抓队列 JSON 路径（默认取 archive 目录最新）")
    ap.add_argument("--limit", type=int, default=15, help="本轮最多抓取条数")
    ap.add_argument("--sleep", type=float, default=8.0, help="两次抓取间隔秒")
    ap.add_argument("--archive-dir", default="", help="scan 存放目录（默认 vault 同级 _migrate_gate_archive）")
    args = ap.parse_args()

    archive_dir = args.archive_dir or _default_archive_dir()
    if args.plan:
        scan = args.scan or latest_scan(archive_dir)
        if not scan:
            print("未找到重抓队列 scan，请用 --scan 指定")
            return
        print(json.dumps(cmd_plan(scan), ensure_ascii=False, indent=1))
        return
    if args.fetch:
        scan = args.scan or latest_scan(archive_dir)
        if not scan:
            print("未找到重抓队列 scan，请用 --scan 指定")
            return
        print(json.dumps(cmd_fetch(scan, limit=args.limit, sleep_s=args.sleep), ensure_ascii=False, indent=1))
        return
    if args.clean:
        print(json.dumps(cmd_clean(), ensure_ascii=False, indent=1))
        return
    if args.cleanup_old:
        print(json.dumps(cmd_cleanup_old(apply=args.apply), ensure_ascii=False, indent=1))
        return
    ap.print_help()


if __name__ == "__main__":
    main()
