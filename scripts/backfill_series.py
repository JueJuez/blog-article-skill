"""系列课点名补齐命令（PLAN-20260908 阶段4.3 + D8 护栏）。

「补齐 <UP> 的系列课」：给系列任一集 URL → 1 次 view API 列全集 → 登记表过滤
已总结 → 剩余集逐条抓字幕（BILI_GAP 串行）→ 入 monitors/pending_summaries.json
降级队列（folder=系列容器路由 + 第NN集_ 前缀 + prompt 预计算）→ 子 Agent 消费
总结落盘（与监控增量同一消费范式，见 AGENTS.md 新会话执行步骤 4）。

D8 护栏（用户 2026-09-08 拍板 A）：失败账本 notes/_scraped/
series_backfill_failures.json 按 URL 累计失败次数——同集累计 3 次（抓字幕失败
+1；入队后历经一轮仍未登记 +1，即消费未成功）→ 停止自动重试、打印转人工清单。
总结失败的集不登记（真源无成品），下次点名再次检出属预期行为（该补的就得补），
护栏防无上限空转。

用法：
    python scripts/backfill_series.py --url <系列任一集URL> [--url ...] [--gap 30]
"""
import argparse
import json
import os
import sys
import time
from typing import Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

FAIL_LIMIT = 3
FAILURES_PATH = os.path.join(BASE_DIR, "notes", "_scraped",
                             "series_backfill_failures.json")


def _pending_path() -> str:
    return os.environ.get(
        "MON_PENDING_SUMMARY_PATH",
        os.path.join(BASE_DIR, "monitors", "pending_summaries.json"))


def load_failures(path: str = None) -> dict:
    p = path or FAILURES_PATH
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_failures(data: dict, path: str = None) -> None:
    p = path or FAILURES_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def bump_failure(url: str, err: str, path: str = None) -> int:
    """同集失败 +1 并持久化，返回累计次数（D8 账本单一写点）。"""
    data = load_failures(path)
    cnt = int((data.get(url) or {}).get("fail_count", 0)) + 1
    data[url] = {"fail_count": cnt, "last_error": (err or "")[:200],
                 "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    save_failures(data, path)
    return cnt


def list_season_episodes(url: str) -> Optional[dict]:
    """1 次 view API 列全集：返回 {season_title, author, episodes:[{ep_index,url}]}。

    ep_index 为跨 section 全局序号（1-based，与五步管线前缀口径一致）；
    URL 无 bvid / view 失败 / 无 ugc_season → None（非系列，点名补齐不处理）。
    """
    from videos.fetch import _bili_extract_bvid, _bili_get_video_info

    bvid = _bili_extract_bvid(url)
    if not bvid:
        return None
    try:
        info = _bili_get_video_info(bvid)
    except Exception:
        info = None
    season = (info or {}).get("ugc_season") or {}
    sections = season.get("sections") or []
    if not sections:
        return None
    episodes, n = [], 0
    for sec in sections:
        for ep in (sec.get("episodes") or []):
            n += 1
            episodes.append({
                "ep_index": n,
                "url": f"https://www.bilibili.com/video/{ep.get('bvid', '')}",
            })
    return {"season_title": season.get("title", ""),
            "author": (info or {}).get("author", ""), "episodes": episodes}


def _load_pending() -> list:
    p = _pending_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_pending(items: list) -> None:
    p = _pending_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)


