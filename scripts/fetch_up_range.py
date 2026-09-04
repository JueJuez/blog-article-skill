#!/usr/bin/env python3
"""批量抓取某 UP 视频字幕（idx 切片），批量化跑 videos/run.py，抓到即入待总结队列。

与 scripts/scys_batch_fetch.py 同构：**抓取 → 入 pending_summaries 队列**，
总结落盘由执行模型派子 Agent 消费队列（prompt/folder 已预计算），不再依赖会话现场手搓。
FORCE_AGENT_MODE 下 run.py 只抓字幕不总结；字幕落 notes/_raw_*.md。

限速与风控防护（2026-09-03 起，两轮迭代）：
- 【批量化进程模型】每 --batch-size（默认 8）条共用一个 run.py 子进程
  （--batch-file 批量模式），消除「每条一次 Python 冷启动」——129 集从 129 进程
  降到 ~17 进程；条间延迟/每小时预算移入 run.py 批内循环；
- 条间随机延迟 --delay-min/--delay-max（默认 15~30s），消除「机器脉冲」节奏；
- 每小时条数预算 --max-per-hour（默认 100），滑动窗口满额则睡眠到窗口释放；
- 单条 412 不熔断整批：命中即「跳过+记录」（写入 <作者>_risk_skip.json，后续不再
  自动重抓），仅当【连续】命中 412 达到 --risk-threshold（默认 3）才熔断整批；
- 批量默认跳过 ASR（注入 BILI_BATCH_NO_ASR=1，可由 --with-asr 开启）：无 AI 字幕的
  视频只做轻量原生 API 探测即返回，不下载音频、不耗时、不触发音频 412；
- cookie 惰性轮换：运行开始 + 每逢 412 风险后，nav 校验 cookie，失效则尝试从本机
  Chrome 提取新 cookie（事件记入运行日志）；
- 【结构化运行日志】每次运行写 <作者>_backfill_<时间戳>.log + .json（请求级追踪：
  密度/状态码分布/412-413-429/超时，逐条结果，cookie 事件），用于事后调参；
- 【日志自动清理】运行开始时，自动删除同作者超过 --log-ttl-days（默认 7 天）且
  【无异常】（零错误/未熔断/无风险跳过）的旧运行日志；有异常的保留供排查。

用法（补齐任意 UP）：
    python scripts/list_up_videos.py --uid <数字UID>          # 1) 拉全量视频列表
    python scripts/fetch_up_range.py 1 242 --uid <UID> --author <UP名>   # 2) 抓字幕+入队
    python scripts/filter_pending.py                          # 3) 清洗队列后派子 Agent 消费
"""
import argparse
import glob
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

RISK_MARKERS = ("RISK_CONTROL_412_STOP", "Precondition Failed")
_RAW_PATH_RE = re.compile(r"原始内容已暂存至:\s*(\S[^\r\n]*)")
_BATCH_BLOCK_RE = re.compile(r"=====BATCH_RESULTS_START=====\s*\n(.*?)\n=====BATCH_RESULTS_END=====", re.S)

_LOG_LINES = []


def _log(msg: str = "") -> None:
    """打印并捕获到运行日志（.log 文件）。"""
    print(msg, flush=True)
    _LOG_LINES.append(msg)


def _iso(ts: float = None) -> str:
    return datetime.fromtimestamp(ts or time.time()).strftime("%Y-%m-%d %H:%M:%S")


def _paths(uid: int, author: str):
    list_path = os.path.join(ROOT, "notes", "_scraped", f"bili_{uid}_videos.json")
    out_path = os.path.join(ROOT, "notes", "_scraped", f"{author}_fetch_results.json")
    return list_path, out_path


def _pending_path() -> str:
    return os.environ.get("MON_PENDING_SUMMARY_PATH",
                          os.path.join(ROOT, "monitors", "pending_summaries.json"))


