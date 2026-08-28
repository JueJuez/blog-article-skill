"""monitors/status_store.py — 监控流水线状态 Ledger（模型可查询）

设计（PLAN-20260828-parallel-monitor.md §4 / §3.5）：
- 每一条任务写入 append-only JSONL，**按 run_id + shard（默认 source）分片**，
  不同 worker 子进程各写各的分片，无 read-modify-write 竞争；聚合器收尾合并。
- 字段扁平、固定，模型可用脚本或直接读 JSON 过滤（"取 last run 全部 status=timeout 的 bili 项"）。
- 落点：`monitors/run_status/<run_id>.<shard>.tasks.jsonl` + `latest.json`。

跨运行重抓（失败项不丢）由 apply_summaries 内的 pending_refetch.json 负责（与公众号同源：
持久化到磁盘、下轮自动重载、3 次上限后显式 drop 上报），本模块不重复实现该能力。

本模块不依赖任何项目内部模块，可单测、可独立 import。
"""

import os
import json
import time
import glob
import threading
from typing import Optional, Dict, Any, List

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUN_STATUS_DIR = os.path.join(BASE_DIR, "run_status")
LATEST_PATH = os.path.join(RUN_STATUS_DIR, "latest.json")

# 同一进程内多 ASR 子线程向同一分片追加时的轻量互斥（跨进程靠分片隔离，无需文件锁）
_local_lock = threading.Lock()

# ---------------------------------------------------------------------------
# 资源探测：ASR 有界池并发数（PLAN D3）
# ---------------------------------------------------------------------------

def detect_asr_max_concurrency(default: Optional[int] = None) -> int:
    """返回 ASR 子进程池最大并发数。

    - 显式 env ASR_MAX_CONCURRENCY 优先；
    - 否则按资源推导：ctranslate2 能看见 CUDA 设备（>0）→ 2（有独显，如本机 RTX 4060）；
      否则 1（纯 CPU）。
    不依赖 torch：faster-whisper 走 ctranslate2 自带 CUDA 运行时。
    """
    env = os.environ.get("ASR_MAX_CONCURRENCY")
    if env and env.strip().isdigit():
        return max(1, int(env.strip()))
    if default is not None:
        return max(1, int(default))
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return 2
    except Exception:
        pass
    return 1


# ---------------------------------------------------------------------------
# run_id / 路径
# ---------------------------------------------------------------------------

def new_run_id(ts: Optional[str] = None) -> str:
    """生成运行级 ID（YYYYMMDD-HHMMSS）。"""
    return ts or time.strftime("%Y%m%d-%H%M%S")


def _shard_path(run_id: str, shard: str) -> str:
    os.makedirs(RUN_STATUS_DIR, exist_ok=True)
    return os.path.join(RUN_STATUS_DIR, f"{run_id}.{shard}.tasks.jsonl")


# ---------------------------------------------------------------------------
# 任务记录（append-only，分片）
# ---------------------------------------------------------------------------

def record_task(
    run_id: str,
    source: str,
    item_id: str,
    stage: str,
    status: str,
    *,
    url: str = "",
    title: str = "",
    error: str = "",
    retry_count: int = 0,
    node_token: str = "",
    ts: Optional[float] = None,
    shard: Optional[str] = None,
) -> None:
    """追加一条任务状态到对应分片 JSONL。

    shard 默认取 source；ASR 子进程可传 shard="asr" 区分转写阶段。
    字段：run_id, source, item_id, url, title, stage, status, ts, error, retry_count, node_token。
    """
    shard = shard or source or "main"
    rec = {
        "run_id": run_id,
        "source": source,
        "item_id": item_id,
        "url": url,
        "title": title,
        "stage": stage,
        "status": status,
        "ts": ts if ts is not None else time.time(),
        "error": error,
        "retry_count": retry_count,
        "node_token": node_token,
    }
    line = json.dumps(rec, ensure_ascii=False)
    path = _shard_path(run_id, shard)
    with _local_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def read_run_tasks(run_id: str) -> List[Dict[str, Any]]:
    """读取某次运行的所有分片任务记录（合并）。"""
    recs: List[Dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(RUN_STATUS_DIR, f"{run_id}.*.tasks.jsonl"))):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        recs.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            continue
    return recs


# ---------------------------------------------------------------------------
# 运行级汇总 / finalize
# ---------------------------------------------------------------------------

def finalize_run(
    run_id: str,
    *,
    started_ts: float,
    summary_counts: Optional[Dict[str, Dict[str, int]]] = None,
    overall_status: str = "ok",
    notes: str = "",
) -> Dict[str, Any]:
    """聚合本次运行所有分片 → latest.json。

    summary_counts: {source: {discovered, fetched, landed, failed, skipped}}。
    返回 latest 字典。
    """
    os.makedirs(RUN_STATUS_DIR, exist_ok=True)
    tasks = read_run_tasks(run_id)
    # 若调用方没给汇总，则从分片自行聚合（按 source × stage/status）
    if not summary_counts:
        summary_counts = {}
        for rec in tasks:
            src = rec.get("source", "unknown")
            d = summary_counts.setdefault(src, {})
            st = rec.get("status", "unknown")
            d[st] = d.get(st, 0) + 1

    latest = {
        "run_id": run_id,
        "started_ts": started_ts,
        "finished_ts": time.time(),
        "duration_secs": round(time.time() - started_ts, 1),
        "overall_status": overall_status,
        "per_source": summary_counts,
        "task_count": len(tasks),
        "notes": notes,
    }
    with _local_lock:
        with open(LATEST_PATH, "w", encoding="utf-8") as f:
            json.dump(latest, f, ensure_ascii=False, indent=2)
    return latest


def get_latest() -> Optional[Dict[str, Any]]:
    if not os.path.exists(LATEST_PATH):
        return None
    try:
        with open(LATEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def list_runs() -> List[str]:
    """列出所有 run_id（按时间倒序）。"""
    ids = set()
    for path in glob.glob(os.path.join(RUN_STATUS_DIR, "*.tasks.jsonl")):
        base = os.path.basename(path)
        # <run_id>.<shard>.tasks.jsonl
        parts = base.split(".")
        if len(parts) >= 4 and parts[-2] == "tasks" and parts[-1] == "jsonl":
            ids.add(".".join(parts[:-3]))
    return sorted(ids, reverse=True)


# (end of module)