def _backfill_one(ep: dict, season_title: str, author: str, report: dict,
                  gap: float) -> None:
    """单集补齐：D8 护栏 → 抓字幕 → 落 raw → 入降级队列（schema 与
    monitors._queue_pending_summary / fetch_up_range.enqueue_pending 对齐）。"""
    url = ep["url"]
    rec = load_failures().get(url) or {}
    if int(rec.get("fail_count", 0)) >= FAIL_LIMIT:
        report["manual"].append({"url": url, "fail_count": rec.get("fail_count", 0),
                                 "last_error": rec.get("last_error", "")})
        print(f"[backfill] 转人工（失败 {rec.get('fail_count')} 次，不再自动重试）: {url}")
        return
    if any(p.get("url") == url for p in _load_pending()):
        # 2026-09-20 改：持续监督模式下 backfill 与消费并行，「在队列等消费」是常态，
        # 每轮 bump 会把正常排队集误推到 3 次转人工——改为只计数不入账，消费失败的
        # 集本来就留在队列里，无需 D8 惩罚（真实抓取失败仍走 bump_failure）。
        report["in_queue"] += 1
        print(f"[backfill] 已在队列（等待消费）: {url}")
        return
    if gap:
        time.sleep(gap)
    # 崩溃定位锚点（2026-09-20）：本集开始抓取即打印，监督者据此识别反复弄死
    # 进程的集（当初 faster-whisper 原生崩溃无法被 try/except 捕获，故用日志锚点定位）。
    # ⚠️ 后续澄清：当时被判成「毒集」的集其实混了三类不同原因——环境崩（ctranslate2
    # 4.8.2 回归，已修）、源没人声（录屏音乐短片）、充电专属仅试看；后两类任何 ASR 都
    # 无解，别当环境故障反复重试（详见 `references/asr-bilibili-sandbox.md`）。
    print(f"[backfill] 开始抓取: {url}", flush=True)
    try:
        from videos.fetch import fetch_transcript
        res = fetch_transcript(url)
    except Exception as exc:
        res, err = None, str(exc)
    else:
        err = "字幕抓取失败（含 ASR 兜底无果）"
    if not res:
        bump_failure(url, err)
        report["failed"] += 1
        print(f"[backfill] 字幕抓取失败（账本 +1）: {url} :: {err}")
        return
    ep_title, segments, t_author = res
    from articles.main import save_raw_content_to_file
    from articles.prompt import classify_note_type, get_note_prompt, source_chars_of
    from prompts.templates import QUALITY_GATE_SELFCHECK
    from shared.chunking import segments_to_text
    from videos.main import series_folder

    text = segments_to_text(segments)
    raw_file = save_raw_content_to_file(text, title=ep_title)
    note_type = classify_note_type(ep_title, text[:4000])
    real_author = author or t_author or ""
    entry = {
        "url": url,
        "title": f"第{ep['ep_index']:02d}集_{ep_title}",
        "author": real_author,
        "note_type": note_type,
        "tags": [real_author] if real_author else [],
        "publish_time": 0,
        "folder": series_folder({}, real_author, season_title, url),
        "raw_file": raw_file,
        # 预计算 prompt（三队列统一口径：monitors/scys/UP）：子 Agent 直接按此总结
        "prompt": get_note_prompt(note_type, source_chars_of(text)) + QUALITY_GATE_SELFCHECK,
        "queued_at": int(time.time()),
    }
    pending = _load_pending()
    pending.append(entry)
    _save_pending(pending)
    report["queued"] += 1
    print(f"[backfill] 已入队: {entry['title']} -> {raw_file}")


def backfill_series(urls: list, gap: float = None) -> dict:
    """点名补齐主流程：列全集 → 过滤已登记 → 逐条入队。返回运行报告。"""
    report = {"series": 0, "non_series": 0, "episodes": 0, "skip_summarized": 0,
              "queued": 0, "in_queue": 0, "failed": 0, "manual": []}
    if gap is None:
        gap = float(os.environ.get("BILI_GAP", "30") or 30)
    for url in urls:
        season = list_season_episodes(url)
        if not season:
            report["non_series"] += 1
            print(f"[backfill] 非系列视频，跳过（点名补齐仅处理系列课）: {url}")
            continue
        report["series"] += 1
        # 系列排除名单（2026-09-20）：用户点名去掉的系列课（subscriptions.json
        # series_exclude）不在补齐范围，与监控管线同一守卫。
        try:
            from shared.routing import load_series_exclude, series_title_excluded
            _exc = load_series_exclude().get(season.get("author", ""), [])
            if series_title_excluded(season["season_title"], _exc):
                print(f"[backfill] 系列《{season['season_title']}》在排除名单（series_exclude），跳过")
                continue
        except Exception:
            pass
        episodes = season["episodes"]
        report["episodes"] += len(episodes)
        ep_urls = [e["url"] for e in episodes]
        try:
            from articles import dedup
            hits = dedup.batch_is_summarized(ep_urls) or set()
        except Exception as e:
            print(f"[backfill] 登记表预查失败（放行全量）: {e}")
            hits = set()
        todo = [e for e in episodes if e["url"] not in hits]
        report["skip_summarized"] += len(episodes) - len(todo)
        print(f"[backfill] 系列《{season['season_title']}》：共 {len(episodes)} 集，"
              f"已登记跳过 {len(episodes) - len(todo)}，待补 {len(todo)}")
        for ep in todo:
            _backfill_one(ep, season["season_title"], season["author"], report, gap)
    return report


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(
        description="系列课点名补齐：列全集 → 过滤已登记 → 逐条入队（D8 失败 3 次转人工）")
    parser.add_argument("--url", action="append", required=True,
                        help="系列任一集 URL，可多次传入处理多个系列")
    parser.add_argument("--gap", type=float, default=None,
                        help="抓字幕间隔秒（默认读 BILI_GAP，缺省 30）")
    args = parser.parse_args(argv)
    report = backfill_series(args.url, gap=args.gap)
    print("[backfill] 报告: " + json.dumps(report, ensure_ascii=False))
    if report["manual"]:
        print(f"[backfill] 转人工 {len(report['manual'])} 条（失败≥{FAIL_LIMIT} 次）：")
        for m in report["manual"]:
            print(f"  - {m['url']}（失败 {m['fail_count']} 次：{m['last_error']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
