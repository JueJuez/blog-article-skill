"""monitors/run_parallel.py — 监控流水线并行编排器（P1, PLAN-20260828 §2/§3）。

设计（相对原串行 run.py 的改造）：
- 父进程：discover_all（沿用现有每源容错 try/except）+ save_state（seen 去重只在这里落盘，
  避免多 worker 并发写 state.json 竞态）。
- 按 route 拆分 all_new → 3 个 worker 子进程（spawn）：
    - wechat worker：apply_summaries(文章/动态) 落飞书
    - bili   worker：先按 fetch_transcript 把「无 CC 字幕」视频投递到 ASR 有界子进程池
                 （ASR_MAX_CONCURRENCY，默认按资源探测：有 CUDA=2 / 无=1）预热缓存，
                 其余视频/源不被这条阻塞；随后 apply_summaries 统一落盘（无 CC 项命中缓存→快）。
    - scys   worker：run_scys_daily 增量抓 → 入 scys 待总结队列。
- 故障隔离：单源 worker 崩溃只记 ledger error，不影响其他源（父进程 join 不依赖返回值）。
- Landing 阶段（串行）：worker 已直接落飞书（P1 Design Y），这里补齐 series drain + 汇总 finalize。
- 顶层 .run.lock 互斥：run.py --parallel 与本文件共用，防两次运行重叠写竞态（§7.5 #4）。

入口：
  python monitors/run_parallel.py --mode auto --obsidian
  python monitors/run.py --parallel --mode auto   （run.py 内转发到此，保留旧串行回退）
"""

import os
import re
import sys
import time
import argparse
import multiprocessing as mp

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from monitors.status_store import (  # 包式导入：脚本/包两种调用都安全
    new_run_id, record_task, add_failure, finalize_run, detect_asr_max_concurrency,
)
from monitors.run_lock import acquire_run_lock, RunLockBusy


# ---------------------------------------------------------------------------
# ASR 有界子进程池（模块级 worker，便于 spawn pickle）
# ---------------------------------------------------------------------------

def asr_worker(url: str, out_dir: str):
    """子进程转写单条 url → 写 transcript 文件。返回 (url, ok, detail)。"""
    import os
    from videos import asr as asr_mod
    safe = re.sub(r"\W+", "_", url)[-48:]
    out = os.path.join(out_dir, f"{safe}.txt")
    try:
        r = asr_mod.transcribe_to_file(url, out)
        return (url, bool(r), str(r) if r else "empty")
    except Exception as e:  # 单条失败不影响池
        return (url, False, str(e)[:200])


def _warm_asr_cache(items, run_id, asr_max):
    """把无 CC 字幕的 bili 视频投递到 ASR 有界池预热缓存；返回 (ok_ids, failed_items)。"""
    import tempfile
    import concurrent.futures as cf
    out_dir = tempfile.mkdtemp(prefix="asr_parallel_")
    try:
        with cf.ProcessPoolExecutor(max_workers=asr_max,
                                    mp_context=mp.get_context("spawn")) as ex:
            futs = {ex.submit(asr_worker, it["url"], out_dir): it for it in items}
            for fut in cf.as_completed(futs):
                it = futs[fut]
                iid = it.get("item_id", it["url"])
                try:
                    url, ok, detail = fut.result()
                    record_task(run_id, "bili", iid, "transcribe",
                                "ok" if ok else "transcribe_failed",
                                shard="asr", url=url, title=it.get("title", ""),
                                error="" if ok else str(detail)[:200])
                    if not ok:
                        add_failure(run_id, "bili", iid, "transcribe", "transcribe_failed",
                                    url=url, title=it.get("title", ""),
                                    error=str(detail)[:200], attempts=1)
                except Exception as e:
                    record_task(run_id, "bili", iid, "transcribe", "transcribe_failed",
                                shard="asr", url=it["url"], title=it.get("title", ""),
                                error=str(e)[:200])
                    add_failure(run_id, "bili", iid, "transcribe", "transcribe_failed",
                                url=it["url"], title=it.get("title", ""),
                                error=str(e)[:200], attempts=1)
    except Exception as e:
        print(f"[warn] ASR 池初始化/执行失败，回退串行 ASR: {e}", file=sys.stderr)
        return  # 回退：apply_summaries 内部会同步 ASR


def run_bili_pipeline(items, mode, obsidian, run_id, asr_max):
    """B站 worker：无 CC 视频先异步转写预热缓存，再统一 apply（CC 内联 / 无 CC 命中缓存）。"""
    from monitors import run as run_mod
    from videos.fetch import fetch_transcript

    asr_items, cc_items = [], []
    for it in items:
        if it.get("is_charging"):
            cc_items.append(it)
            continue
        try:
            cc = fetch_transcript(it["url"])
        except Exception:
            cc = "ERR"  # 探测异常 → 交给 apply_summaries 自行处理（保守内联）
        if cc is None:
            asr_items.append(it)
        else:
            cc_items.append(it)

    if asr_items and asr_max > 1:
        print(f"[bili] {len(asr_items)} 条无字幕，投递 ASR 有界池（并发={asr_max}）预热缓存...",
              file=sys.stderr)
        _warm_asr_cache(asr_items, run_id, asr_max)
    elif asr_items:
        print(f"[bili] {len(asr_items)} 条无字幕（并发=1，串行 ASR）", file=sys.stderr)

    # 统一落盘：CC 内联；无 CC 项已预热→transcribe_video 命中缓存快速返回，不阻塞其他源
    run_mod.apply_summaries(items, obsidian)


# ---------------------------------------------------------------------------
# 单源 worker（spawn 子进程入口，模块级）
# ---------------------------------------------------------------------------

