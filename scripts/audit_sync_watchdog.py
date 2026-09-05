#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/audit_sync_watchdog.py — 周期运行 audit_sync.py --fix，保持飞书镜像与 Obsidian 同步。

复用 migrate_watchdog 的常驻模式（单实例锁 / 状态文件 / 心跳日志），但语义不同：
audit_sync 是短时任务（不是长进程），故本看护 = 周期调度器——每隔 INTERVAL 跑一次
audit_sync.py --fix，把 Obsidian 新增/变更的笔记增量补传到飞书。

飞书空间/根节点：优先读 .env 的 FEISHU_WIKI_SPACE / FEISHU_WIKI_PARENT_NODE；
未配则用 scan_feishu_tree.py 里硬编码的默认值（AI 总结笔记 树），保证开箱可跑。

用法：
  python scripts/audit_sync_watchdog.py [--interval 300]   # 前台
  python scripts/launch_audit_watchdog.py [--interval 300] # DETACHED 常驻
  python scripts/audit_sync_watchdog.py --status
  python scripts/audit_sync_watchdog.py --stop
"""
import os
import sys
import re
import time
import json
import argparse
import subprocess

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

PY = sys.executable
WATCHDOG = os.path.join(BASE_DIR, "scripts", "audit_sync.py")
LOCK = os.path.join(BASE_DIR, "scripts", "_audit_watchdog_lock.pid")
STATUS = os.path.join(BASE_DIR, "scripts", "audit_sync_watchdog_status.json")
LOG = os.path.join(BASE_DIR, "scripts", "audit_sync_watchdog.log")

# scan_feishu_tree.py 里的飞书空间/根节点（AI 总结笔记），作 .env 缺省兜底
DEFAULT_SPACE = "7636965310725115074"
DEFAULT_PARENT = "FX33wKHwZiMzJqk7BQQctHD3nKh"

INTERVAL = int(os.environ.get("AUDIT_WATCHDOG_INTERVAL", "300"))


def wlog(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


def alive(pid: int) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    except Exception:
        return False


def run_once() -> tuple:
    """运行一次 audit_sync.py --fix，返回 (returncode, 尾部输出)。"""
    env = dict(os.environ)
    env.setdefault("FEISHU_WIKI_SPACE", DEFAULT_SPACE)
    env.setdefault("FEISHU_WIKI_PARENT_NODE", DEFAULT_PARENT)
    try:
        r = subprocess.run([PY, WATCHDOG, "--fix"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
        return r.returncode, (r.stdout + r.stderr)[-3000:]
    except Exception as e:
        return -1, str(e)


def main():
    ap = argparse.ArgumentParser(description="audit_sync 周期看护（增量镜像飞书）")
    ap.add_argument("--interval", type=int, default=INTERVAL, help="轮询间隔(秒)")
    ap.add_argument("--status", action="store_true", help="打印状态后退出")
    ap.add_argument("--stop", action="store_true", help="停止看护")
    args = ap.parse_args()

    if args.status:
        if os.path.exists(STATUS):
            print(open(STATUS, encoding="utf-8").read())
        else:
            print("无状态文件（看护可能未运行）")
        return
    if args.stop:
        if os.path.exists(LOCK):
            pid = open(LOCK, encoding="utf-8").read().strip()
            if pid:
                try:
                    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
                except Exception:
                    pass
            try:
                os.remove(LOCK)
            except Exception:
                pass
        print("✓ 已发送停止信号")
        return

    # 单实例锁
    if os.path.exists(LOCK):
        pid = open(LOCK, encoding="utf-8").read().strip()
        if pid and alive(int(pid)):
            print(f"✗ 已有实例运行 (pid {pid})，退出")
            return
    with open(LOCK, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    wlog(f"审计看护启动 (interval={args.interval}s)")
    try:
        while True:
            rc, out = run_once()
            m = re.search(r"缺飞书（待补）：(\d+)", out)
            n_missing = m.group(1) if m else "?"
            m2 = re.search(r"复验后缺飞书：(\d+)", out)
            n_remain = m2.group(1) if m2 else n_missing
            status = {
                "pid": os.getpid(),
                "last_run": time.strftime("%Y-%m-%d %H:%M:%S"),
                "returncode": rc,
                "missing_reported": n_missing,
                "missing_after_verify": n_remain,
            }
            with open(STATUS, "w", encoding="utf-8") as f:
                json.dump(status, f, ensure_ascii=False, indent=2)
            wlog(f"run rc={rc} 报告缺飞书={n_missing} 复验缺飞书={n_remain}")
            time.sleep(args.interval)
    finally:
        try:
            os.remove(LOCK)
        except Exception:
            pass


if __name__ == "__main__":
    main()
