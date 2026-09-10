"""消费 migrate_gate v3 重抓队列（PLAN-20260908 阶段 5，D5）。

主循环四步：
  1. --plan        统计队列（total / kinds / registered / deferred / to_fetch）
  2. --fetch       逐条抓取未登记条目 → 降级产物入 staging（folder=folder_hint 落同目录，
                   prompt 预计算）；B站重传标题失配转 manual（风险 4）；412 熔断停轮；
                   无 CC 字幕条目暂缓（deferred）不重试、不占 limit 配额
  3. --clean       机械清洗 staging：已总结移 done、raw 缺失/过短回重抓态（fails+1）
  4. --cleanup-old v3 铁律：新文已登记才删旧文（默认 dry-run，--apply 才真删）

无 CC 暂缓维护（2026-09-10 加，见 DECISION-20260910-nocc-deferred）：
  5. --defer-failed       把 state 里带无CC指纹的历史失败批量暂缓（幂等）
  6. --classify-deferred  deferred 三分类（默认 dry-run，--apply 落盘）：view API 先行
                          （连续 2 次请求层异常=风控熔断 abort；删除码 → removed_video
                          提前定档）→ 环境健康再直调 ASR 转写（v3 2026-09-10：deferred
                          入场即「字幕层上次已确认空」，跳过字幕层不重复问已知答案，
                          ASR 出文本=误标回队 / None=no_cc_confirmed；间隔 60-180s 随机）

断点续跑：registry（url + old_titles 双键）命中、staging/manual 已有 url、deferred、
同条失败 3 次转人工（D8）全部自动跳过，可反复执行直至队列清空。manual_no_url 条目
直接进人工清单，等待人工补链接后重跑。
"""
import argparse
import difflib
import json
import os
import random
import re
import sys
import time
import urllib.request
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

try:  # 字幕探测（--fetch 无CC暂缓用）；导入失败时探测降级为不可用，走原管线
    from videos.fetch import fetch_transcript as _fetch_transcript_probe
except Exception:  # pragma: no cover
    _fetch_transcript_probe = None

try:  # ASR 直转写（--classify-deferred 终局裁决用）；导入失败时 classify 全批 probe_error
    from videos.asr import check_asr_deps as _check_asr_deps
    from videos.asr import transcribe_video as _asr_probe
except Exception:  # pragma: no cover
    _asr_probe = None
    _check_asr_deps = None

STAGING_PATH = ROOT / "notes" / "_scraped" / "migrate" / "pending_summaries.json"
DONE_PATH = ROOT / "notes" / "_scraped" / "migrate" / "done_queue.json"
STATE_PATH = ROOT / "notes" / "_scraped" / "migrate" / "consume_state.json"
MANUAL_PATH = ROOT / "notes" / "_scraped" / "migrate" / "manual_queue.json"

TITLE_MATCH_RATIO = 0.55
RAW_MIN_CHARS = 200
FAIL_LIMIT = 3
RISK_412_MARKERS = ("412", "Precondition Failed")
NO_CC_MARKERS = ("无可用字幕", "no_cc")
REMOVED_CODES = (-404, 62002)  # B站 view API：-404=不存在（删除），62002=稿件不可见
# classify 探测间隔随机区间（秒）：ASR 音频下载是重请求，且 Whisper 转写每条需数分钟，
# 拉长间隔摊在处理时间里不伤吞吐但降请求密度（2026-09-10 用户定版 8-15 → 60-120 → 60-180）
CLASSIFY_SLEEP_RANGE = (60.0, 180.0)

_sleep = time.sleep


def _is_no_cc_error(msg: str) -> bool:
    """无CC指纹：字幕缺失且 ASR 兜底失败（或探测标记），此类条目暂缓不重试。"""
    return any(m in msg for m in NO_CC_MARKERS)


