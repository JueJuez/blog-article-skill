"""shared/series_state.py — 系列课「已总结集」持久记录（增量去重的核心）。

背景：每日监控可能反复抓到同一个 UP 的系列课。若每次都全量重抓重总结，
既浪费额度又会产生重复落盘。本模块记录每个系列已成功落盘的集 base_name，
供 videos.main._handle_bilibili_series 在检测时只把「未总结的集」写入 raw 并排队：
- 首跑：done 为空 → 全系列待总结（全量）。
- UP 更新后：done 含旧集 → 只把新增集列为 pending（增量）。

状态文件：monitors/series_state.json（运行时状态，已被 .gitignore 忽略，不入库）。
结构：{ "<系列名>": { "url": "...", "author": "...", "done": ["第01集_xxx", ...] } }
"""
import os
import json

# shared/ 的上一级即项目根；状态文件统一放在 monitors/ 下，与 pending_series.json 同目录
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(_ROOT, "monitors", "series_state.json")


def load() -> dict:
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def mark_done(series_title: str, base: str, url: str = "", author: str = "") -> None:
    """标记某集已成功落盘（由 drainer 在 _save_series_note 成功后调用）。"""
    state = load()
    entry = state.setdefault(series_title, {"url": url, "author": author, "done": []})
    if url:
        entry["url"] = url
    if author:
        entry["author"] = author
    if base not in entry.get("done", []):
        entry.setdefault("done", []).append(base)
    save(state)


def is_done(series_title: str, base: str) -> bool:
    state = load()
    entry = state.get(series_title)
    if not entry:
        return False
    return base in entry.get("done", [])


def get_pending(series_title: str, all_bases: list) -> list:
    """增量去重核心：返回 all_bases 中尚未总结的子集。

    all_bases 通常是 [第01集_xxx, 第02集_xxx, ...]（与落盘文件名 base 一致）。
    """
    state = load()
    entry = state.get(series_title)
    done = set(entry.get("done", [])) if entry else set()
    return [b for b in all_bases if b not in done]


def forget(series_title: str = None) -> None:
    """调试/重置用：清空某系列或全部已总结记录。"""
    if series_title is None:
        save({})
    else:
        state = load()
        state.pop(series_title, None)
        save(state)
