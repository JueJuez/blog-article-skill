#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/launch_scys_backfill.py — 以 DETACHED 拉起 scys 补齐，脱离 agent 会话常驻。

与 `scripts/launch_watchdog.py` 同构（薄 launcher + 真 worker），区别：本脚本**自我再派生**
（首次调用即用 `DETACHED_PROCESS` 把自己重开成脱离会话的常驻进程，再跑逐域循环），
所以只需一个文件、调用即"自带进程"。

用法：
    python scripts/launch_scys_backfill.py                     # 补齐 scys_projects.json 全部领域
    python scripts/launch_scys_backfill.py --project 出海       # 只补一个领域
    python scripts/launch_scys_backfill.py --projects 出海,小程序

查看进度：
    notes/_scraped/scys/state.json            已抓计数（done）
    notes/_scraped/scys/pending_summaries.json 待总结队列
    notes/_scraped/scys/_backfill_run.log     逐域日志

为什么不用 run_in_background：那只是 **Bash 工具层**的后台（让工具调用立即返回、不撞 120s 超时），
进程仍是 agent 会话进程树的子进程 → **轮次结束被环境回收**。真正要"自带进程、脱离会话"必须
**OS 级分离**：Windows `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`（本脚本做法），
或由用户在自带终端里跑。断点续传：重跑自动只抓 state.json 里没有的。
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
_REENTRY_FLAG = "--_detached"


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


def _work(projects: list) -> int:
    # 把本进程 fd1/fd2 指向日志，子进程默认继承（避免给子进程传 python 文件对象当 stdout）。
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    lf = os.open(LOG, os.O_CREAT | os.O_WRONLY | os.O_APPEND)
    os.dup2(lf, 1)
    os.dup2(lf, 2)
    print(f"\n########## scys 补齐启动 {_now()} domains={projects} ##########", flush=True)
    for d in projects:
        print(f"########## 域: {d} start={_now()} ##########", flush=True)
        try:
            rc = subprocess.run([sys.executable, SCRIPT, "--project", d], cwd=BASE_DIR).returncode
        except Exception as e:  # noqa: BLE001
            print(f"[launcher] 域 {d} 异常: {e}", flush=True)
            rc = -1
        print(f"---------- 域: {d} end={_now()} rc={rc} ----------", flush=True)
    print(f"ALL DONE {_now()}", flush=True)
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == _REENTRY_FLAG:  # 已在 detached 子进程里：干活
        return _work(argv[1:] or _all_projects())
    projects = _parse_projects(argv)
    p = subprocess.Popen(
        [sys.executable, SELF, _REENTRY_FLAG] + projects,
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    print(f"✓ scys 补齐已 DETACHED 拉起（脱离会话常驻），pid={p.pid}")
    print(f"  领域: {projects}")
    print(f"  进度: notes/_scraped/scys/state.json (done) / _backfill_run.log")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
