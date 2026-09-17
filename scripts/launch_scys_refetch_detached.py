#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DETACHED 启动 scys 源重抓（按待重抓清单），脱离会话后台运行。

用法（普通 Bash 前台调用即可，本脚本 spawn DETACHED 子进程后立刻返回）：
    python scripts/launch_scys_refetch_detached.py
子进程独立存活，日志写在 notes/_scraped/scys/refetch_run.log，
进度/续跑状态在 notes/_scraped/scys/refetch_state.json。
"""
import os
import subprocess
import sys
from subprocess import CREATE_NEW_PROCESS_GROUP, DETACHED_PROCESS

ROOT = r"D:\Code\Skills\blog-article-skill"
PY = r"D:\App\anaconda3\python.exe"
LOG = r"notes\_scraped\scys\refetch_run.log"


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    logf = open(LOG, "w", encoding="utf-8")
    child = subprocess.Popen(
        [
            PY,
            "-u",
            r"scripts\fetch_scys_by_ids.py",
            "--list-md", r"notes\_reports\20260915_待重抓清单.md",
            "--no-external",
        ],
        cwd=ROOT,
        env=os.environ.copy(),
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        stdout=logf,
        stderr=subprocess.STDOUT,
        close_fds=True,
    )
    print(f"[launcher] spawned DETACHED child pid={child.pid} -> {LOG}")


if __name__ == "__main__":
    sys.exit(main())
