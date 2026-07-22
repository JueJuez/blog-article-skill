"""monitors/state.py — 本地去重状态管理。

state.json 结构：
{
  "sources": {
    "wechat:MP_XXX": {"seen": ["a1","a2"], "last_check": 1700000000},
    "bilibili:22675713": {"seen": ["BVxxx","cv123"], "last_check": 1700000000}
  }
}
"""
import json
import os
import sys
from typing import Dict, Any, List, Set

DEFAULT_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

# seen 列表每源保留的最大 ID 数（防 state.json 无限膨胀）。per-source 裁剪，
# 保留「最新」(列表末尾)。默认 1000：首跑单源约 100 ID（视频+动态各~50），
# 留 10× 余量；即使每日跑两遍，窗口内(≤7天) ID 远小于 1000，不会被误删。
DEFAULT_STATE_KEEP = int(os.environ.get("STATE_KEEP", "1000"))


def load_state(path: str = DEFAULT_STATE_PATH) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"sources": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "sources" not in data:
            data["sources"] = {}
        return data
    except Exception:
        # 损坏则重置，避免监控整体卡死
        return {"sources": {}}


def save_state(state: Dict[str, Any], path: str = DEFAULT_STATE_PATH) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_seen(state: Dict[str, Any], source_key: str) -> Set[str]:
    return set(state["sources"].get(source_key, {}).get("seen", []))


def mark_seen(state: Dict[str, Any], source_key: str, ids: List[str],
              last_check: int = None, keep: int = None) -> None:
    """把 ids 并入某源的 seen，并裁剪到最新 keep 个（per-source），防 state.json 膨胀。

    - 保持插入顺序：新抓的 ID 追加在末尾，裁剪时丢最旧的（头部），保留最新。
    - 去重：已存在的 ID 不再重复追加。
    - keep=None 时用 DEFAULT_STATE_KEEP（env STATE_KEEP，默认 1000）。
      裁剪安全性：每日抓取窗口仅 1 天、首跑窗口 7 天，单源窗口内 ID 远小于 1000，
      被裁掉的都是早已超出窗口、不会再被抓回的旧 ID，不会触发重复总结。
    """
    if keep is None:
        keep = DEFAULT_STATE_KEEP
    src = state["sources"].setdefault(source_key, {})
    seen = src.get("seen", [])          # 保持顺序的列表（非 set）
    seen_set = set(seen)
    for i in ids:
        if i not in seen_set:
            seen.append(i)
            seen_set.add(i)
    if keep and len(seen) > keep:
        dropped = len(seen) - keep
        seen = seen[-keep:]
        print(f"[state-trim] {source_key} seen 超出 {keep}，裁剪最旧 {dropped} 个",
              file=sys.stderr)
    src["seen"] = seen
    if last_check is not None:
        src["last_check"] = last_check