def _risk_skip_path(author: str) -> str:
    return os.path.join(ROOT, "notes", "_scraped", f"{author}_risk_skip.json")


def _load_risk_skip(author: str) -> set:
    p = _risk_skip_path(author)
    if os.path.exists(p):
        try:
            return set(json.load(open(p, encoding="utf-8")))
        except Exception:
            return set()
    return set()


def _save_risk_skip(author: str, skip: set) -> None:
    p = _risk_skip_path(author)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(sorted(skip), f, ensure_ascii=False, indent=1)
    except Exception:
        pass


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


def _is_really_done(entry: dict) -> bool:
    """rc==0 且确有字幕输出才算成功；空 stdout 的 rc==0 是历史超时误记，要重抓。"""
    return entry.get("rc") == 0 and len((entry.get("stdout_tail") or "").strip()) > 100


def _gc_old_logs(author: str, ttl_days: int) -> None:
    """自动清理同作者超过 ttl 天且【无异常】的旧运行日志（.json + .log 成对删）。

    有异常（任何请求错误 / 熔断 / 风险跳过）的日志永久保留，供事后排查调参。
    """
    if ttl_days <= 0:
        return
    now = time.time()
    removed = 0
    for p in glob.glob(os.path.join(ROOT, "notes", "_scraped", f"{author}_backfill_*.json")):
        try:
            if now - os.path.getmtime(p) < ttl_days * 86400:
                continue
            d = json.load(open(p, encoding="utf-8"))
            summ = d.get("summary", {})
            errs = summ.get("errors", {})
            clean = (not summ.get("aborted_at") and not summ.get("risk_skip")
                     and all(v == 0 for v in errs.values()))
            if not clean:
                continue
            os.remove(p)
            lp = p[:-5] + ".log"
            if os.path.exists(lp):
                os.remove(lp)
            removed += 1
        except Exception:
            continue
    if removed:
        _log(f"[gc] 已清理 {removed} 份超过 {ttl_days} 天的无异常运行日志")


def _summarize_trace(trace: list) -> dict:
    """请求级追踪 → 汇总：状态码分布、错误分类、请求密度（相邻请求间隔）。"""
    errors = {"412": 0, "413": 0, "429": 0, "timeout": 0, "other": 0}
    status_count = {}
    endpoints = {}
    for r in trace:
        ep = r.get("endpoint") or "unknown"
        endpoints[ep] = endpoints.get(ep, 0) + 1
        if r.get("ok"):
            sc = str(r.get("status"))
            status_count[sc] = status_count.get(sc, 0) + 1
            continue
        st = r.get("status")
        err = str(r.get("err") or "")
        if st == 412:
            errors["412"] += 1
        elif st == 413:
            errors["413"] += 1
        elif st == 429:
            errors["429"] += 1
        elif "Timeout" in err or "timed out" in err.lower():
            errors["timeout"] += 1
        else:
            errors["other"] += 1
        key = f"{ep}:HTTP{st}" if st else f"{ep}:{err}"
        status_count[key] = status_count.get(key, 0) + 1
    gaps = [round(b["ts"] - a["ts"], 2) for a, b in zip(trace, trace[1:])
            if b["ts"] > a["ts"]]
    duration = (trace[-1]["ts"] - trace[0]["ts"]) if len(trace) > 1 else 0.0
    return {
        "requests_total": len(trace),
        "requests_by_endpoint": endpoints,
        "status_count": status_count,
        "errors": errors,
        "density": {
            "avg_gap_s": round(sum(gaps) / len(gaps), 2) if gaps else 0,
            "min_gap_s": round(min(gaps), 2) if gaps else 0,
            "max_gap_s": round(max(gaps), 2) if gaps else 0,
            "req_per_min": round(len(trace) / duration * 60, 1) if duration > 0 else 0,
        },
    }


