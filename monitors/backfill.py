"""monitors/backfill.py — 公众号历史回溯（补最近稳定窗口）功能模块。

把「补某公众号最近 N 天漏抓文章」做成可复用的一键入口。超过稳定窗口的历史
（目前 weread 免费代理约 30~35 天）不补，避免在深历史抖动上浪费 token/时间。

核心机制
--------
- 边界：默认 since = 今天往前 35 天（WECHAT_BACKFILL_DAYS 可调）。这是根据磁盘证据
  定下的稳定边界——哥飞 23 篇 raw 全落在 2026-07-24~08-19（27 天内），更老的历史
  代理返回乱序/伪造时间戳，极不可靠。超过边界的文章视为「代理侧不可稳定提供」，
  不再追。
- 游标 = state.json 的 seen（与日常监控同一套去重），只补窗口内尚未 seen 的文章。
- 分批：每次调用只处理 batch 篇（默认 15），遇到代理空窗会短退避重试，但只停在
  稳定窗口内，不翻深历史。
- 队列文件 backfill_targets.json：一列一次性 job {names, since, batch, done, note}。
  稳定窗口内的补抓跑完一次即标记 done，不追求「 exhaustive 抓全所有历史」。

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
DEFAULT_BACKFILL_DAYS = int(os.environ.get("WECHAT_BACKFILL_DAYS", "35"))


def default_since(days: Optional[int] = None) -> int:
    """返回默认回溯起点时间戳：现在往前 N 天（默认 WECHAT_BACKFILL_DAYS）。"""
    d = days if days is not None else DEFAULT_BACKFILL_DAYS
    return int(time.time()) - d * 86400


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


def mark_job_done(state: dict, names: List[str], note: str = "") -> bool:
    """把队列中首个匹配 names 的未完成 job 标为 done。

    稳定窗口补抓是「跑一次算一次」：不追求 exhaustive 抓全所有历史，只把代理
    在边界内能稳定返回的文章补回来。因此成功跑一次（无论抓到 0 条还是 N 条）
    即可标记 job 完成，避免队列无限 pending。

    返回是否标记了 job 完成。
    """
    q = load_queue()
    names_set = set(names)
    job = next((
        j for j in q
        if not j.get("done") and set(j.get("names", [])) == names_set
    ), None)
    if not job:
        return False
    job["done"] = True
    job["result"] = note or "shallow_backfill_completed"
    job["done_at"] = int(time.time())
    save_queue(q)
    print(f"🏁 队列 job 完成：{', '.join(names)}")
    return True


# --------------------------------------------------------------------------
# 进度报告
# --------------------------------------------------------------------------
def report_progress(state: dict, names: List[str], since_ts: int) -> None:
    """打印每账号回溯进度（稳定窗口内补抓结果）。"""
    since_s = time.strftime("%Y-%m-%d", time.localtime(since_ts)) if since_ts else "?"
    q = load_queue()
    print(f"\n📋 回溯进度（稳定窗口 since {since_s}）：")
    for n in names:
        job = next((j for j in q if n in j.get("names", [])), None)
        done = job.get("done") if job else False
        tag = "✅ 已完成" if done else "⏳ 队列中待跑"
        print(f"   - {n}: {tag}")