def _bili_view_code(url: str) -> Optional[int]:
    """B站 view API 返回 code（0=正常，-404/62002=删除/不可见）；请求层异常返回 None。

    独立轻请求信号源（v2 熔断盲点修复）：fetch_transcript 内部吞异常返回 None，
    412 永远到不了外层熔断（2026-09-10 dry-run 实测盲点）——view API 同源 IP，
    连续 None 视为 IP 级风控/网络异常信号，由 risk_streak>=2 熔断裁决。
    BV 抽取失败返回 0（非 B站链接视为环境健康，交字幕探测权威裁决）。
    自包含实现：不复用 videos.fetch 私有函数（asr.py 断裂教训）。
    """
    m = re.search(r"BV[0-9A-Za-z]{10}", url or "")
    if not m:
        return 0
    api = f"https://api.bilibili.com/x/web-interface/view?bvid={m.group(0)}"
    try:
        req = urllib.request.Request(api, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com/",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return int(data.get("code", 0))
    except Exception:  # 网络/风控异常：None = 信号缺失（非删除定档），交 risk_streak 熔断
        return None


def _defer_entry(state: dict, url: str, reason: str) -> bool:
    """标记条目暂缓（deferred=True），返回是否为新标记。"""
    rec = state.setdefault(url, {})
    if rec.get("deferred"):
        return False
    rec["deferred"] = True
    rec["last_error"] = reason
    rec["ts"] = _now()
    return True


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
    state = _read_json(STATE_PATH, {})
    registered = sum(1 for e in queue if _is_registered(e, hits))
    deferred = sum(
        1 for e in queue
        if e.get("url") and state.get(e["url"], {}).get("deferred") and not _is_registered(e, hits))
    to_fetch = sum(
        1 for e in queue if not _is_registered(e, hits)
        and e.get("kind") != "manual_no_url"
        and not (e.get("url") and state.get(e["url"], {}).get("deferred")))
    return {
        "total": len(queue),
        "kinds": dict(Counter(e.get("kind", "?") for e in queue)),
        "registered": registered,
        "deferred": deferred,
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
    result = {"enqueued": 0, "to_manual": 0, "deferred": 0, "aborted": False}
    processed = 0

    for entry in queue:
        url = entry.get("url", "")
        if _is_registered(entry, hits):
            continue
        if url and url in staged_urls:
            continue
        if url and url in manual_urls:
            continue
        if url and state.get(url, {}).get("deferred"):
            continue  # 无CC暂缓条目：最后统一处理，不占 limit 配额
        if url and state.get(url, {}).get("fails", 0) >= FAIL_LIMIT:
            continue
        if entry.get("kind") == "manual_no_url" or not url:
            _to_manual(manual, manual_urls, entry, "no_url", result)
            continue
        probed = False
        if entry.get("kind") == "bili_video" and _fetch_transcript_probe is not None:
            probed = True  # 探测暂缓不占 limit 配额，不让无CC视频拖累本轮进度
            _sleep(sleep_s)
            try:
                probe = _fetch_transcript_probe(url)
            except Exception:
                probe = "probe_error"  # 探测异常不标记，交原管线正常处理
            if probe is None:
                if _defer_entry(state, url, "no_cc_transcript"):
                    result["deferred"] += 1
                continue
        if processed >= limit:
            break
        processed += 1
        if not probed:
            _sleep(sleep_s)  # 探测分支已限速，此处仅补 article/无探测路径
        if entry.get("kind") == "bili_video":
            res = _fetch_video(url, entry.get("folder_hint", ""))
        else:
            res = _fetch_article(url, entry.get("folder_hint", ""))

        if not res.get("success"):
            msg = str(res.get("message", ""))
            if any(m in msg for m in RISK_412_MARKERS):
                result["aborted"] = True
                break
            if _is_no_cc_error(msg):
                if _defer_entry(state, url, msg):
                    result["deferred"] += 1
                continue
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


def cmd_defer_failed() -> dict:
    """把 state 里带无CC指纹的历史失败记录批量暂缓（幂等，只统计新标记数）。"""
    state = _read_json(STATE_PATH, {})
    n = 0
    for url, rec in state.items():
        if _is_no_cc_error(str(rec.get("last_error", ""))) and _defer_entry(state, url, str(rec.get("last_error", ""))):
            n += 1
    _write_json(STATE_PATH, state)
    return {"deferred": n}


def cmd_classify_deferred(apply: bool = False) -> dict:
    """deferred 三分类：真无CC确认 / 视频删除定档 / 误标回队（默认 dry-run）。

    v2（2026-09-10 熔断盲点修复）：探测前先调 view API（独立轻请求信号源）：
    - view 请求层异常（None）→ risk_streak+1，连续 2 次 abort（下载层异常被
      asr 内部吞掉，412 到不了外层熔断——dry-run 实测盲点，v1 全漏判）
    - view code ∈ REMOVED_CODES → removed_video 定档（跳过 ASR 省重请求）
    v3（2026-09-10，用户方案）：deferred 入场语义即「字幕层上次已确认空」→
    跳过字幕层直调 transcribe_video（不重复问已知答案）：ASR 出文本=当初误标
    回队 / None=no_cc_confirmed 终局。B站活视频缺 BILI_COOKIE 拦截为 probe_error
    （否则 ASR 静默失败会大面积误判 no_cc）；ASR 依赖缺失整批 probe_error 不猜。
    熔断唯一信号源=view 前置（ASR 层吞下载异常、无 412 指纹，探测层 412 检查已删）。
    残余风险：view 健康但音频 CDN 侧限流静默失败 → 误判 no_cc_confirmed
    （幂等可重跑纠偏，备案）；ASR 命中缓存跳过下载转写，成果可被回队后正式管线复用。
    """
    state = _read_json(STATE_PATH, {})
    deferred_urls = [u for u, r in state.items() if isinstance(r, dict) and r.get("deferred")]
    asr_ready = False
    if _asr_probe is not None and _check_asr_deps is not None:
        deps_ok, _missing = _check_asr_deps()
        asr_ready = deps_ok
    result = {"total": len(deferred_urls), "misclassified": 0, "requeued": 0,
              "no_cc_confirmed": 0, "removed_video": 0, "probe_error": 0,
              "skipped": 0, "risk_streak": 0, "aborted": False}
    changed = False
    for i, url in enumerate(deferred_urls):
        rec = state[url]
        if rec.get("classify"):
            result["skipped"] += 1
            continue
        if not asr_ready:  # ASR 不可用/依赖缺失：不猜，保持原状
            result["probe_error"] += 1
            continue
        code = _bili_view_code(url)  # v2：先 view API 确认环境，再烧重请求
        if code is None:
            result["probe_error"] += 1
            result["risk_streak"] += 1
            if result["risk_streak"] >= 2:
                result["aborted"] = True
                break
            continue
        result["risk_streak"] = 0
        if code in REMOVED_CODES:
            rec["classify"] = "removed_video"
            result["removed_video"] += 1
        elif re.search(r"BV[0-9A-Za-z]{10}", url) and not os.environ.get("BILI_COOKIE"):
            # B站活视频缺 cookie：ASR 层会静默失败 return None，直接判会大面积误标 no_cc
            result["probe_error"] += 1
            continue
        else:
            try:
                ts = _asr_probe(url)
            except Exception:  # noqa: BLE001  ASR 层意外异常逐条跳过不断轮（熔断唯一靠 view 前置）
                result["probe_error"] += 1
                continue
            if ts is not None:
                rec.pop("deferred", None)
                rec["fails"] = 0
                rec["last_error"] = "requeued_by_classify"
                rec["ts"] = _now()
                result["misclassified"] += 1
                result["requeued"] += 1
            else:
                rec["classify"] = "no_cc_confirmed"
                result["no_cc_confirmed"] += 1
        changed = True
        if i < len(deferred_urls) - 1:
            _sleep(random.uniform(*CLASSIFY_SLEEP_RANGE))
    if apply and changed:
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
    ap.add_argument("--defer-failed", action="store_true", help="把 state 里无CC指纹的失败记录批量暂缓（幂等）")
    ap.add_argument("--classify-deferred", action="store_true",
                    help="deferred 三分类：真无CC确认/视频删除定档/误标回队（默认 dry-run）")
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
    if args.defer_failed:
        print(json.dumps(cmd_defer_failed(), ensure_ascii=False, indent=1))
        return
    if args.classify_deferred:
        print(json.dumps(cmd_classify_deferred(apply=args.apply), ensure_ascii=False, indent=1))
        return
    if args.cleanup_old:
        print(json.dumps(cmd_cleanup_old(apply=args.apply), ensure_ascii=False, indent=1))
        return
    ap.print_help()


if __name__ == "__main__":
    main()
