#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/launch_watchdog.py — 以 DETACHED 拉起迁移进度看护, 脱离 agent 会话常驻。

直接 `python scripts/launch_watchdog.py [--interval 300]` 即可。
看护会自己去找/拉起迁移进程(作为看护的普通子进程, 继承稳定父 lineage, 不依赖 powershell)。

查看状态: python scripts/migrate_watchdog.py --status
停止看护: 删 scripts/_watchdog_lock.pid 并 taskkill 掉 migrate_watchdog.py 进程,
          或直接等迁移完成后看护自行退出。
"""
import os
import sys
import subprocess

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
WATCHDOG = os.path.join(BASE_DIR, "scripts", "migrate_watchdog.py")
OUT = os.path.join(BASE_DIR, "scripts", "migrate_watchdog.log")
DETACHED = 0x00000008

args = sys.argv[1:]
out = open(OUT, "a", encoding="utf-8")
subprocess.Popen([PY, WATCHDOG] + args, cwd=BASE_DIR,
                 stdout=out, stderr=out, creationflags=DETACHED, close_fds=True)
print("✓ 迁移进度看护已 DETACHED 拉起(脱离会话, 常驻)。")
print("  状态查看: python scripts/migrate_watchdog.py --status")
print(f"  心跳日志: {OUT}")
