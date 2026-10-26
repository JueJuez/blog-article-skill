"""shared/async_review.py — 落盘后异步审核队列（T7 ②，PLAN-20261018 §6 A3）

**要解决的问题**：全套独立语义审核（`prompts.semantic_review.review_fulltext`：分块全文、
独立会话审者、coverage=full 才认）早已建好，但只能「落盘前阻塞」且默认关 —— 打开就拖慢
生产、关掉就永远不审。两者都不是我们想要的。

**改成三段**：
   落盘（照常）→ 入队（只写一行 JSONL，零 AI 成本）→ 有人/有空时跑队列（`scripts/run_async_review.py`）

这样审核不再决定「这篇能不能落盘」，只决定「这篇后来被验成了什么」。与 §3.3 的
「稿件不丢」同一条原则：**先有产物，再谈判定**。

## 为什么必须绑 hash（§10 第 4 项）

后台审的时候稿可能已经被改过（重做、手工修字）。没有 hash，旧版的结论会冒充当前稿。
故每条记录固定 `note_sha256` / `source_sha256` / `prompt_sha256`（`prompts.provenance` 同口径），
消费端跑之前先复核笔记 hash：变了 → 标 `stale`，不审、不覆盖结论。

## 为什么默认「触发式」而不是全量

历史教训（2026-09-15/16）：`_review_queue.jsonl` 曾被 300+ 条假信号淹没，最后整档清空。
故新队列**独立成文件**（`notes/_meta/async_review_queue.jsonl`，不复用旧档），并且只在
①机械信号命中（`review_flags` 非空）或 ②按比例抽样 时入队。

## 开关

- `NOTE_ASYNC_REVIEW`：`0` 关闭 / `1` 触发式（默认）/ `all` 每篇新笔记都入队
- `NOTE_ASYNC_REVIEW_RATE`：触发式的抽样比例，默认 0.15

⚠️ 入队是免费的（一行 JSONL）；**花钱的是消费端**，那个必须显式跑。
"""
from __future__ import annotations

import hashlib
import json
import os
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE = os.path.join(_ROOT, "notes", "_meta", "async_review_queue.jsonl")
RESULT_DIR = os.path.join(_ROOT, "notes", "_meta", "async_reviews")

ENABLE_ENV = "NOTE_ASYNC_REVIEW"
RATE_ENV = "NOTE_ASYNC_REVIEW_RATE"
DEFAULT_RATE = 0.15

STATUSES = ("pending", "passed", "failed", "stale", "skipped", "error")
_OPEN_STATUSES = ("pending",)
# 「有效结论」：只有这两个状态代表真的审过并给出了判定。
# 同一稿同一版本已有有效结论时，消费端不再派单、不覆盖（S1 不重复花钱 / 不覆盖结论）。
VALID_CONCLUSIONS = ("passed", "failed")


