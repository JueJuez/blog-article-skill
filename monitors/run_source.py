#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单源 / 单账号补跑入口（与日常串行 run.py 解耦，互不影响）。

设计意图（2026-09-02）：
- 日常全源串行监控仍走 `python monitors/run.py --mode auto --apply`（B站+公众号+scys 全抓，行为不变）。
- 本文件是「另存出来」的补跑专用入口：只跑某一个平台，或平台上某一个具体账号。
  大概率是补跑（某源漏了 / 登录过期没抓到 / 只想要某一家的增量）。

用法：
  # 只跑 scys（四领域全抓）
  python monitors/run_source.py --source scys
  # 只跑 scys 的「出海」一个领域
  python monitors/run_source.py --source scys --target 出海
  # 只跑 B站（全部 UP）
  python monitors/run_source.py --source bili
  # 只跑 B站某一个 UP（按 uid，或 --target 写名字也能匹配）
  python monitors/run_source.py --source bili --target 22675713
  # 只跑公众号某一个号
  python monitors/run_source.py --source wechat --target 中金研究
  # 仅预览发现结果、不落盘
  python monitors/run_source.py --source scys --dry

默认 apply=True（补跑即落盘/入队）；--dry 仅预览 discover 结果。
--source 不传 = all（等价于日常全源，保留作兜底）。

