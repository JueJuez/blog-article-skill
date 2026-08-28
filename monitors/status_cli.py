"""monitors/status_cli.py — 状态 Ledger 查询入口（模型 & 用户）

PLAN-20260828-parallel-monitor.md §4。子命令：
  summary                      打印 latest.json 运行级汇总

示例：
  python monitors/status_cli.py summary
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from status_store import (
    get_latest,
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


def main():
    p = argparse.ArgumentParser(description="监控状态 Ledger 查询 / 重驱")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("summary", help="打印最近一次运行汇总")
    sp.set_defaults(func=_cmd_summary)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
