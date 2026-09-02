import json
import os
from typing import List, Dict, Set

PENDING_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "pending_results.json",
)


def append_items(items: List[Dict]) -> None:
    all_items = load_items()
    all_items.extend(items)
    _write_all(all_items)


def load_items() -> List[Dict]:
    if not os.path.exists(PENDING_FILE):
        return []
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, IOError):
        return []


def count_items() -> int:
    return len(load_items())


def has_items() -> bool:
    return count_items() > 0


def owner_repo_keys() -> Set[str]:
    keys: Set[str] = set()
    for item in load_items():
        key = item.get("_owner_repo", "")
        if key:
            keys.add(key.lower())
    return keys


def pop_all() -> List[Dict]:
    items = load_items()
    _clear()
    return items


def _clear() -> None:
    if os.path.exists(PENDING_FILE):
        os.remove(PENDING_FILE)


def _write_all(items: List[Dict]) -> None:
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)