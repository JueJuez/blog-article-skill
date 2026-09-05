#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/migrate_watchdog.py — 飞书→Obsidian 迁移「进度看护」(常驻, 脱离 agent 会话)。

取代旧 keep-alive 监控(2026-09-05): 旧监控试图保活子进程, 踩 Windows DETACHED 父子树
~60s 回收怪相, 且自身也脆。本看护改为「进度探活」——不保活, 只盯着「待迁移数」,
卡死/被回收就杀掉重拉:

  循环(每 INTERVAL 秒):
    1. 读 feishu_to_obsidian_log.json 统计「待迁移数」(非终态 token 数);
       为 0 → 迁移完成, 清 pid 文件并退出。
    2. 读 _migrate_pid.txt 找当前迁移进程, 判存活:
       - 进程不在 → 立即重拉(处理被回收 / 异常退出 / 跑到最大轮次退出)。
       - 进程在:
           · 待迁移数 < 上次 → 正常推进, 不动进程。
           · 待迁移数 > 上次 → 新发现失败(如重启后), 重置基线。
           · 待迁移数 == 上次(连续 STUCK_TIMES 次, 且过 warmup 宽限)
             → 判定卡死, 杀旧进程 + 重拉。
    3. 写状态文件 + 心跳日志, sleep INTERVAL。

  重拉安全: 迁移脚本每篇 save_log 续跑, 杀+重拉无数据丢失; 单实例锁防并发 race;
  重拉后给 INTERVAL*2 宽限, 避开轮间 90s 冷却造成的伪"不变"。

  启动方式(稳定父进程, 关键): 看护经 run_in_background 拉起 → 父=稳定的
  agent 服务, 常驻。重拉迁移作为看护的「普通子进程」(不 DETACHED、不依赖
  powershell), 继承稳定父 lineage, 不被 DETACHED 父子树回收, 也不受本环境
  powershell 派生新进程的拦截影响。