⚠️ 隔离原则（2026-09-02 修正）：每个源有独立的补跑函数（run_bili_backfill /
run_wechat_backfill / run_scys_backfill）。各源只消费「自己 source 的 pending_refetch」
（bili 取 source=bilibili、wechat 取 source=wechat），绝不把别的源的撞墙文
拉进来重抓。因此「补哪源只跑哪源」，不会串联。all 模式才保留跨源合并。
"""
import os
import sys
import json
import time
import argparse

# 让 `from monitors.x` 与 `import run` 都能解析（无论从哪 cwd 调用）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from monitors import run  # noqa: E402


_SOURCE_KEY = {"bili": "bilibili", "wechat": "wechat", "scys": "scys"}


def _match(entry: dict, source: str, target: str) -> bool:
    """target 为空 = 全匹配；否则按源字段匹配。"""
    if not target:
        return True
    t = str(target)
    if source == "bili":
        return str(entry.get("uid", "")) == t or (t in (entry.get("name", "") or ""))
    if source == "wechat":
        return t in (
            str(entry.get("name", "")),
            str(entry.get("mp_id", "")),
            str(entry.get("share_url", "")),
        )
    if source == "scys":
        return str(entry.get("project", "")) == t
    return False


def filter_subs(subs: dict, source: str, target: str) -> dict:
    """按 source（+ 可选 target）过滤订阅清单。"""
    if source == "all":
        return subs
    key = _SOURCE_KEY[source]
    out = {"bilibili": [], "wechat": [], "scys": []}
    out[key] = [e for e in subs.get(key, []) if _match(e, source, target)]
    return out


def _summarize_filtered(subs: dict) -> str:
    parts = []
    for k in ("bilibili", "wechat", "scys"):
        items = subs.get(k, [])
        if not items:
            continue
        labels = []
        for e in items:
            if k == "bilibili":
                labels.append(f"{e.get('name','?')}(uid={e.get('uid','?')})")
            elif k == "wechat":
                labels.append(e.get("name", e.get("mp_id", "?")))
            else:
                labels.append(e.get("project", "?"))
        parts.append(f"{k}: {', '.join(labels)}")
    return "; ".join(parts) if parts else "（空）"


def _consume_own_refetch(source_key: str) -> list:
    """从全局 pending_refetch.json 取出本源待重抓条目，并把剩余写回（避免重复消费）。

    这样补跑 bili 只拿 source=bilibili 的（如 B站 ASR 失败重抓），补跑 wechat 只拿
    source=wechat 的（那 20 篇撞墙文），互不串。成功消费的会被 apply_summaries 自行
    从队列移除；仍失败的会由它写回全局队列（MON_PENDING_REFETCH_PATH），不丢。
    """
    path = run.PENDING_REFETCH_PATH
    all_items = run._load_json(path, [])
    own = [x for x in all_items if x.get("source") == source_key]
    others = [x for x in all_items if x.get("source") != source_key]
    if len(others) != len(all_items):
        run._save_json(path, others)
    if own:
        print(f"[refetch] 本源（{source_key}）待重抓 {len(own)} 篇，已隔离取出"
              f"（其余 {len(others)} 篇非本源，不动）")
    return own


def _land_items(items: list, obsidian: bool) -> dict:
    """单源落盘：只处理传入的 items（已含本源 discover + 本源 refetch），
    不合并全局 pending_refetch（consume_prev_refetch=False），保证不串联。

    apply_summaries 是落盘引擎（daily run.py 也用它），此处仅作单源调用。
    """
    _session_holder = {"obj": None}
    stats = run.apply_summaries(
        items, obsidian, session=None, session_holder=_session_holder,
        consume_prev_refetch=False,
    )
    if _session_holder["obj"] is not None:
        _session_holder["obj"].close()
    return stats


def _discover(subs: dict, mode: str, all_videos: bool) -> tuple:
    state = run.load_state()
    all_new = run.discover_all(subs, state, mode=mode, force_all=all_videos)
    return state, all_new


def _record_and_finalize(run_id: str, started: float, stats: dict,
                         scys_pending_n: int) -> None:
    run._ledger_record_land(run_id, stats, scys_pending_n)
    run.finalize_run(run_id, started_ts=started,
                     summary_counts=run._build_summary(stats, scys_pending_n),
                     overall_status="ok")
    print(f"\n📋 Ledger 已写入：monitors/run_status/{run_id}.*.tasks.jsonl"
          f"（查询：python monitors/status_cli.py summary）")


def run_bili_backfill(subs: dict, mode: str, apply: bool,
                      obsidian: bool, all_videos: bool) -> None:
    """B站补跑：只发现 B站 + 只重抓 source=bilibili 的 refetch（如 ASR 失败）。"""
    scoped = {"bilibili": subs.get("bilibili", []), "wechat": [], "scys": []}
    state, all_new = _discover(scoped, mode, all_videos)
    if not apply:
        print(json.dumps(all_new, ensure_ascii=False, indent=2))
        return
    run_id = run.new_run_id()
    started = time.time()
    run._ledger_record_discover(run_id, all_new)
    run.save_state(state)
    bili_refetch = _consume_own_refetch("bilibili")
    items = all_new + bili_refetch
    stats = _land_items(items, obsidian)
    _record_and_finalize(run_id, started, stats, 0)
    print(f"🤖 B站补跑完成：新发现 {len(all_new)} + 本源重抓 {len(bili_refetch)}，"
          f"落盘 video={stats.get('video')}")


def run_wechat_backfill(subs: dict, mode: str, apply: bool, obsidian: bool) -> None:
    """公众号补跑：只发现公众号 + 只重抓 source=wechat 的 refetch（即那 20 篇撞墙文）。"""
    scoped = {"bilibili": [], "wechat": subs.get("wechat", []), "scys": []}
    state, all_new = _discover(scoped, mode, False)
    if not apply:
        print(json.dumps(all_new, ensure_ascii=False, indent=2))
        return
    run_id = run.new_run_id()
    started = time.time()
    run._ledger_record_discover(run_id, all_new)
    run.save_state(state)
    wechat_refetch = _consume_own_refetch("wechat")
    items = all_new + wechat_refetch
    stats = _land_items(items, obsidian)
    _record_and_finalize(run_id, started, stats, 0)
    print(f"🤖 公众号补跑完成：新发现 {len(all_new)} + 本源重抓 {len(wechat_refetch)}，"
          f"落盘 article={stats.get('article')}")


def run_scys_backfill(subs: dict, mode: str, apply: bool, obsidian: bool) -> None:
    """scys 补跑：四领域全抓，进 scys 专属待总结队列（不调 apply_summaries，天然不串）。"""
    scoped = {"bilibili": [], "wechat": [], "scys": subs.get("scys", [])}
    state, all_new = _discover(scoped, mode, False)
    if not apply:
        print(json.dumps(all_new, ensure_ascii=False, indent=2))
        return
    run_id = run.new_run_id()
    started = time.time()
    run._ledger_record_discover(run_id, all_new)
    run.save_state(state)
    from shared.cdp_session import SharedCdpSession
    with SharedCdpSession() as sess:
        run.run_scys_daily(subs["scys"], mode=mode, session=sess)
    scys_pending = run._load_json(run.SCYS_PENDING_PATH, [])
    scys_pending_n = len(scys_pending)
    if scys_pending:
        print(f"\n🤖 NEED_AGENT_SCYS_SUMMARY: scys {scys_pending_n} 篇待总结。"
              f"清单见 {run.SCYS_PENDING_PATH}\n"
              f"   处理路径：Read 条目 output 指向的 md 原文 → 按模板总结 → "
              f"save_summary_only 落飞书（folder=生财有术/<领域>）→ 从队列移除该条。")
    _record_and_finalize(run_id, started,
                         {"video": 0, "article": 0}, scys_pending_n)


def run_all_backfill(subs: dict, mode: str, apply: bool,
                     obsidian: bool, all_videos: bool) -> None:
    """all 模式：等价于日常全源（跨源合并 refetch），保留作兜底。"""
    state, all_new = _discover(subs, mode, all_videos)
    if not apply:
        print(json.dumps(all_new, ensure_ascii=False, indent=2))
        return
    run_id = run.new_run_id()
    started = time.time()
    run._ledger_record_discover(run_id, all_new)
    run.save_state(state)
    # all 模式沿用 apply_summaries 默认（consume_prev_refetch=True）合并全部 refetch
    _session_holder = {"obj": None}
    stats = run.apply_summaries(
        all_new, obsidian, session=None, session_holder=_session_holder,
    )
    if _session_holder["obj"] is not None:
        _session_holder["obj"].close()
    scys_pending_n = 0
    if subs.get("scys"):
        from shared.cdp_session import SharedCdpSession
        with SharedCdpSession() as sess:
            run.run_scys_daily(subs["scys"], mode=mode, session=sess)
        scys_pending = run._load_json(run.SCYS_PENDING_PATH, [])
        scys_pending_n = len(scys_pending)
        if scys_pending:
            print(f"\n🤖 NEED_AGENT_SCYS_SUMMARY: scys {scys_pending_n} 篇待总结。")
    _record_and_finalize(run_id, started, stats, scys_pending_n)


def run_source(source: str, target: str, mode: str, apply: bool,
               obsidian: bool, all_videos: bool) -> None:
    subs = run.load_subscriptions()
    subs = filter_subs(subs, source, target)

    chosen = [s for s in ("bilibili", "wechat", "scys") if subs.get(s)]
    if not chosen:
        print(f"[warn] 过滤后无匹配账号（source={source}, target={target!r}），退出。",
              file=sys.stderr)
        return

    print(f"[run_source] 本次范围：{_summarize_filtered(subs)}  "
          f"（mode={mode}, apply={apply}）")

    if source == "scys":
        run_scys_backfill(subs, mode, apply, obsidian)
    elif source == "bili":
        run_bili_backfill(subs, mode, apply, obsidian, all_videos)
    elif source == "wechat":
        run_wechat_backfill(subs, mode, apply, obsidian)
    else:  # all
        run_all_backfill(subs, mode, apply, obsidian, all_videos)


def main() -> None:
    p = argparse.ArgumentParser(
        description="单源/单账号补跑（不影响日常串行 run.py）")
    p.add_argument("--source", choices=["bili", "wechat", "scys", "all"],
                   default="all", help="只跑某平台；all=全源（兜底，等价日常）")
    p.add_argument("--target", default="",
                   help="平台上具体账号：bili=uid或名字；wechat=名字/mp_id/share_url；"
                        "scys=project 名")
    p.add_argument("--mode", choices=["auto", "first"], default="auto",
                   help="first=更宽窗口（B站7天/scys35天），适合大补跑")
    p.add_argument("--dry", action="store_true",
                   help="仅预览 discover 结果，不落盘/不入队")
    p.add_argument("--obsidian", action="store_true",
                   help="落盘时追加写 Obsidian（默认只飞书）")
    p.add_argument("--all-videos", action="store_true",
                   help="B站视频全量（不过滤已处理），大补跑用")
    args = p.parse_args()

    run_source(args.source, args.target, args.mode,
               apply=not args.dry, obsidian=args.obsidian,
               all_videos=args.all_videos)


if __name__ == "__main__":
    main()