def digest(text: str) -> str:
    """与 prompts.provenance / semantic_review 同口径（UTF-8 文本 SHA256）。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def file_digest(path: str) -> str:
    """读文本算 hash（换行按文本模式规范化，与 provenance 一致）；读不到返回 ""。"""
    try:
        with open(path, encoding="utf-8") as fh:
            return digest(fh.read())
    except (OSError, UnicodeError):
        return ""


def mode() -> str:
    """当前入队模式：`0` / `1` / `all`（大小写与空白已归一）。"""
    raw = (os.environ.get(ENABLE_ENV) or "1").strip().lower()
    if raw in ("0", "off", "false", "no", "disabled"):
        return "0"
    if raw in ("all", "always"):
        return "all"
    return "1"


def rate() -> float:
    try:
        return max(0.0, min(1.0, float(os.environ.get(RATE_ENV, DEFAULT_RATE))))
    except ValueError:
        return DEFAULT_RATE


def sampled(rng=None) -> bool:
    """触发式抽样（比例来自 env，默认 15%）。rng 可注入，便于测试可复现。"""
    import random
    r = rng or random
    return r.random() < rate()


# ---------------------------------------------------------------- 写入端

def enqueue(note_path: str, *, source_path: str = "", note_path_rel: str = "",
            note_type: str = "", url: str = "", title: str = "", content: str = "",
            source_text: str = "", reasons=None, prompt_sha256: str = "unknown",
            note_sha256: str = "", source_sha256: str = "", extra: dict = None,
            queue: str = "") -> dict:
    """把一篇已落盘的笔记写进待审队列。**永不抛异常**。

    源文本会**快照**到 `notes/_meta/async_reviews/sources/<id>.txt`（仅入队的那部分，
    默认约 15% 的新笔记）：后台审时要的是「当时那份源」，而 `_raw_` 可能被清理、
    重抓也可能覆盖 —— 这就是 Brain `[cite:N]` 的思路：**引用是指针，判据要留证**。

    Returns: {"queued": bool, "id": str, "reason": str}
    """
    try:
        queue = queue or QUEUE          # 运行时解析，便于测试/多队列隔离
        path = note_path or ""
        note_hash = note_sha256 or (file_digest(path) if path else (digest(content) if content else ""))
        if not note_hash:
            return {"queued": False, "id": "", "reason": "no_content"}
        if not path:
            return {"queued": False, "id": "", "reason": "no_note_path"}

        for rec in load(queue):
            if (rec.get("note_path") == path and rec.get("note_sha256") == note_hash
                    and rec.get("status") in _OPEN_STATUSES):
                return {"queued": False, "id": rec.get("id", ""), "reason": "already_pending"}

        rec = {
            "id": f"{time.strftime('%Y%m%d%H%M%S')}-{note_hash[:8]}",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "note_path": path,
            "note_path_rel": note_path_rel or "",
            "note_sha256": note_hash,
            "note_chars": len(content or ""),
            "source_path": source_path or "",
            "source_sha256": source_sha256 or (file_digest(source_path) if source_path else "unknown"),
            "source_chars": len(source_text or ""),
            "prompt_sha256": prompt_sha256 or "unknown",
            "note_type": note_type or "",
            "url": url or "",
            "title": title or "",
            "reasons": list(reasons or []),
            "status": "pending",
            "reviewed_at": "",
        }
        if source_text:
            snap = _save_source_snapshot(rec["id"], source_text)
            if snap:
                rec["source_origin"] = source_path or ""
                rec["source_path"] = snap
                rec["source_sha256"] = digest(source_text)
                rec["source_snapshot"] = True
        if extra:
            rec.update(extra)
        os.makedirs(os.path.dirname(queue), exist_ok=True)
        with open(queue, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {"queued": True, "id": rec["id"], "reason": "ok"}
    except Exception as exc:                      # 保险丝：审核入队绝不影响落盘
        return {"queued": False, "id": "", "reason": f"{type(exc).__name__}: {exc}"}


def _save_source_snapshot(rec_id: str, source_text: str) -> str:
    """源文本快照（随队入档）；写失败返回 ""（退回用原 source_path）。"""
    try:
        d = os.path.join(RESULT_DIR, "sources")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"{rec_id}.txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(source_text)
        return p
    except (OSError, UnicodeError):
        return ""


def maybe_enqueue(note_path: str, *, reasons=None, rng=None, **kwargs) -> dict:
    """生产落盘点调用：按 env 开关 + 抽样决定是否入队。**永不抛异常、绝不打印噪音**。

    触发条件（`NOTE_ASYNC_REVIEW=1` 时）：`reasons` 非空 **或** 被抽样命中。
    """
    try:
        m = mode()
        if m == "0" or not note_path:
            return {"queued": False, "id": "", "reason": "disabled"}
        if m != "all":
            if not reasons and not sampled(rng=rng):
                return {"queued": False, "id": "", "reason": "not_sampled"}
        return enqueue(note_path, reasons=reasons, **kwargs)
    except Exception as exc:
        return {"queued": False, "id": "", "reason": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------- 读取 / 更新

def load(queue: str = "") -> list:
    queue = queue or QUEUE
    out = []
    if not os.path.exists(queue):
        return out
    with open(queue, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def pending(limit: int = 0, queue: str = "") -> list:
    queue = queue or QUEUE
    rows = [r for r in load(queue) if r.get("status") in _OPEN_STATUSES]
    return rows[:limit] if limit else rows


def update(rec_id: str, *, status: str, result: dict = None, queue: str = "",
           force: bool = False) -> bool:
    """按 id 改一条记录的状态（原子重写：同目录临时文件 + os.replace）。

    **有效结论不回退**：已经是 `passed`/`failed` 的记录，默认不允许改成
    `error`/`stale`/`skipped`（返回 False），避免重跑/过期检查把真审过的结论冲掉。
    确需覆盖（例如人工纠错）显式传 `force=True`。
    """
    if status not in STATUSES:
        raise ValueError(f"未知状态: {status}")
    queue = queue or QUEUE
    rows = load(queue)
    hit = False
    for rec in rows:
        if rec.get("id") == rec_id:
            if (rec.get("status") in VALID_CONCLUSIONS and status not in VALID_CONCLUSIONS
                    and not force):
                return False
            rec["status"] = status
            rec["reviewed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            if result is not None:
                rec["result"] = result
            hit = True
            break
    if not hit:
        return False
    os.makedirs(os.path.dirname(queue), exist_ok=True)
    tmp = queue + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for rec in rows:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.replace(tmp, queue)
    return True


def stats(queue: str = "") -> dict:
    """按状态计数 + 是否可消费（队列不存在时全 0）。"""
    queue = queue or QUEUE
    counts = {s: 0 for s in STATUSES}
    for rec in load(queue):
        counts[rec.get("status", "error")] = counts.get(rec.get("status", "error"), 0) + 1
    counts["total"] = sum(counts[s] for s in STATUSES)
    return counts


def save_result(rec: dict, report: dict, result_dir: str = "") -> str:
    """把整份审核报告另存（队列里只留摘要，全文占用太大）。返回路径，失败返回 ""。"""
    try:
        result_dir = result_dir or RESULT_DIR
        os.makedirs(result_dir, exist_ok=True)
        path = os.path.join(result_dir, f"{rec.get('id', 'unknown')}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        return path
    except (OSError, TypeError):
        return ""
