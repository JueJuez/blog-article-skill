# -*- coding: utf-8 -*-
"""S2-⑥：编排层测试（2026-09-12，PLAN-20260911 S2）。

覆盖内核 `cdp_session.py` 的编排函数（不触真实 Chrome / 不触真实 playwright）：
- probe_endpoint / ensure_endpoint 端点发现编排（healed 改写端口文件、锁内 double-check）
- _clone_lock 文件锁（O_CREAT|O_EXCL、超龄抢占、timeout）
- 持有者注册表（.cdp_holders payload / 注销）
- _pid_alive
- _shutdown_if_last_holder D6 关灯编排（env 开关、保守让位、最后持有者才关灯、锁超时保守）
- _attach 语义（rebuilt 收编 pages[0] / reused 无条件 new_page）
- _launch 30s 超时
"""

import json
import os
import time as _time_mod
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from shared import cdp_session as cdp


# ---------------------------------------------------------------------------
# 通用 fake / 工具
# ---------------------------------------------------------------------------


def _fake_playwright(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    p = MagicMock()
    holder = MagicMock()
    holder.start.return_value = p
    from playwright import sync_api

    monkeypatch.setattr(sync_api, "sync_playwright", lambda: holder)
    return p


def _sentinel_raise(name: str):
    def _f(*args, **kwargs):
        raise AssertionError(f"不应调用 {name}")
    return _f


def _reused_session(endpoint: str = "ws://127.0.0.1:59222/devtools/browser/abc",
                    clone_dir: str = "C:/fake/clone") -> cdp._skill.CdpSession:
    s = cdp._skill.CdpSession.__new__(cdp._skill.CdpSession)
    s._headless = False
    s._use_cdp = True
    s._live = True
    s._own_browser = False
    s._ctx = None
    s._page = None
    s._browser = None
    s._proc = None
    s.cdp_endpoint = endpoint
    s._clone_dir = Path(clone_dir)
    s._holder = None
    s._p = MagicMock()
    return s


@contextmanager
def _busy_lock(*args, **kwargs):
    raise TimeoutError("busy")
    yield  # pragma: no cover


# ---------------------------------------------------------------------------
# probe_endpoint：端点发现编排
# ---------------------------------------------------------------------------


class TestProbeEndpoint:
    def test_reused_no_rewrite(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()
        (clone_dir / "DevToolsActivePort").write_text("59222\n/devtools/browser/abc", encoding="utf-8")
        monkeypatch.setattr(cdp._skill, "_find_clone_chrome_port", lambda d: None)
        endpoint = "ws://127.0.0.1:59222/devtools/browser/abc"
        probed: list = []

        def fake_probe(port, *a, **k):
            probed.append(port)
            return endpoint if port == 59222 else None

        monkeypatch.setattr(cdp._skill, "_probe_endpoint", fake_probe)

        r = cdp._skill.probe_endpoint(str(clone_dir))

        assert r is not None
        assert r.action == "reused"
        assert probed == [59222]

    def test_healed_rewrites_port_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()
        (clone_dir / "DevToolsActivePort").write_text("5494", encoding="utf-8")
        monkeypatch.setattr(cdp._skill, "_find_clone_chrome_port", lambda d: 55873)
        endpoint = "ws://127.0.0.1:55873/devtools/browser/xyz"
        probed: list = []

        def fake_probe(port, *a, **k):
            probed.append(port)
            return endpoint if port == 55873 else None

        monkeypatch.setattr(cdp._skill, "_probe_endpoint", fake_probe)
        rewrites: list = []
        monkeypatch.setattr(cdp._skill, "_rewrite_devtools_port_file",
                            lambda d, ep: rewrites.append((d, ep)) or True)

        r = cdp._skill.probe_endpoint(str(clone_dir))

        assert r.action == "healed"
        assert r.port == 55873
        assert probed == [5494, 55873]
        assert rewrites == [(clone_dir, endpoint)]

    def test_all_dead_returns_none(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setattr(cdp._skill, "_find_clone_chrome_port", lambda d: None)
        monkeypatch.setattr(cdp._skill, "_probe_endpoint", lambda port, *a, **k: None)
        assert cdp._skill.probe_endpoint(str(tmp_path)) is None

    def test_dedup_same_port(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()
        (clone_dir / "DevToolsActivePort").write_text("59222", encoding="utf-8")
        monkeypatch.setattr(cdp._skill, "_find_clone_chrome_port", lambda d: 59222)
        probed: list = []

        def fake_probe(port, *a, **k):
            probed.append(port)
            return "ws://127.0.0.1:59222/devtools/browser/abc"

        monkeypatch.setattr(cdp._skill, "_probe_endpoint", fake_probe)

        r = cdp._skill.probe_endpoint(str(clone_dir))

        assert r.action == "reused"
        assert probed == [59222]


# ---------------------------------------------------------------------------
# ensure_endpoint：外层命中免锁 / 锁内 double-check / 重建
# ---------------------------------------------------------------------------


class TestEnsureEndpointOrchestration:
    def test_outer_hit_skips_lock(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        fake = MagicMock()
        monkeypatch.setattr(cdp._skill, "probe_endpoint", lambda pd=None: fake)
        monkeypatch.setattr(cdp._skill, "_clone_lock", _sentinel_raise("_clone_lock"))
        monkeypatch.setattr(cdp._skill, "_launch_cloned_logged_in_browser",
                            _sentinel_raise("_launch_cloned_logged_in_browser"))

        r = cdp._skill.ensure_endpoint(str(tmp_path))

        assert r is fake

    def test_doublecheck_hit(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        fake = MagicMock()
        probe = MagicMock(side_effect=[None, fake])
        monkeypatch.setattr(cdp._skill, "probe_endpoint", probe)

        r = cdp._skill.ensure_endpoint(str(tmp_path))

        assert r is fake
        assert probe.call_count == 2

    def test_rebuilt_launch(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        probe = MagicMock(side_effect=[None, None])
        monkeypatch.setattr(cdp._skill, "probe_endpoint", probe)
        monkeypatch.setattr(cdp._skill, "_launch_cloned_logged_in_browser",
                            lambda d, source=None: (MagicMock(), "ws://127.0.0.1:5599/devtools/browser/x"))

        r = cdp._skill.ensure_endpoint(str(tmp_path))

        assert r.action == "rebuilt"
        assert r.port == 5599
        assert r.proc is not None


# ---------------------------------------------------------------------------
# _clone_lock：文件锁
# ---------------------------------------------------------------------------


class TestCloneLock:
    def test_creates_and_releases(self, tmp_path: Path):
        p = cdp._skill._lock_path(tmp_path)
        with cdp._skill._clone_lock(p):
            assert p.exists()
            assert str(os.getpid()) in p.read_text(encoding="utf-8")
        assert not p.exists()

    def test_stale_lock_preempted(self, tmp_path: Path):
        p = cdp._skill._lock_path(tmp_path)
        p.write_text("999 old", encoding="utf-8")
        old = _time_mod.time() - 7200
        os.utime(p, (old, old))

        with cdp._skill._clone_lock(p):
            assert p.exists()

        assert not p.exists()

    def test_timeout_raises(self, tmp_path: Path):
        p = cdp._skill._lock_path(tmp_path)
        # 用当前活进程 PID 当持有者：非孤儿锁 → 等待 deadline 后抛 TimeoutError。
        # （旧写法 "999 fresh" 的 PID 999 已死，2026-09-12 孤儿锁修复会立即抢占，测不到超时）
        p.write_text(f"{os.getpid()} alive", encoding="utf-8")
        with pytest.raises(TimeoutError):
            with cdp._skill._clone_lock(p, timeout=0.3):
                pass

    def test_writes_pid_and_iso(self, tmp_path: Path):
        p = cdp._skill._lock_path(tmp_path)
        with cdp._skill._clone_lock(p):
            content = p.read_text(encoding="utf-8")
        assert str(os.getpid()) in content
        assert "T" in content


# ---------------------------------------------------------------------------
# 持有者注册表
# ---------------------------------------------------------------------------


class TestHolders:
    def test_register_writes_payload(self, tmp_path: Path):
        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()
        endpoint = "ws://127.0.0.1:59222/devtools/browser/abc"

        h = cdp._skill._register_holder(clone_dir, endpoint)

        data = json.loads(h.read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid()
        assert data["endpoint"] == endpoint
        assert "ts" in data

    def test_unregister_removes(self, tmp_path: Path):
        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()
        h = cdp._skill._register_holder(clone_dir, "ws://127.0.0.1:59222/x")
        assert h.exists()
        cdp._skill._unregister_holder(h)
        assert not h.exists()

    def test_unregister_none_noop(self):
        cdp._skill._unregister_holder(None)


# ---------------------------------------------------------------------------
# _pid_alive
# ---------------------------------------------------------------------------


class TestPidAlive:
    def test_own_pid_alive(self):
        assert cdp._skill._pid_alive(os.getpid()) is True

    def test_zero_pid_dead(self):
        assert cdp._skill._pid_alive(0) is False


# ---------------------------------------------------------------------------
# _shutdown_if_last_holder：D6 关灯编排
# ---------------------------------------------------------------------------


class TestShutdownIfLastHolder:
    def test_idle_disabled_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setenv("CDP_IDLE_SHUTDOWN", "0")
        monkeypatch.setattr(cdp._skill, "_clone_lock", _sentinel_raise("_clone_lock"))
        cdp._skill._shutdown_if_last_holder(
            tmp_path, "ws://127.0.0.1:59222/devtools/browser/abc", None)

    def test_unmanaged_endpoint_no_lock(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setattr(cdp._skill, "_clone_lock", _sentinel_raise("_clone_lock"))
        monkeypatch.setattr(cdp._skill, "_find_clone_chrome_port", lambda d: None)
        cdp._skill._shutdown_if_last_holder(
            tmp_path, "ws://127.0.0.1:7777/devtools/browser/abc", None)

    def test_other_alive_holder_conservative(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()
        endpoint = "ws://127.0.0.1:59222/devtools/browser/abc"
        (clone_dir / "DevToolsActivePort").write_text("59222", encoding="utf-8")
        own = cdp._skill._register_holder(clone_dir, endpoint)
        other = own.parent / "other.json"
        other.write_text(json.dumps({"pid": 4242, "endpoint": endpoint, "ts": "t"}), encoding="utf-8")
        monkeypatch.setattr(cdp._skill, "_pid_alive", lambda pid: True)
        monkeypatch.setattr(cdp._skill, "_find_clone_chrome_port", lambda d: None)
        monkeypatch.setattr(cdp._skill, "_kill_chrome_on_port", _sentinel_raise("_kill_chrome_on_port"))
        monkeypatch.setattr(cdp._skill, "_kill_clone_chrome", _sentinel_raise("_kill_clone_chrome"))

        cdp._skill._shutdown_if_last_holder(clone_dir, endpoint, own)

        assert (clone_dir / "DevToolsActivePort").exists()
        assert own.exists()

    def test_last_holder_shuts_down(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()
        endpoint = "ws://127.0.0.1:59222/devtools/browser/abc"
        (clone_dir / "DevToolsActivePort").write_text("59222", encoding="utf-8")
        own = cdp._skill._register_holder(clone_dir, endpoint)
        monkeypatch.setattr(cdp._skill, "_find_clone_chrome_port", lambda d: None)
        killed_ports: list = []
        killed_dirs: list = []
        monkeypatch.setattr(cdp._skill, "_kill_chrome_on_port",
                            lambda p: killed_ports.append(p))
        monkeypatch.setattr(cdp._skill, "_kill_clone_chrome",
                            lambda d: killed_dirs.append(d) or 0)

        cdp._skill._shutdown_if_last_holder(clone_dir, endpoint, own)

        assert killed_ports == [59222]
        assert killed_dirs == [clone_dir]
        assert not (clone_dir / "DevToolsActivePort").exists()
        assert not own.exists()

    def test_lock_timeout_conservative(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()
        endpoint = "ws://127.0.0.1:59222/devtools/browser/abc"
        (clone_dir / "DevToolsActivePort").write_text("59222", encoding="utf-8")
        monkeypatch.setattr(cdp._skill, "_find_clone_chrome_port", lambda d: None)
        monkeypatch.setattr(cdp._skill, "_clone_lock", _busy_lock)
        monkeypatch.setattr(cdp._skill, "_shutdown_locked", _sentinel_raise("_shutdown_locked"))

        cdp._skill._shutdown_if_last_holder(clone_dir, endpoint, None)

        assert (clone_dir / "DevToolsActivePort").exists()


# ---------------------------------------------------------------------------
# _attach：rebuilt 收编 pages[0] / reused 无条件 new_page
# ---------------------------------------------------------------------------


class TestAttach:
    def _session(self, monkeypatch: pytest.MonkeyPatch, ctx: MagicMock) -> cdp._skill.CdpSession:
        s = _reused_session()
        p = _fake_playwright(monkeypatch)
        p.chromium.connect_over_cdp.return_value = MagicMock(contexts=[ctx])
        s._p = p
        return s

    def test_rebuilt_adopts_existing_page(self, monkeypatch: pytest.MonkeyPatch):
        sentinel_page = MagicMock(name="about_blank_page")
        ctx = MagicMock()
        ctx.pages = [sentinel_page]
        s = self._session(monkeypatch, ctx)

        s._attach("ws://127.0.0.1:59222/devtools/browser/abc", "rebuilt")

        assert s._page is sentinel_page

    def test_reused_opens_new_page(self, monkeypatch: pytest.MonkeyPatch):
        own_page = MagicMock(name="new_page")
        ctx = MagicMock()
        ctx.new_page.return_value = own_page
        s = self._session(monkeypatch, ctx)

        s._attach("ws://127.0.0.1:59222/devtools/browser/abc", "reused")

        assert s._page is own_page


# ---------------------------------------------------------------------------
# _launch：30s 超时
# ---------------------------------------------------------------------------


class TestLaunchTimeout:
    def test_launch_timeout_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        t = {"now": 1000.0}
        monkeypatch.setattr(cdp._skill, "time", SimpleNamespace(
            time=lambda: t["now"],
            sleep=lambda *a: t.__setitem__("now", t["now"] + 1.0),
        ))
        monkeypatch.setattr(cdp._skill, "_clone_is_fresh", lambda d: True)
        monkeypatch.setattr(cdp._skill.ens, "ensure_profile", lambda *a, **k: None)
        monkeypatch.setattr(cdp._skill, "_find_system_chrome", lambda: "C:/fake/chrome.exe")
        monkeypatch.setattr(cdp._skill, "subprocess",
                            SimpleNamespace(Popen=MagicMock(), DEVNULL=-3))
        monkeypatch.setattr(cdp._skill, "_free_port", lambda: 59222)
        monkeypatch.setattr(cdp._skill, "_read_devtools_port", lambda d: 59222)
        monkeypatch.setattr(cdp._skill, "_probe_chrome_devtools", lambda port: None)

        with pytest.raises(RuntimeError, match="30s 超时"):
            cdp._skill._launch_cloned_logged_in_browser(tmp_path)