def main():
    ap = argparse.ArgumentParser(description="按 idx 切片批量抓 UP 视频字幕（批量化+限速+风控+入队+运行日志）")
    ap.add_argument("start", type=int)
    ap.add_argument("end", type=int)
    ap.add_argument("--uid", type=int, default=1750569201, help="UP 的数字 UID（决定列表文件名）")
    ap.add_argument("--author", type=str, default="趋势浪子", help="UP 名（决定结果文件名与队列 author/folder）")
    ap.add_argument("--delay-min", type=float, default=15.0, help="条间最小延迟秒（批内+批间通用）")
    ap.add_argument("--delay-max", type=float, default=30.0, help="条间最大延迟秒（批内+批间通用）")
    ap.add_argument("--max-per-hour", type=int, default=100, help="每小时最多处理条数（滑动窗口，批内执行）")
    ap.add_argument("--batch-size", type=int, default=8, help="每个子进程处理的视频数（默认 8）")
    ap.add_argument("--no-enqueue", dest="enqueue", action="store_false", default=True,
                    help="只抓不入队（默认抓到即入 pending_summaries）")
    ap.add_argument("--risk-threshold", type=int, default=3,
                    help="连续命中 412 达到此数才熔断整批（默认 3；0=永不熔断，只跳过单条）")
    ap.add_argument("--no-stop-on-risk", dest="risk_threshold", action="store_const", const=0,
                    help="等价 --risk-threshold 0：永不熔断，单条412仅跳过记录")
    ap.add_argument("--with-asr", action="store_true", default=False,
                    help="批量也跑 ASR 音频转写（默认关：无 AI 字幕视频留待后续单独处理）")
    ap.add_argument("--reset-risk-skip", action="store_true", default=False,
                    help="清空已记录的 412 风险跳过列表，让这些视频重新参与抓取")
    ap.add_argument("--log-ttl-days", type=int, default=7,
                    help="无异常运行日志保留天数（默认 7，运行开始时自动清理；0=不清理）")
    args = ap.parse_args()

    started_at = time.time()
    list_path, out_path = _paths(args.uid, args.author)
    items = json.load(open(list_path, encoding="utf-8"))["items"]
    pub_by_idx = {it["idx"]: it.get("created", 0) for it in items}
    results = []
    if os.path.exists(out_path):
        results = json.load(open(out_path, encoding="utf-8"))
    done_urls = {r["url"] for r in results if _is_really_done(r)}
    if len(done_urls) != sum(1 for r in results if r.get("rc") == 0):
        _log("[info] 结果文件中存在失败/空输出条目，本次自动重抓这些条目")

    # 子进程限速环境：批内不做就地重试、命中 412 不走兜底链
    os.environ.setdefault("BILI_SUB_RETRIES", "1")
    os.environ.setdefault("BILI_FAILFAST_412", "1")
    # 批量默认跳过 ASR（无字幕视频只做轻量探测，不下载音频/不耗时/不触发音频412），
    # 把麻烦的 ASR 后置到后续单独跑；--with-asr 才在批量内也做 ASR。
    if not args.with_asr:
        os.environ.setdefault("BILI_BATCH_NO_ASR", "1")

    if args.reset_risk_skip:
        _save_risk_skip(args.author, set())
        _log(f"[info] 已清空 {args.author} 的 412 风险跳过列表")
    risk_skip = _load_risk_skip(args.author)
    if risk_skip:
        _log(f"[info] 已记录 {len(risk_skip)} 条 412 风险跳过（本次不抓，--reset-risk-skip 可重置）")

    todo = [it for it in items
            if args.start <= it["idx"] <= args.end
            and f"https://www.bilibili.com/video/{it['bvid']}" not in done_urls
            and it["bvid"] not in risk_skip]
    n_chunks = (len(todo) + args.batch_size - 1) // args.batch_size
    _log(f"[plan] 范围 [{args.start},{args.end}] {args.author}({args.uid})，待抓 {len(todo)} 条"
         f" → {n_chunks} 个批次（每批 {args.batch_size} 条共 1 进程）"
         f"（延迟 {args.delay_min}~{args.delay_max}s/条，预算 {args.max_per_hour} 条/小时，"
         f"{'抓到即入队' if args.enqueue else '只抓不入队'}"
         f"{' / 含ASR' if args.with_asr else ' / 跳过ASR(默认)'}，"
         f"连续 {args.risk_threshold} 条412才熔断）")

    # 自动清理旧的【无异常】运行日志
    _gc_old_logs(args.author, args.log_ttl_days)

    from videos import fetch as bfetch
    all_trace = []
    cookie_events = []

    def _ensure_cookie(trigger: str) -> None:
        """cookie 惰性轮换：nav 校验 → 失效才从本机 Chrome 提取。事件记入运行日志。"""
        try:
            state, fresh = bfetch.rotate_bili_cookie_if_dead()
        except Exception as e:
            state, fresh = "failed", None
            _log(f"[cookie] 校验/轮换异常：{type(e).__name__}: {e}")
        if state == "rotated" and fresh:
            os.environ["BILI_COOKIE"] = fresh  # 子进程继承新 cookie
        cookie_events.append({"ts": _iso(), "trigger": trigger, "event": f"cookie_{state}"})
        _log(f"[cookie] {trigger} → {state}")

    _ensure_cookie("run_start")

    aborted = None
    enqueued = skipped = series_cnt = 0
    consec_risk = 0       # 连续 412 计数（成功条目重置）
    risk_skipped = 0      # 本次因 412 被跳过记录的条数
    video_outcomes = []   # 逐条结果（结构化日志用）
    chunks = [todo[i:i + args.batch_size] for i in range(0, len(todo), args.batch_size)]

    for ci, chunk in enumerate(chunks):
        if ci > 0:
            delay = random.uniform(args.delay_min, args.delay_max)
            _log(f"[delay] 批间等待 {delay:.1f}s（批次 {ci + 1}/{len(chunks)}）…")
            time.sleep(delay)

        # 写临时批量文件（系统 temp，scratch 不进项目目录）→ 一个子进程处理整批
        fd, batch_path = tempfile.mkstemp(prefix=f"bili_batch_{args.author}_",
                                          suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump([{"idx": it["idx"],
                        "url": f"https://www.bilibili.com/video/{it['bvid']}",
                        "title": it["title"], "author": args.author,
                        "publish_time": pub_by_idx.get(it["idx"], 0),
                        "lang": "zh"} for it in chunk], f, ensure_ascii=False)

        # 超时上界：批内每条 ≈ delay_max + 处理 150s（批量链路轻量，正常远低于此）
        timeout_s = max(300, len(chunk) * (args.delay_max + 150))
        stdout = stderr = ""
        batch_rc = None
        try:
            p = subprocess.run([sys.executable, os.path.join(ROOT, "videos", "run.py"),
                                "--batch-file", batch_path,
                                "--delay-min", str(args.delay_min),
                                "--delay-max", str(args.delay_max),
                                "--max-per-hour", str(args.max_per_hour)],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout_s, cwd=ROOT)
            stdout, stderr, batch_rc = p.stdout or "", p.stderr or "", p.returncode
        except subprocess.TimeoutExpired as e:
            so = e.stdout or b""
            stdout = so.decode("utf-8", "replace") if isinstance(so, bytes) else str(so)
            stderr = f"CHUNK TIMEOUT {timeout_s}s"
            batch_rc = -9
        except Exception as e:
            stderr = f"{type(e).__name__}: {e}"
            batch_rc = -1

        m = _BATCH_BLOCK_RE.search(stdout)
        if m:
            try:
                batch_data = json.loads(m.group(1))
            except Exception:
                batch_data = None
        else:
            batch_data = None

        if not batch_data:
            # 整批未产出结果（超时/崩溃）：每条记 rc=-9，下轮重跑幂等续抓
            _log(f"[batch] 批次 {ci + 1} 未解析到结果（rc={batch_rc}），"
                 f"{len(chunk)} 条按超时记录，下轮幂等重抓")
            if stderr.strip():
                _log("[stderr] " + stderr[-500:])
            for it in chunk:
                url = f"https://www.bilibili.com/video/{it['bvid']}"
                results.append({"idx": it["idx"], "url": url, "title": it["title"],
                                "rc": batch_rc if batch_rc is not None else -1,
                                "stdout_tail": "", "stderr_tail": stderr[-1500:]})
                video_outcomes.append({"idx": it["idx"], "url": url,
                                       "outcome": "chunk_failed", "ms": 0,
                                       "err": stderr[-200:]})
        else:
            all_trace.extend(batch_data.get("http_trace") or [])
            for rec in batch_data.get("videos") or []:
                url = rec.get("url", "")
                idx = rec.get("idx")
                it = next((x for x in chunk if x["idx"] == idx), None)
                title = (it or {}).get("title") or rec.get("title", "")
                rc = 0 if rec.get("ok") else 1
                stdout_tail = json.dumps(rec, ensure_ascii=False)
                results.append({"idx": idx, "url": url, "title": title,
                                "rc": rc, "stdout_tail": stdout_tail,
                                "stderr_tail": stderr[-1500:] if stderr else ""})
                _log(f"[batch] #{idx} {'OK' if rec['ok'] else 'FAIL'}"
                     f"{' series' if rec.get('series') else ''}"
                     f"{' RISK412' if rec.get('risk412') else ''} {rec.get('ms', 0)}ms"
                     + (f" err={rec['error'][:120]}" if rec.get("error") else ""))

                outcome = ("series" if rec.get("series")
                           else "done" if rec.get("ok")
                           else "risk412" if rec.get("risk412") else "error")
                video_outcomes.append({"idx": idx, "url": url, "outcome": outcome,
                                       "ms": rec.get("ms", 0),
                                       "err": rec.get("error") or None})

                # 抓到真字幕 → 入待总结队列（scys_batch_fetch 同款闭环）
                raw_file = rec.get("raw_file") or ""
                if (args.enqueue and rec.get("ok") and not rec.get("series")
                        and raw_file and os.path.exists(raw_file)):
                    status = enqueue_pending(url, title, args.author,
                                             pub_by_idx.get(idx, 0), raw_file)
                    if status == "queued":
                        enqueued += 1
                        _log(f"[queue] 已入队: {title[:36]}")
                    else:
                        skipped += 1
                        _log(f"[queue] 跳过入队（{status}）: {title[:36]}")
                elif rec.get("ok") and rec.get("series"):
                    series_cnt += 1
                    _log(f"[series] #{idx} 走系列课管线（写 notes/<系列>/，不入单篇队列）")
                elif rec.get("ok") and not raw_file:
                    skipped += 1
                    _log("[queue] 未解析到 raw 文件路径，跳过入队")

                # 412 风险：跳过 + 记录，不碰它；连续达阈值才熔断
                if rec.get("risk412"):
                    consec_risk += 1
                    risk_skipped += 1
                    if it:
                        risk_skip.add(it["bvid"])
                        _save_risk_skip(args.author, risk_skip)
                    _log(f"   ⚠️ #{idx} 命中B站风控(412)，跳过并记录（不再自动重抓）。"
                         f"连续412计数 {consec_risk}/{args.risk_threshold}")
                elif rec.get("ok"):
                    consec_risk = 0

        # 批内子进程的完整叙述输出（每次请求的明细行、[delay]/[rate]）并入 .log 人读部分
        if stdout.strip():
            _LOG_LINES.append("----- batch stdout -----")
            _LOG_LINES.extend(stdout.strip()[-4000:].splitlines())

        try:
            os.remove(batch_path)
        except Exception:
            pass
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)

        if aborted is None and args.risk_threshold and consec_risk >= args.risk_threshold:
            aborted = video_outcomes[-1]["idx"] if video_outcomes else None
            _log(f"\n🛑 连续 {consec_risk} 条命中412，判定为B站风控，熔断整批。"
                 f"\n   建议：冷却 30~60 分钟后重跑（412 风险条目已记录，"
                 f"待冷却可用 --reset-risk-skip 重新尝试）。")
            break
        # 每逢 412 风险后复检 cookie（区分「风控」与「cookie 失效」两类 412 根因）
        if any(v.get("outcome") == "risk412" for v in video_outcomes[-len(chunk):] if v):
            _ensure_cookie("after_risk412")

    # ---- 结构化运行日志（.log 人读 + .json 机读：请求密度/错误分布/逐条结果）----
    trace_summary = _summarize_trace(all_trace)
    duration_s = round(time.time() - started_at, 1)
    run_log = {
        "meta": {
            "author": args.author, "uid": args.uid,
            "range": [args.start, args.end],
            "started_at": _iso(started_at), "ended_at": _iso(),
            "duration_s": duration_s,
            "delay_range_s": [args.delay_min, args.delay_max],
            "max_per_hour": args.max_per_hour, "batch_size": args.batch_size,
            "with_asr": args.with_asr, "risk_threshold": args.risk_threshold,
            "cookie_present": bool(os.environ.get("BILI_COOKIE")),
        },
        "summary": {
            "total": len(video_outcomes),
            "done": sum(1 for v in video_outcomes if v["outcome"] == "done"),
            "series": series_cnt,
            "enqueued": enqueued, "enqueue_skipped": skipped,
            "risk_skip": risk_skipped,
            "aborted_at": aborted,
            "errors": {**trace_summary["errors"],
                       "chunk_failed": sum(1 for v in video_outcomes if v["outcome"] == "chunk_failed"),
                       "video_error": sum(1 for v in video_outcomes if v["outcome"] == "error")},
            "cookie_events": cookie_events,
            **{k: v for k, v in trace_summary.items() if k != "errors"},
        },
        "videos": video_outcomes,
        "requests": all_trace,
    }
    ts_tag = datetime.fromtimestamp(started_at).strftime("%Y%m%d_%H%M%S")
    run_json = os.path.join(ROOT, "notes", "_scraped", f"{args.author}_backfill_{ts_tag}.json")
    run_logf = os.path.join(ROOT, "notes", "_scraped", f"{args.author}_backfill_{ts_tag}.log")
    with open(run_json, "w", encoding="utf-8") as f:
        json.dump(run_log, f, ensure_ascii=False, indent=1)
    with open(run_logf, "w", encoding="utf-8") as f:
        f.write("\n".join(_LOG_LINES) + "\n")

    if aborted is not None:
        _log(f"\n📋 运行日志：{run_logf}\n            {run_json}")
        sys.exit(87)
    _log(f"\n✅ 批量抓取完成 [{args.start}-{args.end}]，共 {len(video_outcomes)} 条结果 → {out_path}"
         + (f"（其中 {risk_skipped} 条因412被跳过记录，见 {_risk_skip_path(args.author)}）"
            if risk_skipped else ""))
    _log(f"   请求统计：共 {trace_summary['requests_total']} 次请求"
         f"（{trace_summary['density']['req_per_min']}/分钟，均隔 "
         f"{trace_summary['density']['avg_gap_s']}s），错误 "
         f"{sum(trace_summary['errors'].values())} 次 {trace_summary['errors']}")
    _log(f"   📋 运行日志：{run_logf}\n            {run_json}")
    if args.enqueue:
        _log(f"   队列：新入队 {enqueued} 条，跳过 {skipped} 条，系列 {series_cnt} 条。"
             f"消费：python scripts/filter_pending.py 清洗后派子 Agent 总结。")


if __name__ == "__main__":
    main()
