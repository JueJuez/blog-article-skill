#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/launch_scys_backfill.py — scys 补齐调度器：每域一个独立 DETACHED 子进程。

⚠️ **不要在本脚本外再套一层 DETACHED launcher**（例如曾出现的 `_tmp/scys_launch_detached.py`）：
本脚本自身已是「非 DETACHED 父 + 每域 DETACHED 子」，外层再包一个 DETACHED 父会复刻下方文档
所述的「DETACHED 父 spawn 失能」bug（跑完第 1 域后父失去 spawn 能力，第 2 域起 rc=1）。
**直接 `python scripts/launch_scys_backfill.py [--projects ...] [--limit 0]` 即可。** 多域若被
轮次回收，重跑本脚本即断点续传（state.json 去重，已抓的跳过）。

与老实现（单个 DETACHED 父进程内 for 循环 spawn 多域）不同：本脚本**自身非 DETACHED**，
仅对每个领域 spawn 一个**独立 DETACHED 子进程**并串行 wait。原因：
  - "DETACHED 父进程 + 多子"实测会失能（跑完第 1 域后父失去 spawn 能力，第 2 域起 rc=1）；
  - 非 DETACHED 父 spawn 多子正常（参考 monitors/run.py 7 域非 DETACHED 全 rc=0）；
  - 每域子进程 DETACHED → 脱离 agent 会话树，**抗轮次回收**（即使调度器被收，已 spawn 的子继续跑完）。
  - 串行 wait → 全局单 Chrome（共享克隆 profile，严禁并行多 Chrome）。

用法：
    python scripts/launch_scys_backfill.py                     # 补齐 scys_projects.json 全部领域
    python scripts/launch_scys_backfill.py --project 出海       # 只补一个领域
    python scripts/launch_scys_backfill.py --projects 出海,小程序

查看进度：
    notes/_scraped/scys/state.json            已抓计数（done）
    notes/_scraped/scys/pending_summaries.json 待总结队列
    notes/_scraped/scys/_backfill_run.log     逐域日志

为什么调度器自身非 DETACHED：避免"DETACHED 父 spawn 失能"，抗回收由每域子进程的 DETACHED 提供。
断点续传：scys_batch_fetch 重跑自动只抓 state.json 里没有的；若调度器被轮次收导致部分域未 spawn，
重跑本脚本即可从断点续（已抓的跳过）。
"""
import datetime
import json
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELF = os.path.abspath(__file__)
CONFIG = os.path.join(BASE_DIR, "scripts", "scys_projects.json")
SCRIPT = os.path.join(BASE_DIR, "scripts", "scys_batch_fetch.py")
LOG = os.path.join(BASE_DIR, "notes", "_scraped", "scys", "_backfill_run.log")

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def _now() -> str:
    return datetime.datetime.now().strftime("%F %T")


def _all_projects() -> list:
    cfg = json.loads(open(CONFIG, encoding="utf-8").read())
    return [k for k, v in (cfg.get("projects") or {}).items() if v]


def _parse_projects(argv: list) -> list:
    if "--projects" in argv:
        return [x for x in argv[argv.index("--projects") + 1].split(",") if x]
    if "--project" in argv:
        return [argv[argv.index("--project") + 1]]
    return _all_projects()


def _parse_limit(argv: list) -> int | None:
    """--limit N 透传给每域子进程；N=0 表示不限（去掉默认 30 上限）。未给则透 None（子进程走配置默认）。"""
    if "--limit" in argv:
        try:
            return int(argv[argv.index("--limit") + 1])
        except (IndexError, ValueError):
            return None
    return None


def _work(projects: list, limit: int | None = None) -> int:
    # 日志文件：父（dup2 fd1/2）与每域 DETACHED 子（显式传文件对象）都写入同一日志。
    # ⚠️ DETACHED 子默认脱离控制台、不继承标准句柄，必须显式传 stdout 文件对象，否则子输出全丢。
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    logf = open(LOG, "a", encoding="utf-8", buffering=1)
    lf = logf.fileno()
    os.dup2(lf, 1)
    os.dup2(lf, 2)
    print(f"\n########## scys 补齐启动 {_now()} domains={projects} limit={limit} ##########", flush=True)
    for d in projects:
        print(f"########## 域: {d} start={_now()} ##########", flush=True)
        try:
            # 每域一个独立 DETACHED 子进程：脱离会话、抗轮次回收；串行 wait 保证全局单 Chrome。
            # stdout 显式传 logf —— DETACHED 子不继承控制台句柄，必须指定才能写日志。
            cmd = [sys.executable, SCRIPT, "--project", d]
            if limit is not None:
                cmd += ["--limit", str(limit)]
            p = subprocess.Popen(
                cmd,
                cwd=BASE_DIR,
                stdout=logf,
                stderr=subprocess.STDOUT,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
            rc = p.wait()
        except Exception as e:  # noqa: BLE001
            print(f"[launcher] 域 {d} 异常: {e}", flush=True)
            rc = -1
        print(f"---------- 域: {d} end={_now()} rc={rc} ----------", flush=True)
    print(f"ALL DONE {_now()}", flush=True)
    logf.close()
    return 0


def main() -> int:
    projects = _parse_projects(sys.argv[1:])
    limit = _parse_limit(sys.argv[1:])
    rc = _work(projects, limit=limit)
    print(f"✓ scys 补齐调度完成（每域独立 DETACHED 子进程）。")
    print(f"  领域: {projects}")
    print(f"  上限: {limit if limit is not None else '配置默认(30)'}")
    print(f"  进度: notes/_scraped/scys/state.json (done) / _backfill_run.log")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
