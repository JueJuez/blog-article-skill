#!/usr/bin/env python3
"""批量抓取某 UP 视频字幕（idx 切片），逐条跑 videos/run.py，抓到即入待总结队列。

与 scripts/scys_batch_fetch.py 同构：**抓取 → 入 pending_summaries 队列**，
总结落盘由执行模型派子 Agent 消费队列（prompt/folder 已预计算），不再依赖会话现场手搓。
FORCE_AGENT_MODE 下 run.py 只抓字幕不总结；字幕落 notes/_raw_*.md。

限速与风控防护（2026-09-03，连续抓取触发 B站 412 风控后加）：
- 条间随机延迟 --delay-min/--delay-max（默认 15~30s），消除「机器脉冲」节奏；
- 每小时条数预算 --max-per-hour（默认 100），滑动窗口满额则睡眠到窗口释放；
- 命中风控（HTTP 412 / RISK_CONTROL_412_STOP）立即熔断整批 --stop-on-risk，
  失败条目留在结果文件，冷却后重跑本命令自动续（幂等）；
- 子进程注入 BILI_SUB_RETRIES=1 / BILI_FAILFAST_412=1：批内不就地重试、不做
  yt-dlp/ASR 兜底，把请求省下来（日常单视频调用不受影响）。
- 只跳过「真成功」条目（rc==0 且有字幕输出）；修复超时条目误记上一条 rc 的 bug
  （超时现记 rc=-9）。

用法（补齐任意 UP）：
    python scripts/list_up_videos.py --uid <数字UID>          # 1) 拉全量视频列表
    python scripts/fetch_up_range.py 1 242 --uid <UID> --author <UP名>   # 2) 抓字幕+入队
    python scripts/filter_pending.py                          # 3) 清洗队列后派子 Agent 消费
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

RISK_MARKERS = ("RISK_CONTROL_412_STOP", "412", "Precondition Failed")
_RAW_PATH_RE = re.compile(r"原始内容已暂存至:\s*(\S[^\r\n]*)")


def _paths(uid: int, author: str):
    list_path = os.path.join(ROOT, "notes", "_scraped", f"bili_{uid}_videos.json")
    out_path = os.path.join(ROOT, "notes", "_scraped", f"{author}_fetch_results.json")
    return list_path, out_path


def _pending_path() -> str:
    return os.environ.get("MON_PENDING_SUMMARY_PATH",
                          os.path.join(ROOT, "monitors", "pending_summaries.json"))


def enqueue_pending(url: str, title: str, author: str, publish_time: int,
                    raw_file: str) -> str:
    """抓成功的视频入待总结队列（与 monitors._queue_pending_summary 同 schema）。

    - URL 已在队列 / 已总结过（dedup 闸门）→ 跳过，返回 skip 原因；
    - note_type 由分类器定，prompt 预计算（get_note_prompt + QUALITY_GATE_SELFCHECK）；
    - folder 走统一路由器预计算（监控作者→【监控】区，非监控→【我的总结】/作者/<名>）。
    """
    from articles import dedup as _dedup
    from articles.prompt import classify_note_type, get_note_prompt
    from prompts.templates import QUALITY_GATE_SELFCHECK
    from shared.routing import resolve_folder

    ppath = _pending_path()
    pending = []
    if os.path.exists(ppath):
        try:
            pending = json.load(open(ppath, encoding="utf-8"))
        except Exception:
            pending = []
    if any(p.get("url") == url for p in pending):
        return "in-queue"
    if url and _dedup.is_summarized(url=url):
        return "summarized"

    try:
        content = open(raw_file, encoding="utf-8").read() if raw_file else ""
    except Exception:
        content = ""
    note_type = classify_note_type(title, content)
    entry = {
        "url": url,
        "title": title,
        "author": author,
        "note_type": note_type,
        "tags": [author] if author else [],
        "publish_time": publish_time,
        "folder": resolve_folder({"author": author, "url": url, "title": title,
                                  "source": "bili_backfill"}),
        "raw_file": raw_file,
        # 预计算 prompt（scys 队列同款）：子 Agent 直接按此总结，无需自调任何 CLI
        "prompt": get_note_prompt(note_type) + QUALITY_GATE_SELFCHECK,
        "queued_at": int(time.time()),
    }
    pending.append(entry)
    os.makedirs(os.path.dirname(ppath), exist_ok=True)
    with open(ppath, "w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False, indent=1)
    return "queued"


def main():
    ap = argparse.ArgumentParser(description="按 idx 切片批量抓 UP 视频字幕（限速 + 风控熔断 + 入队）")
    ap.add_argument("start", type=int)
    ap.add_argument("end", type=int)
    ap.add_argument("--uid", type=int, default=1750569201, help="UP 的数字 UID（决定列表文件名）")
    ap.add_argument("--author", type=str, default="趋势浪子", help="UP 名（决定结果文件名与队列 author/folder）")
    ap.add_argument("--delay-min", type=float, default=15.0, help="条间最小延迟秒")
    ap.add_argument("--delay-max", type=float, default=30.0, help="条间最大延迟秒")
    ap.add_argument("--max-per-hour", type=int, default=100, help="每小时最多处理条数（滑动窗口）")
    ap.add_argument("--no-enqueue", dest="enqueue", action="store_false", default=True,
                    help="只抓不入队（默认抓到即入 pending_summaries）")
    ap.add_argument("--stop-on-risk", dest="stop_on_risk", action="store_true", default=True,
                    help="命中 412 风控立即熔断整批（默认开）")
    ap.add_argument("--no-stop-on-risk", dest="stop_on_risk", action="store_false",
                    help="命中 412 不熔断，继续跑完（不推荐）")
    args = ap.parse_args()

    list_path, out_path = _paths(args.uid, args.author)
    items = json.load(open(list_path, encoding="utf-8"))["items"]
    created = [it for it in items if it.get("created")]
    pub_by_idx = {it["idx"]: it.get("created", 0) for it in items}
    results = []
    if os.path.exists(out_path):
        results = json.load(open(out_path, encoding="utf-8"))
    done_urls = {r["url"] for r in results if _is_really_done(r)}
    if len(done_urls) != sum(1 for r in results if r.get("rc") == 0):
        print("[info] 结果文件中存在失败/空输出条目，本次自动重抓这些条目")

    # 子进程限速环境：批内不做就地重试、命中 412 不走兜底链
    os.environ.setdefault("BILI_SUB_RETRIES", "1")
    os.environ.setdefault("BILI_FAILFAST_412", "1")

    window = deque()  # 本进程内已完成条目的时间戳（滑动 1h 窗口）

    def _wait_budget():
        now = time.time()
        while window and now - window[0] >= 3600:
            window.popleft()
        while len(window) >= args.max_per_hour:
            sleep_s = max(1.0, window[0] + 3600 - now + random.uniform(1, 5))
            print(f"[rate] 每小时 {args.max_per_hour} 条预算已满，睡眠 {sleep_s:.0f}s", flush=True)
            time.sleep(sleep_s)
            now = time.time()
            while window and now - window[0] >= 3600:
                window.popleft()

    todo = [it for it in items
            if args.start <= it["idx"] <= args.end
            and f"https://www.bilibili.com/video/{it['bvid']}" not in done_urls]
    print(f"[plan] 范围 [{args.start},{args.end}] {args.author}({args.uid})，待抓 {len(todo)} 条"
          f"（延迟 {args.delay_min}~{args.delay_max}s/条，预算 {args.max_per_hour} 条/小时，"
          f"{'抓到即入队' if args.enqueue else '只抓不入队'}）", flush=True)

    aborted = None
    enqueued = skipped = 0
    for n, it in enumerate(todo):
        if n > 0:
            delay = random.uniform(args.delay_min, args.delay_max)
            print(f"[delay] 等待 {delay:.1f}s 后继续…", flush=True)
            time.sleep(delay)
        _wait_budget()
        url = f"https://www.bilibili.com/video/{it['bvid']}"
        print(f"\n===== #{it['idx']}/{it['title'][:40]} {url} =====", flush=True)
        try:
            p = subprocess.run([sys.executable, os.path.join(ROOT, "videos", "run.py"),
                                "--url", url, "--author", args.author],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=900, cwd=ROOT)
            rc = p.returncode
            tail = (p.stdout or "")[-3000:]
            err_tail = (p.stderr or "")[-1500:]
        except subprocess.TimeoutExpired:
            rc, tail, err_tail = -9, "", "TIMEOUT 900s"
        except Exception as e:
            rc, tail, err_tail = -1, "", f"{type(e).__name__}: {e}"
        print(tail[-1200:], flush=True)
        if err_tail.strip():
            print("[stderr]", err_tail[-400:], flush=True)
        results.append({"idx": it["idx"], "url": url, "title": it["title"],
                        "rc": rc, "stdout_tail": tail, "stderr_tail": err_tail})
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        window.append(time.time())

        # 抓到真字幕 → 入待总结队列（scys_batch_fetch 同款闭环）
        if args.enqueue and rc == 0 and _is_really_done(results[-1]):
            m = _RAW_PATH_RE.search(tail)
            raw_file = m.group(1).strip() if m else ""
            if raw_file and os.path.exists(raw_file):
                status = enqueue_pending(url, it["title"], args.author,
                                         pub_by_idx.get(it["idx"], 0), raw_file)
                if status == "queued":
                    enqueued += 1
                    print(f"[queue] 已入队: {it['title'][:36]}", flush=True)
                else:
                    skipped += 1
                    print(f"[queue] 跳过入队（{status}）: {it['title'][:36]}", flush=True)
            else:
                skipped += 1
                print("[queue] 未解析到 raw 文件路径，跳过入队", flush=True)

        combined = tail + err_tail
        if args.stop_on_risk and any(mk in combined for mk in RISK_MARKERS):
            aborted = it["idx"]
            print(f"\n🛑 命中B站风控(412)，熔断整批。已完成 {n + 1}/{len(todo)} 条。"
                  f"\n   建议：冷却 30~60 分钟后重跑同一命令自动续抓（幂等）。", flush=True)
            break

    if aborted is not None:
        sys.exit(87)
    print(f"\n✅ 批量抓取完成 [{args.start}-{args.end}]，共 {len(results)} 条结果 → {out_path}", flush=True)
    if args.enqueue:
        print(f"   队列：新入队 {enqueued} 条，跳过 {skipped} 条。"
              f"消费：python scripts/filter_pending.py 清洗后派子 Agent 总结。", flush=True)


def _is_really_done(entry: dict) -> bool:
    """rc==0 且确有字幕输出才算成功；空 stdout 的 rc==0 是历史超时误记，要重抓。"""
    return entry.get("rc") == 0 and len((entry.get("stdout_tail") or "").strip()) > 100


if __name__ == "__main__":
    main()
