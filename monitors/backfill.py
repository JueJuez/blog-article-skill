"""monitors/backfill.py — 公众号历史回溯（续批）功能模块。

把「抓某公众号 N 年内历史」从一次性 env-var 拼凑，升级为参数化、可复用、可自动续批的功能。

核心机制
--------
- 游标 = state.json 的 seen（与日常监控同一套去重）。不重置即可续批；每账号回溯进度
  存于 state["backfill"][name] = {done, reason, oldest_ts, done_at}，避免无限重复拉取。
- 分批：每次调用只入队 batch 篇（默认 15），多跑几次自然往前翻（discover 的 backfill 分支
  只 mark 本批 new 为 seen，保留续批能力）。
- 完成判定（在 wechat.WechatSource.discover 内执行，本模块负责读取报告）：
    - 本批 0 新 且 已越过 since 边界        -> done(reached_since)
    - 本批 0 新 但 代理最老只到 oldest_ts    -> done(proxy_depth:<最老ts>)  ← 直接暴露代理深度上限
    - 否则未完成，下次续批。
- 队列文件 backfill_targets.json：一列 job {names, since, batch, done, note}。
  「今天哥飞+生财，明天别的号」= 往队列加 job；一条 recurring 自动化 --drain 即可逐 job 续批，
  无需改代码、无需改命令。

不变量
------
- 范围保护：只处理 names 指定的号，绝不波及其他订阅源（discover 内 WECHAT_BACKFILL_NAMES 门禁）。
- 不重复标记：discover backfill 分支只 mark 本批 new，日常监控 mark 全部 fetched —— 两套互不污染。
- 与 discover 的 env-var backfill 是同一底层；本模块是 canonical 入口（CLI/队列/进度报告）。
"""
import os
import json
import time
from typing import Dict, Any, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE_PATH = os.path.join(HERE, "backfill_targets.json")
DEFAULT_BATCH = 15


def _parse_since(since: str) -> int:
    """把 since 解析成时间戳。支持 YYYY-MM-DD / YYYY/MM/DD / 纯数字时间戳。"""
    s = (since or "").strip()
    if s.isdigit():
        return int(s)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return int(time.mktime(time.strptime(s, fmt)))
        except ValueError:
            continue
    raise ValueError(f"无法解析 since: {since!r}（支持 YYYY-MM-DD 或时间戳）")


# --------------------------------------------------------------------------
# 队列文件 backfill_targets.json
# --------------------------------------------------------------------------
def load_queue() -> List[dict]:
    try:
        with open(QUEUE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_queue(q: List[dict]) -> None:
    with open(QUEUE_PATH, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False, indent=2)


def add_job(names: List[str], since: str, batch: int = DEFAULT_BATCH, note: str = "") -> str:
    """往队列加一个回溯 job。同名同 since 的 job 不重复加（改为重新激活 + 更新 batch）。

    返回 "added" / "reactivated"。
    """
    q = load_queue()
    sig = (tuple(sorted(names)), since)
    for j in q:
        if (tuple(sorted(j.get("names", []))), j.get("since")) == sig:
            j["done"] = False
            j["batch"] = batch
            if note:
                j["note"] = note
            save_queue(q)
            return "reactivated"
    q.append({
        "names": list(names),
        "since": since,
        "batch": batch,
        "done": False,
        "note": note,
        "created_at": int(time.time()),
    })
    save_queue(q)
    return "added"


def first_pending_job() -> Optional[dict]:
    """返回队列中第一个未完成 job（用于 --drain）。"""
    q = load_queue()
    return next((j for j in q if not j.get("done")), None)


def reset_backfill(state: dict, names: List[str]) -> None:
    """清除指定账号的回溯完成状态，便于重新往前翻。按账号名 key，无需 mp_id 反解。"""
    bf = state.get("backfill", {})
    for n in names:
        bf.pop(n, None)


def mark_job_done_if(state: dict, names: List[str]) -> bool:
    """若 names 中全部账号都已 backfill_done，则把队列首个未完成 job 标为 done。

    返回是否标记了 job 完成。
    """
    bf = state.get("backfill", {})
    if not all(bf.get(n, {}).get("backfill_done") for n in names):
        return False
    q = load_queue()
    job = next((j for j in q if not j.get("done")), None)
    if not job:
        return False
    job["done"] = True
    job["result"] = "; ".join(
        f"{n}:{bf.get(n, {}).get('backfill_done_reason', '')}" for n in names
    )
    save_queue(q)
    print(f"🏁 队列 job 完成：{', '.join(names)}")
    return True


# --------------------------------------------------------------------------
# 进度报告
# --------------------------------------------------------------------------
def report_progress(state: dict, names: List[str], since_ts: int) -> None:
    """打印每账号回溯进度（完成状态 + 代理最老可达日期），直接呼应 Q1 的代理深度差异。"""
    bf = state.get("backfill", {})
    since_s = time.strftime("%Y-%m-%d", time.localtime(since_ts)) if since_ts else "?"
    print(f"\n📋 回溯进度（since {since_s}）：")
    for n in names:
        d = bf.get(n, {})
        done = d.get("backfill_done")
        oldest = d.get("backfill_oldest_ts", 0)
        reason = d.get("backfill_done_reason", "")
        ots = time.strftime("%Y-%m-%d", time.localtime(oldest)) if oldest else "?"
        if done:
            if reason == "reached_since":
                tag = "✅ 完成（已回溯越过 since 边界）"
            elif reason.startswith("proxy_depth:"):
                tag = f"✅ 完成（代理历史深度上限：最老仅到 {ots}，更早文章代理侧不可达）"
            else:
                tag = f"✅ 完成（{reason}）"
        else:
            tag = "⏳ 未完成（仍有历史未抓，下次续批）"
        print(f"   - {n}: {tag}")
