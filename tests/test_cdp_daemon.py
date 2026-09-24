# -*- coding: utf-8 -*-
"""cdp_daemon 控制面 + reaper 集成测试。

直接以子进程拉起 cdp_daemon.py（不依赖 PowerShell Start-Process，
仅验证 socket 协议与 reaper 行为；生产脱离 job 的启动由 launch_cdp_daemon.py 负责）。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
import shared.cdp_session as scs  # noqa: E402

SKILL_DIR = Path(scs._resolve_skill_dir())
sys.path.insert(0, str(SKILL_DIR))

import cdp_session as S  # noqa: E402

# 隔离 clone 目录（必须在本进程与 daemon 子进程都设置，否则 STATE_PATH 与
# daemon 实际写的 state 文件路径对不上，导致测试连错端口）。
_ISOLATED_CLONE = Path(tempfile.mkdtemp(prefix="cdp_test_clone_"))
os.environ["CDP_PROFILE_DIR"] = str(_ISOLATED_CLONE)

STATE_PATH = S._default_clone_dir().parent / f".{S._default_clone_dir().name}.cdp.daemon.json"


def _send(port: int, cmd: str, timeout: float = 60.0) -> str:
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        s.sendall((cmd + "\n").encode("utf-8"))
        return s.makefile("r").readline().strip()
    finally:
        s.close()


def _state() -> dict:
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def daemon():
    # 复用模块级隔离 clone（STATE_PATH 据此计算），避免 daemon 写的 state 文件路径对不上
    clone = _ISOLATED_CLONE
    # 短空闲超时，便于验证 reaper
    env = dict(os.environ)
    env["CDP_PROFILE_DIR"] = str(clone)
    env["CDP_IDLE_TIMEOUT"] = "2"
    env["CDP_REAPER_INTERVAL"] = "1"
    env["CDP_DAEMON_DRY"] = "1"  # 不真正拉起 Chrome，仅验证控制面/reaper 逻辑
    proc = subprocess.Popen(
        [sys.executable, str(SKILL_DIR / "cdp_daemon.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # 等状态文件出现（daemon 已监听）
    port = None
    for _ in range(50):
        if STATE_PATH.exists():
            try:
                port = _state().get("port")
                if port:
                    break
            except Exception:
                pass
        time.sleep(0.1)
    assert port, "daemon 未在 5s 内就绪"
    yield port
    # 清理：优先 QUIT，否则杀进程
    try:
        _send(port, "QUIT", timeout=5)
    except Exception:
        proc.kill()
    try:
        if proc.poll() is None:
            proc.wait(timeout=5)
    except Exception:
        proc.kill()


def test_ping(daemon):
    assert _send(daemon, "PING") == "PONG"


def test_acquire_returns_endpoint(daemon):
    ep = _send(daemon, "ACQUIRE")
    assert ep and ep.startswith("ws://"), f"ACQUIRE 应返回 ws:// endpoint，实得 {ep!r}"
    # ACQUIRE 后 daemon 内缓存了 endpoint（reaper 未触发时不应为 None）
    assert _state().get("endpoint"), "ACQUIRE 后 daemon 应已缓存 endpoint"
    assert _send(daemon, "RELEASE") == "OK"


def test_reaper_clears_endpoint_after_idle(daemon):
    _send(daemon, "ACQUIRE")
    _send(daemon, "RELEASE")
    # 空闲 2s 后应被 reaper 关闭 Chrome：daemon 把缓存 endpoint 置空、写回状态文件
    time.sleep(4)
    assert _state().get("endpoint") is None, "reaper 应在空闲超时后清空缓存 endpoint"


def test_quit_stops_daemon(daemon):
    assert _send(daemon, "QUIT") == "OK"
    # 状态文件应在 QUIT 后被删除
    time.sleep(1.0)
    assert not STATE_PATH.exists(), "QUIT 后状态文件应被清理"
    # daemon 进程已退出：端口不再可达
    with pytest.raises(OSError):
        _send(daemon, "PING", timeout=3)
