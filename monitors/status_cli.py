"""monitors/status_cli.py — 状态 Ledger 查询 / 重驱入口（模型 & 用户）

PLAN-20260828-parallel-monitor.md §4。子命令：
  summary                      打印 latest.json 运行级汇总
  failures [--source X] [--status Y] [--since TS]
                             查询跨运行失败项（status 逗号分隔 OR）
  redrive  [--source X] [--status Y] [--dry-run]
                             选出可重驱失败项（含 transcribe_failed + 陈旧 transcribing 看门狗复位）
                             dry-run（默认）只打印；接 P3 后真正投递到 pending 队列

示例：
  python monitors/status_cli.py summary
  python monitors/status_cli.py failures --source bili --status timeout
  python monitors/status_cli.py redrive --source bili --status transcribe_failed
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from status_store import (
    get_latest,
    list_failures,
    redrive_items,
    deliver_redrive,
    PENDING_SUMMARY_PATH,
    SCYS_PENDING_PATH,
    list_runs,
)


def _cmd_summary(args):
    latest = get_latest()
    if not latest:
        print("ℹ️ 暂无运行记录（run_status/latest.json 不存在）。")
        return
    print(f"\n=== 最近一次运行汇总 ({latest['run_id']}) ===")
    print(f"  起止：{latest['started_ts']:.0f} → {latest['finished_ts']:.0f}"
          f"（耗时 {latest['duration_secs']}s）")
    print(f"  整体状态：{latest['overall_status']} ｜ 任务条数：{latest['task_count']}")
    print("  每源明细：")
    for src, d in latest.get("per_source", {}).items():
        parts = "  ".join(f"{k}={v}" for k, v in d.items())
        print(f"    - {src:8s} {parts}")
    if latest.get("notes"):
        print(f"  备注：{latest['notes']}")


def _cmd_failures(args):
    recs = list_failures(source=args.source, status=args.status, since=args.since)
    if not recs:
        print("ℹ️ 无匹配失败项。")
        return
    print(f"\n=== 失败项（共 {len(recs)}）===")
    for r in recs:
        ts = r.get("ts", 0)
        print(f"  [{r.get('source')}] {r.get('status'):16s} {r.get('stage'):10s}"
              f" attempts={r.get('attempts')}  ts={ts:.0f}")
        print(f"      id={r.get('item_id')}  title={r.get('title','')[:40]}")
        if r.get("error"):
            print(f"      err={str(r['error'])[:120]}")


def _cmd_redrive(args):
    recs = redrive_items(source=args.source, status=args.status)
    if not recs:
        print("ℹ️ 无可重驱项（过滤：status in failed/timeout/pending/transcribe_failed 且 attempts<MAX，含陈旧 transcribing 看门狗复位）。")
        return
    print(f"\n=== 将重驱（共 {len(recs)}）===")
    for r in recs:
        print(f"  [{r.get('source')}] {r.get('status'):16s} id={r.get('item_id')}"
              f"  title={r.get('title','')[:40]}")
    if args.dry_run:
        print("\n(dry-run) 仅预览，未投递队列。去除 --dry-run 即真正重驱。")
    else:
        # P3: 真正投递到对应 pending 队列
        stats = deliver_redrive(recs)
        print(f"\n✅ 已投递：")
        print(f"   - 单篇队列 {stats['single']} 项 → {PENDING_SUMMARY_PATH}")
        print(f"   - scys 队列 {stats['scys']} 项 → {SCYS_PENDING_PATH}")
        print("   下次运行 / 模型派单将自动重新抓取并总结这些项。")
    return recs


def main():
    p = argparse.ArgumentParser(description="监控状态 Ledger 查询 / 重驱")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("summary", help="打印最近一次运行汇总")
    sp.set_defaults(func=_cmd_summary)

    fp = sub.add_parser("failures", help="查询跨运行失败项")
    fp.add_argument("--source", default=None)
    fp.add_argument("--status", default=None, help="逗号分隔多值 OR，如 timeout,transcribe_failed")
    fp.add_argument("--since", type=float, default=None, help="unix 时间戳，只取此后的")
    fp.set_defaults(func=_cmd_failures)

    rp = sub.add_parser("redrive", help="选出可重驱失败项")
    rp.add_argument("--source", default=None)
    rp.add_argument("--status", default=None)
    rp.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                    help="真正投递（P3 接入后生效，当前仍仅预览）")
    rp.set_defaults(func=_cmd_redrive)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
