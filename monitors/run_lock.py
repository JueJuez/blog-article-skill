"""monitors/run_lock.py — 监控运行顶层互斥锁（PLAN-20260828 §7.5 #4）。

防止 run.py（串行 & --parallel）与 run_parallel.py 重叠运行，导致 ledger / pending
队列并发写竞态。

机制：monitors/.run.lock 存 {pid, heartbeat}；
- 持锁者起一个守护心跳线程，每 HEARTBEAT_INTERVAL 秒续期 heartbeat；
- 抢占者发现锁属「存活进程且心跳新鲜」→ 拒绝（block=False）或等待（block=True）；
- 心跳陈旧（> STALE_AFTER，默认 2h）或进程已死 → 视为崩溃，可抢占（防僵尸锁）。
退出（with 结束 / atexit）自动释放，且仅当 pid 匹配时才删文件，避免误删他人锁。
"""

import os
import json
import time
import threading
import atexit

LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".run.lock")
HEARTBEAT_INTERVAL = 30.0
STALE_AFTER = 2 * 3600.0


class RunLockBusy(Exception):
    """另一监控进程正占用运行锁。"""


def _pid_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 进程存在但无信号权限（通常是别的用户/系统进程）→ 视为存活
        return True
    except Exception:
        return False
    return True


def _read() -> dict:
    try:
        with open(LOCK_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write(pid: int, ts: float) -> None:
    tmp = LOCK_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "heartbeat": ts}, f)
        os.replace(tmp, LOCK_PATH)
    except Exception:
        pass


class RunLock:
    def __init__(self, pid: int, heartbeat: bool = True):
        self.pid = pid
        self._stop = threading.Event()
        self._thread = None
        if heartbeat:
            self._thread = threading.Thread(target=self._beat, daemon=True)
            self._thread.start()
        atexit.register(self.release)

    def _beat(self):
        while not self._stop.is_set():
            _write(self.pid, time.time())
            self._stop.wait(HEARTBEAT_INTERVAL)

    def release(self) -> None:
        self._stop.set()
        try:
            cur = _read()
            if cur and cur.get("pid") == self.pid:
                os.remove(LOCK_PATH)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def acquire_run_lock(block: bool = False, timeout: float = 0.0,
                     stale_after: float = STALE_AFTER) -> RunLock:
    """获取运行锁。

    - 空闲 / 自己已持有 / 占用者已死或心跳陈旧 → 直接抢占，返回 RunLock 上下文。
    - 被存活进程新鲜占用：
        block=False → 抛 RunLockBusy；
        block=True   → 每 5s 重试，直到拿到或超时（超时抛 RunLockBusy）。
    """
    pid = os.getpid()
    deadline = time.time() + (timeout or 0)
    while True:
        cur = _read()
        if cur is None or cur.get("pid") == pid:
            _write(pid, time.time())
            return RunLock(pid)
        alive = _pid_alive(cur.get("pid", -1))
        fresh = (time.time() - cur.get("heartbeat", 0)) < stale_after
        if not alive or not fresh:
            _write(pid, time.time())
            return RunLock(pid)
        if not block:
            raise RunLockBusy(f"另一监控进程占用（pid={cur.get('pid')}，心跳 {cur.get('heartbeat')}）")
        if time.time() > deadline:
            raise RunLockBusy(f"等待运行锁超时（占用 pid={cur.get('pid')}）")
        time.sleep(5)
