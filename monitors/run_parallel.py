"""monitors/run_parallel.py — 监控流水线并行编排器（P1, PLAN-20260828 §2/§3）。

设计（相对原串行 run.py 的改造）：
- 父进程：discover_all（沿用现有每源容错 try/except）+ save_state（seen 去重只在这里落盘，
  避免多 worker 并发写 state.json 竞态）。
- 按 route 拆分 all_new → 3 个 worker 子进程（spawn）：
    - wechat worker：apply_summaries(文章/动态) 落飞书
    - bili   worker：直接调用 apply_summaries（CC 字幕内联总结 + 无 CC 视频末尾统一走 ASR
                 有界池批量转写，与串行同源）；ASR 在 bili worker 进程内执行，不阻塞 wechat/scys
                 并发 worker，且每条无字幕视频只转写一次（无预热重复）。
    - scys   worker：run_scys_daily 增量抓 → 入 scys 待总结队列。
- 故障隔离：单源 worker 崩溃只记 ledger error，不影响其他源（父进程 join 不依赖返回值）。
- Landing 阶段（串行）：worker 已直接落飞书（P1 Design Y），这里补齐 series drain + 汇总 finalize。
- 顶层 .run.lock 互斥：run.py --parallel 与本文件共用，防两次运行重叠写竞态（§7.5 #4）。

入口：
  python monitors/run_parallel.py --mode auto --obsidian
  python monitors/run.py --parallel --mode auto   （run.py 内转发到此，保留旧串行回退）
"""

import os
import sys
import time
import argparse
import multiprocessing as mp

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from monitors.status_store import (  # 包式导入：脚本/包两种调用都安全
    new_run_id, record_task, finalize_run, detect_asr_max_concurrency,
)
from monitors.run_lock import acquire_run_lock, RunLockBusy
from monitors.run import (
    SCYS_PENDING_PATH, _load_json, _save_json,
    PENDING_SUMMARY_PATH, PENDING_REFETCH_PATH,
)


# ASR 转写统一在 monitors.run.apply_summaries 末尾的有界池完成（串行/并行同源），
# 此处不再单独预热，避免同一条无字幕视频被转写两次。


def run_bili_pipeline(items, obsidian):
    """B站 worker：直接复用 apply_summaries（CC 内联 + 无 CC 项末尾 ASR 有界池批量转写，
    与串行同源单一事实源）。ASR 在 bili worker 进程内跑，不阻塞 wechat/scys 并发 worker，
    且每条无字幕视频只转写一次（无预热重复）。"""
    from monitors import run as run_mod
    run_mod.apply_summaries(items, obsidian, consume_prev_refetch=False)


def _merge_stage_to_json(canonical: str, stages: list, key: str = "url",
                         keep_existing: bool = True) -> None:
    """把各 worker 的 staging 文件合并回 canonical（消除并发写同一 JSON 的竞态）。

    - keep_existing=True（pending_summaries）：保留 canonical 中已有条目（外部 Agent 未消费的）
      + 追加各 staging 新条目，按 key 去重。
    - keep_existing=False（pending_refetch）：旧队列已被父进程路由给 worker 重抓，canonical
      应由本轮 staging 的失败项重建（与串行 _save_json(refetch_next) 语义一致），不保留旧项。
    """
    existing = _load_json(canonical, []) if (keep_existing and os.path.exists(canonical)) else []
    seen = {e.get(key) for e in existing if isinstance(e, dict)}
    merged = list(existing)
    for st in stages:
        arr = _load_json(st, []) if os.path.exists(st) else []
        for e in arr:
            if not isinstance(e, dict):
                continue
            k = e.get(key)
            if k in seen:
                continue
            seen.add(k)
            merged.append(e)
    _save_json(canonical, merged)
    for st in stages:
        try:
            os.remove(st)
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# 单源 worker（spawn 子进程入口，模块级）
# ---------------------------------------------------------------------------

