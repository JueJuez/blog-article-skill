#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/launch_audit_watchdog.py — 前台/自带终端拉起 audit_sync 周期看护（非 detached）。

直接 `python scripts/launch_audit_watchdog.py [--interval 300]` 即可（在自带终端或前台跑，
不要靠 detached 脱离会话——detached 父子树有 ~60s 回收怪相，且 detached 父进程跑多任务会失能）。
看护周期运行 audit_sync.py --fix, 把 Obsidian 新增笔记增量镜像到飞书。

查看状态: python scripts/audit_sync_watchdog.py --status
停止看护: python scripts/audit_sync_watchdog.py --stop
"""
import os
import sys
import subprocess

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from shared.rolling_log import cleanup_expired, rolling_log_path  # noqa: E402

PY = sys.executable
WATCHDOG = os.path.join(BASE_DIR, "scripts", "audit_sync_watchdog.py")
OUT_BASE = os.path.join(BASE_DIR, "scripts", "audit_sync_watchdog.log")

args = sys.argv[1:]
cleanup_expired(OUT_BASE)  # 按天滚动（防膨胀），打开前顺手清理过期日志
OUT = rolling_log_path(OUT_BASE)
out = open(OUT, "a", encoding="utf-8")
subprocess.Popen([PY, WATCHDOG] + args, cwd=BASE_DIR,
                 stdout=out, stderr=out, close_fds=True)
print("✓ audit_sync 周期看护已拉起(前台运行, 日志轮转)。")
print("  状态查看: python scripts/audit_sync_watchdog.py --status")
print(f"  心跳日志: {OUT}")
