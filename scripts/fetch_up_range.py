#!/usr/bin/env python3
"""批量抓取某 UP 视频字幕（idx 切片），逐条跑 videos/run.py，结果汇总落盘。
FORCE_AGENT_MODE 下 run.py 只抓字幕不总结；本脚本收集 degraded 信息供子 Agent 总结。

限速与风控防护（2026-09-03，连续抓取触发 B站 412 风控后加）：
- 条间随机延迟 --delay-min/--delay-max（默认 15~30s），消除「机器脉冲」节奏；
- 每小时条数预算 --max-per-hour（默认 100），滑动窗口满额则睡眠到窗口释放；
- 命中风控（HTTP 412 / RISK_CONTROL_412_STOP）立即熔断整批 --stop-on-risk，
  失败条目留在结果文件，冷却后重跑本命令自动续（幂等）；
- 子进程注入 BILI_SUB_RETRIES=1 / BILI_FAILFAST_412=1：批内不就地重试、不做
  yt-dlp/ASR 兜底，把请求省下来（日常单视频调用不受影响）。
- 只跳过「真成功」条目（rc==0 且有字幕输出）；修复超时条目误记上一条 rc 的 bug
  （超时现记 rc=-9）。
"""
import argparse
import json
import os
import random
import subprocess
import sys
import time
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST_PATH = os.path.join(ROOT, "notes", "_scraped", "bili_1750569201_videos.json")
OUT_PATH = os.path.join(ROOT, "notes", "_scraped", "趋势浪子_fetch_results.json")
AUTHOR = "趋势浪子"

RISK_MARKERS = ("RISK_CONTROL_412_STOP", "412", "Precondition Failed")


def _is_really_done(entry: dict) -> bool:
    """rc==0 且确有字幕输出才算成功；空 stdout 的 rc==0 是历史超时误记，要重抓。"""
    return entry.get("rc") == 0 and len((entry.get("stdout_tail") or "").strip()) > 100


def main():
    ap = argparse.ArgumentParser(description="按 idx 切片批量抓 UP 视频字幕（限速 + 风控熔断）")
    ap.add_argument("start", type=int)
    ap.add_argument("end", type=int)
    ap.add_argument("--delay-min", type=float, default=15.0, help="条间最小延迟秒")
    ap.add_argument("--delay-max", type=float, default=30.0, help="条间最大延迟秒")
    ap.add_argument("--max-per-hour", type=int, default=100, help="每小时最多处理条数（滑动窗口）")
    ap.add_argument("--stop-on-risk", dest="stop_on_risk", action="store_true", default=True,
                    help="命中 412 风控立即熔断整批（默认开）")
    ap.add_argument("--no-stop-on-risk", dest="stop_on_risk", action="store_false",
                    help="命中 412 不熔断，继续跑完（不推荐）")
    args = ap.parse_args()

    items = json.load(open(LIST_PATH, encoding="utf-8"))["items"]
    results = []
    if os.path.exists(OUT_PATH):
        results = json.load(open(OUT_PATH, encoding="utf-8"))
    done_urls = {r["url"] for r in results if _is_really_done(r)}
    if len(done_urls) != sum(1 for r in results if r.get("rc") == 0):
        print(f"[info] 结果文件中存在失败/空输出条目，本次自动重抓这些条目")

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
    print(f"[plan] 范围 [{args.start},{args.end}]，待抓 {len(todo)} 条"
          f"（延迟 {args.delay_min}~{args.delay_max}s/条，预算 {args.max_per_hour} 条/小时）", flush=True)

    aborted = None
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
                                "--url", url, "--author", AUTHOR],
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
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        window.append(time.time())

        combined = tail + err_tail
        if args.stop_on_risk and any(m in combined for m in RISK_MARKERS):
            aborted = it["idx"]
            print(f"\n🛑 命中B站风控(412)，熔断整批。已完成 {n + 1}/{len(todo)} 条。"
                  f"\n   建议：冷却 30~60 分钟后重跑同一命令自动续抓（幂等）。", flush=True)
            break

    if aborted is not None:
        sys.exit(87)
    print(f"\n✅ 批量抓取完成 [{args.start}-{args.end}]，共 {len(results)} 条结果 → {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
