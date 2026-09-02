"""收录质量门禁（quality gate）。

默认关闭（opt-in）：门禁需要显式开启才生效（``QUALITY_GATE_ENABLED=1``）。
开启后，低星或低文档/功能评分的项目**不自动入库**，而是转入
``pending_review.json`` 待复核队列，由人工确认后再决定是否入库。这样
"抽到的全部入库"的问题被关掉——垃圾项目（如 8★ 的空壳仓库）会先进入
待复核队列，不会污染本地项目库 / 飞书 Bitable。不设或置 "0" 时门禁不生效，
所有项目直接入库（与无门禁时行为一致）。

可通过环境变量开启或调阈值：

- ``QUALITY_GATE_ENABLED``  (默认 "0"，置 "1" 开启整个门禁)
- ``QUALITY_GATE_MIN_STARS``(默认 100，stars 低于此值判低质)
- ``QUALITY_GATE_MIN_DOC``  (默认 5)
- ``QUALITY_GATE_MIN_FUNC`` (默认 5)

判定为低质（转入复核）的条件（任一成立即拦截）：

    stars < MIN_STARS
        OR
    (doc_score < MIN_DOC  AND  func_score < MIN_FUNC)

门禁只影响**未来**的入库；已写入本地库 / 飞书的项目不会回滚。
"""
import json
import os
from pathlib import Path
from typing import Optional

GATE_FILE = Path(__file__).resolve().parent.parent / "pending_review.json"


def is_enabled() -> bool:
    return os.environ.get("QUALITY_GATE_ENABLED", "0") == "1"


def thresholds() -> dict:
    def _int(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, default))
        except (ValueError, TypeError):
            return default

    return {
        "min_stars": _int("QUALITY_GATE_MIN_STARS", 100),
        "min_doc": _int("QUALITY_GATE_MIN_DOC", 5),
        "min_func": _int("QUALITY_GATE_MIN_FUNC", 5),
    }


def is_low_quality(doc_score: int, func_score: int, stars: int) -> bool:
    """返回 True 表示应转入待复核队列（不自动入库）。"""
    if not is_enabled():
        return False
    t = thresholds()
    if stars < t["min_stars"]:
        return True
    if doc_score < t["min_doc"] and func_score < t["min_func"]:
        return True
    return False


def gate_reason(doc_score: int, func_score: int, stars: int) -> str:
    t = thresholds()
    if stars < t["min_stars"]:
        return f"stars={stars} < 阈值 {t['min_stars']}"
    return (f"doc_score={doc_score} 与 func_score={func_score} "
            f"均低于阈值 {t['min_doc']}/{t['min_func']}")


def _load_queue() -> list:
    if not GATE_FILE.exists():
        return []
    try:
        with open(GATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def _save_queue(items: list) -> None:
    GATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(GATE_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def route_to_review(owner_repo, url, stars, result, source_kind, reason) -> str:
    """把低质量项目记入待复核队列。

    幂等：同一 owner/repo 已记录则直接返回 'reviewed' 不再追加，
    避免并行 / 重跑时队列脏增长。返回 'reviewed'。
    """
    items = _load_queue()
    for it in items:
        if it.get("owner_repo", "").lower() == owner_repo.lower():
            return "reviewed"
    entry = {
        "owner_repo": owner_repo,
        "url": url,
        "stars": stars,
        "project_type": getattr(result, "project_type", ""),
        "domain": getattr(result, "domain", ""),
        "doc_score": getattr(result, "doc_score", 0),
        "func_score": getattr(result, "func_score", 0),
        "source_kind": source_kind,
        "reason": reason,
    }
    items.append(entry)
    _save_queue(items)
    return "reviewed"
