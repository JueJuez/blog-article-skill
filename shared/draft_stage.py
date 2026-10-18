"""shared/draft_stage.py — 被拦稿件的暂存区（T7 · 稿件不丢）

来源：`docs/plans/PLAN-20261018-knowledge-layer.md` §3.3 第 4 步 + §6 A3。

**为什么必须有它**：现流程是「门禁不过 → 不落盘 → 子 Agent 重写，两次仍失败则跳过」，
**稿会丢**。方法论（writing-studio）把「迭代前提是稿件可保留/回退/比较」列为三大底层认知
之一；Brain 的 staged output tree 是同一原则：**先有暂存物，再谈改不改**。
没有暂存物，后续所有「定向修订 / 先比后换」都无从落地。

本模块只做一件事：**在拦截点把候选稿连同它的身份写下来**，以便之后：
① 能按 hash 证明「当时审/拒的是哪一版」；② 能在它上面改，而不是从头再来一遍；
③ 多篇比稿时有一致的输入。

## 硬约束

- **绝不抛异常**：暂存是观测与保险，任何失败都只能告警（`ok=False` + 原因），不许影响主流程。
- **绝不写 vault**：落点是项目私有目录 `notes/_meta/staged_drafts/`，读者是我们自己，不进知识库。
- **必须绑 hash**：`source_sha256` / `note_sha256` / `prompt_sha256`（拿得到才记，不猜）。
  没有 hash 的暂存在异步场景里毫无意义 —— 稿早就被改过，结论会张冠李戴（§10 第 4 项）。
"""
from __future__ import annotations

import hashlib
import json
import os
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGE_BASE = os.path.join(_ROOT, "notes", "_meta", "staged_drafts")


def digest(text: str) -> str:
    """与 prompts.provenance / semantic_review 同口径（UTF-8 文本 SHA256）。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _slug(text: str, limit: int = 40) -> str:
    keep = [c for c in (text or "untitled")
            if c.isalnum() or c in ("-", "_", "．", "·") or "\u4e00" <= c <= "\u9fff"]
    s = "".join(keep).strip("-_")
    return s[:limit] or "untitled"


def _unique_path(dirpath: str, base: str, ext: str) -> str:
    p = os.path.join(dirpath, base + ext)
    if not os.path.exists(p):
        return p
    for i in range(1, 1000):
        p = os.path.join(dirpath, f"{base}-{i}{ext}")
        if not os.path.exists(p):
            return p
    return os.path.join(dirpath, f"{base}-{int(time.time())}{ext}")


def stage_rejected(content: str, *, reason: str, issues=None, warnings=None,
                   url: str = "", title: str = "", note_type: str = "",
                   source_text: str = "", source_path: str = "",
                   prompt_sha256: str = "", evidence: dict = None,
                   extra: dict = None, stage_dir: str = "", route: str = "") -> dict:
    """把被拦的候选稿写进暂存区（含伴生的元信息 JSON）。

    Returns: {"ok": True, "draft": path, "meta": path, "note_sha256": h, ...}
             或 {"ok": False, "error": str} —— **永不抛异常**。
    """
    try:
        base_dir = stage_dir or os.path.join(STAGE_BASE, time.strftime("%Y%m%d"))
        os.makedirs(base_dir, exist_ok=True)
        stamp = time.strftime("%H%M%S")
        name = _slug(title or _slug(url, 24))[:40]
        draft_path = _unique_path(base_dir, f"{stamp}_{name}", ".md")
        meta_path = draft_path[:-3] + ".json"

        meta = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "reason": reason,                      # mechanical_block / content_block / semantic
            "route": route,                        # 哪条落盘链路拦的
            "title": title, "url": url, "note_type": note_type,
            "issues": list(issues or []),
            "warnings": list(warnings or []),
            "compression_warnings": [],
            "note_sha256": digest(content),
            "source_sha256": digest(source_text) if source_text else "unknown",
            "source_path": source_path or "",
            "prompt_sha256": prompt_sha256 or "unknown",
            "draft_chars": len(content or ""),
        }
        if evidence is not None:
            meta["evidence"] = evidence
        if extra:
            meta.update(extra)

        with open(draft_path, "w", encoding="utf-8") as fh:
            fh.write(content or "")
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=1)
        return {"ok": True, "draft": draft_path, "meta": meta_path,
                "note_sha256": meta["note_sha256"]}
    except Exception as exc:                        # 保险丝：暂存失败绝不影响主流程
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def stage_blocked(content: str, **kwargs) -> dict:
    """拦截点用的薄封装：落暂存 + 打一行提示，**永不抛异常**。

    各管线（`save_summary_only` / `videos._summarize_and_save` / `videos._save_series_note` /
    `resum_save_batch`）在门禁失败处统一调这个，不再各写一份 try/except 与打印格式
    （2026-10-18：`videos/main.py` 曾直接调一个从未导入的名字，拦下来就是 NameError，
    把「拦下不落盘」变成「拦下就崩」）。

    `kwargs` 与 `stage_rejected` 完全一致；额外支持 `quiet=True` 静默。
    """
    quiet = bool(kwargs.pop("quiet", False))
    res = stage_rejected(content, **kwargs)
    if not quiet:
        if res.get("ok"):
            print(f"   📦 稿件已暂存（未通过，不丢）：{_rel(res['draft'])}")
        else:
            print(f"   ⚠️ 暂存失败（不影响主流程）：{res.get('error')}")
    return res


def _rel(path: str) -> str:
    try:
        return os.path.relpath(path, _ROOT)
    except (ValueError, OSError):
        return path


def list_staged(stage_dir: str = "", since: str = "") -> list:
    """列出暂存稿件（按目录或整棵树），`since=YYYYMMDD` 只看该日之后。"""
    root = stage_dir or STAGE_BASE
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, _dirs, files in os.walk(root):
        day = os.path.basename(dirpath)
        if (not day.startswith("20")) and os.path.basename(os.path.dirname(dirpath)) == "staged_drafts":
            continue
        if since and day < since:
            continue
        for f in sorted(files):
            if f.endswith(".md"):
                out.append(os.path.join(dirpath, f))
    return sorted(out)


def read_meta(draft_path: str) -> dict:
    """读伴生元信息；缺失返回 {}（不抛异常）。"""
    try:
        return json.load(open(draft_path[:-3] + ".json", encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def annotate(meta_path: str, extra: dict) -> bool:
    """给已落暂存的元信息补记后续决策（T8 受控替换结果 / 失败收尾原因）。

    暂存发生在拦截点或替换前，而决策可能在其后（比如过了门禁又被 A/B 判定保留旧稿）。
    补记保证「当时为什么留下这份稿」和稿件在一起。失败返回 False，**永不抛异常**。
    """
    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        meta.update(extra or {})
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False


def purge(stage_dir: str, keep_days: int = 30) -> int:
    """清理超过 keep_days 天的暂存目录（只在显式调用时使用，不自动跑）。"""
    removed = 0
    try:
        for d in sorted(os.listdir(stage_dir or STAGE_BASE)):
            path = os.path.join(stage_dir or STAGE_BASE, d)
            if not os.path.isdir(path) or len(d) != 8 or not d.startswith("20"):
                continue
            age = (time.time() - os.path.getmtime(path)) / 86400
            if age > keep_days:
                for f in os.listdir(path):
                    os.remove(os.path.join(path, f))
                os.rmdir(path)
                removed += 1
    except OSError:
        return removed
    return removed
