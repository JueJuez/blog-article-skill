"""monitors/status_store.py — 监控流水线状态 Ledger（模型可查询 / 可重驱）

设计（PLAN-20260828-parallel-monitor.md §4 / §3.5）：
- 每一条任务写入 append-only JSONL，**按 run_id + shard（默认 source）分片**，
  不同 worker 子进程各写各的分片，无 read-modify-write 竞争；聚合器收尾合并。
- 字段扁平、固定，模型可用脚本或直接读 JSON 过滤（"取 last run 全部 status=timeout 的 bili 项"）。
- 失败项跨运行追加到 failures.jsonl（按 item_id 去重），供 redrive 重抓。
- 落点：`monitors/run_status/<run_id>.<shard>.tasks.jsonl` + `latest.json` + `failures.jsonl`。

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
FAILURES_PATH = os.path.join(RUN_STATUS_DIR, "failures.jsonl")
LATEST_PATH = os.path.join(RUN_STATUS_DIR, "latest.json")

# P3 重驱投递目标队列（与 run.py 定义保持一致）
PENDING_SUMMARY_PATH = os.path.join(BASE_DIR, "pending_summaries.json")
SCYS_PENDING_PATH = os.path.join(os.path.dirname(BASE_DIR), "notes", "_scraped", "scys", "pending_summaries.json")

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
# 失败项（跨运行追加，按 item_id 去重）
# ---------------------------------------------------------------------------

def add_failure(
    run_id: str,
    source: str,
    item_id: str,
    stage: str,
    status: str,
    *,
    url: str = "",
    title: str = "",
    error: str = "",
    attempts: int = 0,
) -> None:
    """追加一条失败项到 failures.jsonl（跨运行）。同一 item_id 只保留最新一次。"""
    os.makedirs(RUN_STATUS_DIR, exist_ok=True)
    failures = _load_failures()
    new_rec = {
        "run_id": run_id,
        "source": source,
        "item_id": item_id,
        "stage": stage,
        "status": status,
        "url": url,
        "title": title,
        "error": error,
        "attempts": attempts,
        "ts": time.time(),
    }
    replaced = False
    for i, rec in enumerate(failures):
        if rec.get("item_id") == item_id and rec.get("source") == source:
            failures[i] = new_rec
            replaced = True
            break
    if not replaced:
        failures.append(new_rec)
    with _local_lock:
        with open(FAILURES_PATH, "w", encoding="utf-8") as f:
            for rec in failures:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _load_failures() -> List[Dict[str, Any]]:
    if not os.path.exists(FAILURES_PATH):
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(FAILURES_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return []
    return out


def list_failures(
    source: Optional[str] = None,
    status: Optional[str] = None,
    since: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """查询失败项。status 可为逗号分隔多值（OR）。"""
    failures = _load_failures()
    statuses = set(s for s in (status or "").split(",") if s)
    out = []
    for rec in failures:
        if source and rec.get("source") != source:
            continue
        if statuses and rec.get("status") not in statuses:
            continue
        if since is not None and float(rec.get("ts", 0)) < float(since):
            continue
        out.append(rec)
    return out


def redrive_items(
    source: Optional[str] = None,
    status: Optional[str] = None,
    max_attempts: int = 3,
    stale_transcribing_secs: float = 1800.0,
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """选出可重驱的失败项。

    过滤器：status in (failed, timeout, pending, transcribe_failed) 且 attempts<max_attempts；
    并自动复位「陈旧 transcribing」条目（卡超 stale_transcribing_secs）→ 一并重驱（审计 #4）。
    status 参数若给定，与默认集合取交集（便于手动 `redrive --status timeout`）。
    """
    now = now if now is not None else time.time()
    default_statuses = {"failed", "timeout", "pending", "transcribe_failed"}
    if status:
        wanted = set(s for s in status.split(",") if s)
        default_statuses &= wanted
    picked = []
    for rec in _load_failures():
        if source and rec.get("source") != source:
            continue
        st = rec.get("status")
        if st == "transcribing":
            # 陈旧 transcribing → 复位为 pending 重驱
            age = now - float(rec.get("ts", 0))
            if age >= stale_transcribing_secs:
                rec["status"] = "pending"
                picked.append(rec)
            continue
        if st not in default_statuses:
            continue
        if int(rec.get("attempts", 0)) >= max_attempts:
            continue
        picked.append(rec)
    return picked


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


# ---------------------------------------------------------------------------
# P3 重驱投递：把 failure 重新投入到对应 pending 队列（供下次 run / 模型派单重试）
# ---------------------------------------------------------------------------

def _append_json_arr(path: str, items: List[Dict[str, Any]]) -> int:
    """把 items 追加到 path（JSON 数组文件）；文件不存在或格式损坏则重建为数组。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    arr: List[Dict[str, Any]] = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                arr = data
        except (json.JSONDecodeError, FileNotFoundError):
            arr = []
    arr.extend(items)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(arr, f, ensure_ascii=False, indent=2)
    return len(items)


def deliver_redrive(recs: List[Dict[str, Any]]) -> Dict[str, int]:
    """把 redrive_items 选出的失败项重新投递到对应 pending 队列（P3）。

    - bili/wechat → monitors/pending_summaries.json（单篇队列，下次 run / 模型派单重试）
    - scys        → notes/_scraped/scys/pending_summaries.json（scys 队列）

    每条构造最小可重试条目（携带 url/title/redrive 标记 / 递增 attempts / 原始错误），
    供下游重新 fetch+总结。返回投递统计 {"single": n, "scys": m}。
    """
    single: List[Dict[str, Any]] = []
    scys: List[Dict[str, Any]] = []
    for r in recs:
        src = (r.get("source") or "").lower()
        base = {
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "original_title": r.get("title", ""),
            "redrive": True,
            "redrive_error": r.get("error", ""),
            "attempts": int(r.get("attempts", 0)) + 1,
        }
        if src == "scys":
            base["route"] = "scys"
            scys.append(base)
        else:
            base["route"] = "video" if src == "bili" else "article"
            single.append(base)
    stats = {"single": 0, "scys": 0}
    if single:
        stats["single"] = _append_json_arr(PENDING_SUMMARY_PATH, single)
    if scys:
        stats["scys"] = _append_json_arr(SCYS_PENDING_PATH, scys)
    return stats