def run_source(source_name, items, mode, obsidian, run_id, asr_max,
               endpoint=None, summary_stage=None, refetch_stage=None):
    """单个源 worker：fetch + summarize + 落飞书（故障隔离）。

    items 含义：wechat/bili = 该源待处理条目；scys = scys 订阅条目列表。

    并发安全：wechat/bili 各自写独立 staging 文件（summary_stage / refetch_stage），
    由父进程合并回 canonical，避免多进程并发读写同一 JSON 造成静默覆盖丢失。
    会话复用：endpoint 非空时经 connect_over_cdp 接管父进程已建的同一浏览器（一次 kill 多 worker 共享）。
    """
    # P2: Worker 只写本地草稿（DRAFT_ONLY=1），不落飞书；Landing 阶段统一串行落盘，
    # 避免多 worker 并发打飞书 API 触发频限 / 重复节点竞态（§7.5 #2/#6）。
    os.environ["DRAFT_ONLY"] = "1"
    # 各 worker 写独立 staging 文件（仅 wechat/bili 触碰 pending 队列；scys 走自己的队列）
    if source_name in ("wechat", "bili"):
        if summary_stage:
            os.environ["MON_PENDING_SUMMARY_PATH"] = summary_stage
        if refetch_stage:
            os.environ["MON_PENDING_REFETCH_PATH"] = refetch_stage
    session = None
    if endpoint and source_name in ("wechat", "scys"):
        try:
            from shared.cdp_session import SharedCdpSession
            session = SharedCdpSession.from_endpoint(endpoint)
        except Exception as e:
            print(f"[worker:{source_name}] CDP endpoint 复用失败，回退独立会话: {e}",
                  file=sys.stderr)
            session = None
    try:
        from monitors import run as run_mod
        if source_name == "scys":
            run_mod.run_scys_daily(items, mode=mode, session=session)
            scys_pending = _load_json(SCYS_PENDING_PATH, [])
            record_task(run_id, "scys", "land:scys", "land", "ok",
                        title=f"scys pending {len(scys_pending)}")
        elif source_name == "bili":
            run_bili_pipeline(items, obsidian)
        else:  # wechat
            run_mod.apply_summaries(items, obsidian, session=session,
                                   consume_prev_refetch=False)
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
    finally:
        if session is not None:
            try:
                session.close()
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

    # 上一轮限流未抓到正文的文章：父进程在此按路由拆分给对应 worker 重抓，
    # 避免两个 worker 各自读写同一 pending_refetch.json（读-改-写竞态 → 覆盖丢失）。
    prev_refetch = _load_json(PENDING_REFETCH_PATH, [])
    wechat_prev = [r for r in prev_refetch if r.get("route") in ("article", "cv")]
    bili_prev = [r for r in prev_refetch if r.get("route") in ("video", "dynamic")]

    bili_items = [it for it in all_new if it.get("route") in ("video", "dynamic")] + bili_prev
    wechat_items = [it for it in all_new if it.get("route") in ("article", "cv")] + wechat_prev
    scys_entries = subs.get("scys", [])

    # 单一共享 CDP 会话（父进程创建，最多 kill 一次）：worker 经 endpoint connect_over_cdp 复用，
    # 不再各自 kill+clone（避免并发 clone 同一 CLONE_DIR 互相污染 + 重复开销）。
    endpoint = None
    parent_session = None
    if wechat_items or scys_entries:
        try:
            from shared.cdp_session import SharedCdpSession
            parent_session = SharedCdpSession()
            endpoint = parent_session.cdp_endpoint
        except Exception as e:
            print(f"[warn] 父进程 CDP 会话创建失败，worker 将各自回退独立会话: {e}",
                  file=sys.stderr)
            endpoint = None
            parent_session = None

    # 每个 worker 写独立 staging 文件，杜绝并发写同一 JSON 的竞态（父进程末尾合并）
    stage_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_status")
    os.makedirs(stage_dir, exist_ok=True)
    wechat_summary_stage = os.path.join(stage_dir, "pending_summaries.wechat.stage.json")
    bili_summary_stage = os.path.join(stage_dir, "pending_summaries.bili.stage.json")
    wechat_refetch_stage = os.path.join(stage_dir, "pending_refetch.wechat.stage.json")
    bili_refetch_stage = os.path.join(stage_dir, "pending_refetch.bili.stage.json")
    for f in (wechat_summary_stage, bili_summary_stage, wechat_refetch_stage, bili_refetch_stage):
        try:
            os.remove(f)
        except FileNotFoundError:
            pass

    # 2) 启 worker 子进程（故障隔离）
    ctx = mp.get_context("spawn")
    procs = []
    if bili_items:
        p = ctx.Process(target=run_source,
                       args=("bili", bili_items, mode, obsidian, run_id, asr_max,
                             endpoint, bili_summary_stage, bili_refetch_stage),
                       name="mon-bili")
        p.start(); procs.append(p)
    if wechat_items:
        p = ctx.Process(target=run_source,
                       args=("wechat", wechat_items, mode, obsidian, run_id, asr_max,
                             endpoint, wechat_summary_stage, wechat_refetch_stage),
                       name="mon-wechat")
        p.start(); procs.append(p)
    if scys_entries:
        p = ctx.Process(target=run_source,
                       args=("scys", scys_entries, mode, obsidian, run_id, asr_max,
                             endpoint, None, None),
                       name="mon-scys")
        p.start(); procs.append(p)

    for p in procs:
        p.join()  # 不依赖返回值，ledger 已记录

    # 各 worker staging → canonical 合并（消除并发写竞态；与串行语义一致）
    _merge_stage_to_json(PENDING_SUMMARY_PATH,
                         [wechat_summary_stage, bili_summary_stage], key="url",
                         keep_existing=True)
    _merge_stage_to_json(PENDING_REFETCH_PATH,
                         [wechat_refetch_stage, bili_refetch_stage], key="url",
                         keep_existing=False)

    # 释放父进程共享 CDP 会话（关闭驱动连接；活 Chrome 保持打开，克隆浏览器随之退出）
    if parent_session is not None:
        try:
            parent_session.close()
        except Exception:
            pass

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