def run_source(source_name, items, mode, obsidian, run_id, asr_max):
    """单个源 worker：fetch + summarize + 落飞书（故障隔离）。

    items 含义：wechat/bili = 该源待处理条目；scys = scys 订阅条目列表。
    """
    # P2: Worker 只写本地草稿（DRAFT_ONLY=1），不落飞书；Landing 阶段统一串行落盘，
    # 避免多 worker 并发打飞书 API 触发频限 / 重复节点竞态（§7.5 #2/#6）。
    os.environ["DRAFT_ONLY"] = "1"
    try:
        from monitors import run as run_mod
        if source_name == "scys":
            run_mod.run_scys_daily(items, mode=mode)
            from monitors.run import SCYS_PENDING_PATH, _load_json
            scys_pending = _load_json(SCYS_PENDING_PATH, [])
            record_task(run_id, "scys", "land:scys", "land", "ok",
                        title=f"scys pending {len(scys_pending)}")
        elif source_name == "bili":
            run_bili_pipeline(items, mode, obsidian, run_id, asr_max)
        else:  # wechat
            run_mod.apply_summaries(items, obsidian)
            record_task(run_id, "wechat", "land:wechat", "land", "ok",
                        title=f"processed {len(items)}")
        print(f"[worker:{source_name}] done", file=sys.stderr)
    except Exception as e:
        import traceback
        print(f"[worker:{source_name}] FAILED: {type(e).__name__} {e}", file=sys.stderr)
        traceback.print_exc()
        try:
            record_task(run_id, source_name, f"worker:{source_name}", "land", "error",
                        error=str(e)[:300])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 并行主流程
# ---------------------------------------------------------------------------

def run_parallel_main(mode: str = "auto", obsidian: bool = False,
                      all_videos: bool = False, asr_max: int = 0) -> None:
    from monitors import run as run_mod

    # 顶层互斥锁：防两次运行重叠写 ledger/pending（§7.5 #4）
    try:
        lock = acquire_run_lock()
    except RunLockBusy as e:
        print(f"⚠️ {e}；本进程退出，避免重叠运行。", file=sys.stderr)
        return

    run_id = new_run_id()
    started = time.time()
    print(f"🚀 并行监控启动 run_id={run_id}（--parallel）", file=sys.stderr)

    subs = run_mod.load_subscriptions()
    asr_max = asr_max or detect_asr_max_concurrency()

    # 1) 父进程 discover（沿用现有每源容错）+ 落 state（只在父进程，避免竞态）
    all_new = run_mod.discover_all(subs, run_mod.load_state(), mode=mode, force_all=all_videos)
    run_mod._ledger_record_discover(run_id, all_new)
    run_mod.save_state(run_mod.load_state())

    if not all_new and not subs.get("scys"):
        print("ℹ️ 本轮无新内容、无 scys 源。", file=sys.stderr)

    bili_items = [it for it in all_new if it.get("route") in ("video", "dynamic")]
    wechat_items = [it for it in all_new if it.get("route") in ("article", "cv")]
    scys_entries = subs.get("scys", [])

    # 2) 启 worker 子进程（故障隔离）
    ctx = mp.get_context("spawn")
    procs = []
    if bili_items:
        p = ctx.Process(target=run_source, args=("bili", bili_items, mode, obsidian, run_id, asr_max),
                        name="mon-bili")
        p.start(); procs.append(p)
    if wechat_items:
        p = ctx.Process(target=run_source, args=("wechat", wechat_items, mode, obsidian, run_id, asr_max),
                        name="mon-wechat")
        p.start(); procs.append(p)
    if scys_entries:
        p = ctx.Process(target=run_source, args=("scys", scys_entries, mode, obsidian, run_id, asr_max),
                        name="mon-scys")
        p.start(); procs.append(p)

    for p in procs:
        p.join()  # 不依赖返回值，ledger 已记录

    # 3) Landing 阶段（串行）：worker 已写本地 draft / scys 已入队列；这里统一落飞书
    #    防御：清除 DRAFT_ONLY，确保父进程（Landing）一定落飞书，不被外部环境变量污染。
    os.environ.pop("DRAFT_ONLY", None)
    try:
        from articles.main import land_drafts
        n = land_drafts(obsidian=obsidian)
        print(f"  📥 Landing 已从本地草稿落盘 {n} 篇（串行，避免并发飞书竞态）")
    except Exception as e:
        print(f"  ⚠️ Landing 落盘异常（非致命）：{e}")
    # 系列课 drain 幂等补齐（沿用 series 级 drain 锁）
    try:
        from monitors.apply_pending_series import drain_series_pending
        drain_series_pending(obsidian=obsidian)
    except Exception as e:
        print(f"  ⚠️ 系列课落地异常（非致命）：{e}")

    finalize_run(run_id, started_ts=started, overall_status="ok", notes="parallel")
    print(f"\n📋 Ledger：monitors/run_status/{run_id}.*.tasks.jsonl"
          f"（查询：python monitors/status_cli.py summary）")
    lock.release()


def main():
    parser = argparse.ArgumentParser(description="订阅监控（并行编排）")
    parser.add_argument("--mode", choices=["auto", "first"], default="auto")
    parser.add_argument("--first-run", action="store_true")
    parser.add_argument("--obsidian", action="store_true")
    parser.add_argument("--all-videos", action="store_true")
    parser.add_argument("--asr-max", type=int, default=0,
                        help="覆盖 ASR 并发数（默认按资源探测：有 CUDA=2 / 无=1）")
    args = parser.parse_args()
    mode = "first" if args.first_run else args.mode
    run_parallel_main(mode=mode, obsidian=args.obsidian,
                      all_videos=args.all_videos, asr_max=args.asr_max)


if __name__ == "__main__":
    main()
