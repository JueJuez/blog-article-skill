"""scys .lock 残留自动释放测试（DECISION-20260825）。

保护需求：抓取进程异常退出后锁文件残留，下次运行不应被永久卡死；
但真实存活的进程仍须被互斥保护。
"""
import os
import sys
import time
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
for _p in (str(BASE_DIR), str(BASE_DIR / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scys_batch_fetch as sbf


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sbf, "BASE", tmp_path)
    return tmp_path


def _fake_pid_alive(monkeypatch, alive: bool):
    """直接控制内核探活：实现已改为不依赖 psutil 的 `_pid_alive`。"""
    monkeypatch.setattr(sbf, "_pid_alive", lambda pid: alive)


class TestLockStaleRelease:
    def test_dead_pid_lock_auto_released(self, lock_dir, monkeypatch):
        _fake_pid_alive(monkeypatch, alive=False)
        (lock_dir / ".lock").write_text("999999", encoding="utf-8")
        lock = sbf._acquire_lock()
        assert lock == lock_dir / ".lock"
        assert (lock_dir / ".lock").exists()
        sbf._release_lock(lock)

    def test_live_pid_lock_still_blocks(self, lock_dir, monkeypatch):
        # 外来存活进程（非自身 PID）仍须被互斥保护（DECISION-20260825）
        _fake_pid_alive(monkeypatch, alive=True)
        (lock_dir / ".lock").write_text("999999", encoding="utf-8")
        with pytest.raises(SystemExit):
            sbf._acquire_lock()

    def test_self_pid_lock_taken_over(self, lock_dir, monkeypatch):
        # 自身 PID 视为 stale：本进程重入允许接管，不再误判互斥（DECISION-20260825）
        _fake_pid_alive(monkeypatch, alive=True)
        (lock_dir / ".lock").write_text(str(os.getpid()), encoding="utf-8")
        lock = sbf._acquire_lock()
        assert lock == lock_dir / ".lock"
        sbf._release_lock(lock)

    def test_unreadable_pid_old_lock_released(self, lock_dir, monkeypatch):
        p = lock_dir / ".lock"
        p.write_text("garbage", encoding="utf-8")
        old = time.time() - 7 * 3600
        os.utime(p, (old, old))
        lock = sbf._acquire_lock()
        sbf._release_lock(lock)

    def test_unreadable_pid_fresh_lock_blocks(self, lock_dir, monkeypatch):
        (lock_dir / ".lock").write_text("garbage", encoding="utf-8")
        with pytest.raises(SystemExit):
            sbf._acquire_lock()

    def test_lock_records_holder_pid(self, lock_dir):
        lock = sbf._acquire_lock()
        assert (lock_dir / ".lock").read_text(encoding="utf-8") == str(os.getpid())
        sbf._release_lock(lock)
