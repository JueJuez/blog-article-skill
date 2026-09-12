#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/launch_watchdog.py — 前台/自带终端拉起迁移进度看护（非 detached）。

直接 `python scripts/launch_watchdog.py [--interval 300]` 即可（在自带终端或前台跑，
不要靠 detached 脱离会话——detached 父子树有 ~60s 回收怪相，且 detached 父进程跑多任务会失能）。
看护会自己去找/拉起迁移进程(作为看护的普通子进程, 继承稳定父 lineage, 不依赖 powershell)。

查看状态: python scripts/migrate_watchdog.py --status
停止看护: 删 scripts/_watchdog_lock.pid 并 taskkill 掉 migrate_watchdog.py 进程,
          或直接等迁移完成后看护自行退出。
"""
import os
import sys
import subprocess

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from shared.rolling_log import cleanup_expired, rolling_log_path  # noqa: E402

PY = sys.executable
WATCHDOG = os.path.join(BASE_DIR, "scripts", "migrate_watchdog.py")
OUT_BASE = os.path.join(BASE_DIR, "scripts", "migrate_watchdog.log")

args = sys.argv[1:]
cleanup_expired(OUT_BASE)  # 按天滚动（防膨胀），打开前顺手清理过期日志
OUT = rolling_log_path(OUT_BASE)
out = open(OUT, "a", encoding="utf-8")
subprocess.Popen([PY, WATCHDOG] + args, cwd=BASE_DIR,
                 stdout=out, stderr=out, close_fds=True)
print("✓ 迁移进度看护已拉起(前台运行, 日志轮转)。")
print("  状态查看: python scripts/migrate_watchdog.py --status")
print(f"  心跳日志: {OUT}")
