#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/launch_audit_watchdog.py — 以 DETACHED 拉起 audit_sync 周期看护, 脱离会话常驻。

直接 `python scripts/launch_audit_watchdog.py [--interval 300]` 即可。
看护周期运行 audit_sync.py --fix, 把 Obsidian 新增笔记增量镜像到飞书。

查看状态: python scripts/audit_sync_watchdog.py --status
停止看护: python scripts/audit_sync_watchdog.py --stop
"""
import os
import sys
import subprocess

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
WATCHDOG = os.path.join(BASE_DIR, "scripts", "audit_sync_watchdog.py")
OUT = os.path.join(BASE_DIR, "scripts", "audit_sync_watchdog.log")
DETACHED = 0x00000008

args = sys.argv[1:]
out = open(OUT, "a", encoding="utf-8")
subprocess.Popen([PY, WATCHDOG] + args, cwd=BASE_DIR,
                 stdout=out, stderr=out, creationflags=DETACHED, close_fds=True)
print("✓ audit_sync 周期看护已 DETACHED 拉起(脱离会话, 常驻)。")
print("  状态查看: python scripts/audit_sync_watchdog.py --status")
print(f"  心跳日志: {OUT}")