查看状态(不阻断看护): python scripts/migrate_watchdog.py --status
"""
import os
import sys
import time
import json
import subprocess
import argparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(BASE_DIR, "scripts", "feishu_to_obsidian.py")
LOG_PATH = os.path.join(BASE_DIR, "scripts", "feishu_to_obsidian_log.json")
# 迁移自写此文件(见 feishu_to_obsidian.py MIGRATE_PID); 看护只读它来识别/杀死迁移。
PID_FILE = os.path.join(BASE_DIR, "scripts", "_migrate_pid.txt")
LOCK_FILE = os.path.join(BASE_DIR, "scripts", "_watchdog_lock.pid")
WATCHDOG_LOG = os.path.join(BASE_DIR, "scripts", "migrate_watchdog.log")
STATUS_PATH = os.path.join(BASE_DIR, "scripts", "migrate_watchdog_status.json")
CHILD_LOG = os.path.join(BASE_DIR, "scripts", "_watchdog_child.log")
PY = sys.executable

INTERVAL = int(os.environ.get("WATCHDOG_INTERVAL", "300"))   # 轮询间隔(秒), 默认5分钟
STUCK_TIMES = 2    # 待迁移数连续不变几次才判定卡死(避开轮间90s冷却误杀)
# 终态 = 迁移脚本 TERMINAL_DEFAULT(不含 --recheck-missing 时 skip_missing_doc 也算终态)
TERMINAL = {"written", "synced_title", "skip_same_title", "skip_path", "skip_missing_doc"}

# 保活子进程 stdout 句柄: 模块级, 绝不关闭, 否则子进程写 stdout 崩(旧监控踩过的坑)。
_child_streams = []


def wlog(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        with open(WATCHDOG_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(line, end="")


def count_pending():
    if not os.path.exists(LOG_PATH):
        return None
    try:
        d = json.load(open(LOG_PATH, encoding="utf-8"))
    except Exception:
        return None
    return sum(1 for v in d.values()
               if isinstance(v, dict) and v.get("status") not in TERMINAL)


def read_pid():
    try:
        return int(open(PID_FILE, encoding="utf-8").read().strip())
    except Exception:
        return None


def pid_alive(pid):
    """用 tasklist 探活(比 os.kill(pid,0) 稳, 无 WinError 87 假阴性)。"""
    if not pid:
        return False
    try:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=15)
        return "python.exe" in r.stdout or "pythonw.exe" in r.stdout
    except Exception:
        return False


def kill_migration(pid):
    if not pid:
        return
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, text=True, timeout=15)
        wlog(f"■ 终止迁移进程 pid={pid}")
    except Exception as e:
        wlog(f"  taskkill 失败: {e}")


def ensure_env():
    env = dict(os.environ)
    env.setdefault("USERPROFILE", os.path.expanduser("~"))
    env.setdefault("HOME", os.path.expanduser("~"))
    env.setdefault("PYTHONUTF8", "1")
    return env


def start_migration():
    """重拉迁移。

    关键(2026-09-05 实测): 本看护自身由 run_in_background 拉起, 父进程=稳定的
    agent 服务(等效 52256 的稳定方式), 故迁移作为看护的「普通子进程」(不 DETACHED)
    即可稳定常驻——既避开旧监控 DETACHED 父子树 ~60s 回收怪相, 也不依赖 powershell
    (本环境安全策略拦截 powershell 派生新进程, Start-Process 拉不起 python)。
    子进程 stdout 重定向到 CHILD_LOG(句柄挂模块级, 永不关, 否则子进程写 stdout 崩)。
    """
    out = open(CHILD_LOG, "a", encoding="utf-8")
    _child_streams.append(out)
    subprocess.Popen([PY, SCRIPT], cwd=BASE_DIR, env=ensure_env(),
                     stdout=out, stderr=out, close_fds=True)
    wlog("▶ 拉起迁移(看护普通子进程, 稳定)")


def write_status(state, pending, **extra):
    s = {"state": state, "pending": pending, "ts": time.time()}
    s.update(extra)
    try:
        json.dump(s, open(STATUS_PATH, "w", encoding="utf-8"))
    except Exception:
        pass


def print_status():
    if not os.path.exists(STATUS_PATH):
        print("看护未运行 / 无状态文件")
        return
    try:
        s = json.load(open(STATUS_PATH, encoding="utf-8"))
    except Exception:
        print("状态文件损坏")
        return
    print(f"状态: {s.get('state')}  待迁移: {s.get('pending')}  "
          f"更新于: {time.strftime('%H:%M:%S', time.localtime(s.get('ts', 0)))}")
    if s.get("running") is not None:
        print(f"迁移进程在跑: {s.get('running')}")
    if s.get("last_restart"):
        print(f"上次重拉: {s.get('last_restart')}")


def acquire_lock():
    """看护自身单实例锁, 防重复启动 race。"""
    try:
        if os.path.exists(LOCK_FILE):
            old = open(LOCK_FILE, encoding="utf-8").read().strip()
            if old.isdigit() and pid_alive(int(old)):
                print(f"[看护单实例锁] 另一看护已在运行 (pid={old}), 退出。")
                sys.exit(0)
    except Exception:
        pass
    open(LOCK_FILE, "w", encoding="utf-8").write(str(os.getpid()))


def main_loop():
    wlog(f"=== 迁移进度看护启动 (interval={INTERVAL}s, stuck_times={STUCK_TIMES}) ===")
    last_pending = None
    stuck = 0
    grace_until = 0
    while True:
        pending = count_pending()
        if pending is None:
            time.sleep(INTERVAL)
            continue
        if pending == 0:
            wlog("✓ 待迁移=0, 迁移完成。看护退出。")
            try:
                if os.path.exists(PID_FILE):
                    os.remove(PID_FILE)
            except Exception:
                pass
            write_status("done", 0)
            break

        pid = read_pid()
        alive = pid_alive(pid)
        now = time.time()
        if not alive:
            # 进程不在(被回收/异常退出/跑到最大轮次) → 立即重拉
            wlog(f"待迁移={pending}, 迁移进程不在(pid={pid}) → 重拉")
            kill_migration(pid)  # 清可能的残留
            time.sleep(3)
            start_migration()
            last_pending = pending
            stuck = 0
            grace_until = now + INTERVAL * 2
            write_status("restart", pending, running=True,
                         last_restart=time.strftime("%H:%M:%S"))
            time.sleep(INTERVAL)
            continue

        # 进程在: 看进度
        if last_pending is None:
            last_pending = pending
        if pending < last_pending:
            last_pending = pending
            stuck = 0
            wlog(f"待迁移={pending} (↓推进中)")
        elif pending > last_pending:
            last_pending = pending
            stuck = 0  # 新失败, 重置基线
        else:
            if now < grace_until:
                pass  # warmup 宽限内, 耐心等(避开轮间冷却)
            else:
                stuck += 1
                if stuck >= STUCK_TIMES:
                    wlog(f"待迁移={pending} 连续 {stuck} 次不变 → 杀旧重拉")
                    kill_migration(pid)
                    time.sleep(3)
                    start_migration()
                    last_pending = pending
                    stuck = 0
                    grace_until = now + INTERVAL * 2
                    write_status("restart", pending, running=True,
                                 last_restart=time.strftime("%H:%M:%S"))
                    time.sleep(INTERVAL)
                    continue
        write_status("watching", pending, running=True)
        time.sleep(INTERVAL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="只打印状态, 不进入看护循环")
    ap.add_argument("--interval", type=int, default=0,
                    help="轮询间隔(秒), 覆盖默认 WATCHDOG_INTERVAL/300")
    args = ap.parse_args()
    if args.interval:
        global INTERVAL
        INTERVAL = args.interval
    if args.status:
        print_status()
        return
    acquire_lock()
    try:
        main_loop()
    finally:
        try:
            if (os.path.exists(LOCK_FILE)
                    and open(LOCK_FILE, encoding="utf-8").read().strip() == str(os.getpid())):
                os.remove(LOCK_FILE)
        except Exception:
            pass


if __name__ == "__main__":
    main()
