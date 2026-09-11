# -*- coding: utf-8 -*-
"""S2-⑤：内核命名空间迁移版复用测试（2026-09-12）。

背景（PLAN-20260911 S2）：连接语义（D5 实例常驻 / D6 最后持有者关灯 / 文件锁）
已上移用户级技能 `cdp-automation-profile` 的 `cdp_session.py` 内核；项目侧
`shared/cdp_session.py` 只剩薄子类。本文件将旧 44+ 用例迁移至内核命名空间
（`cdp._skill`），新增 2026-09-10 微修回归（kill_clone 计数）与 2026-09-11
实踩坑（netstat/taskkill/PowerShell CIM/tasklist 一律 list 形式）回归。

设计要点：
- 内核顶层无 playwright import；`sync_playwright` 为方法内 import（__init__ /
  restart_fresh）→ patch `playwright.sync_api.sync_playwright` 模块属性对所有
  路径生效。
- 内核 subprocess 一律 list 形式 → fake 先 join 再分发。
- Magic Mock 陷阱：`_attach` 的 `ctx.pages[0] if getattr(ctx, "pages", None) else
  ctx.new_page()` → reused 场景需显式 `ctx.pages = []`。
"""

import time as _time_mod
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from shared import cdp_session as cdp
import profile_clone_fetch as pcf


# ---------------------------------------------------------------------------
# 通用 fake / 工具
# ---------------------------------------------------------------------------


def _fake_playwright(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """patch playwright.sync_api.sync_playwright（方法内 import 从 sys.modules 取）。"""
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


def _make_clone(tmp_path: Path, marker: str = "2026-09-10", with_cookies: bool = True) -> None:
    if with_cookies:
        (tmp_path / "Default" / "Network").mkdir(parents=True)
        (tmp_path / "Default" / "Network" / "Cookies").write_text("cookie", encoding="utf-8")
    if marker:
        (tmp_path / pcf.MARKER_DATE).write_text(marker, encoding="utf-8")


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


def _result(action: str, endpoint: str, proc=None) -> cdp._skill.EnsureResult:
    return cdp._skill.EnsureResult(
        endpoint=endpoint,
        port=cdp._skill._endpoint_port(endpoint) or 0,
        profile_dir=Path("C:/fake/clone"),
        action=action,
        proc=proc,
    )


# ---------------------------------------------------------------------------
# A · 端口文件读取
# ---------------------------------------------------------------------------


class TestReadDevtoolsPort:
    def test_normal_two_lines(self, tmp_path: Path):
        (tmp_path / "DevToolsActivePort").write_text("55873\n/devtools/browser/abc", encoding="utf-8")
        assert cdp._skill._read_devtools_port(tmp_path) == 55873

    def test_missing_file_returns_none(self, tmp_path: Path):
        assert cdp._skill._read_devtools_port(tmp_path) is None

    def test_bad_port_text_returns_none(self, tmp_path: Path):
        (tmp_path / "DevToolsActivePort").write_text("not-a-port", encoding="utf-8")
        assert cdp._skill._read_devtools_port(tmp_path) is None

    def test_only_port_line(self, tmp_path: Path):
        (tmp_path / "DevToolsActivePort").write_text("55873", encoding="utf-8")
        assert cdp._skill._read_devtools_port(tmp_path) == 55873


# ---------------------------------------------------------------------------
# C · __init__ 编排（健康复用 / 复用开新页 / 重建回退 / 连接失败重试）
# ---------------------------------------------------------------------------


class TestInitOrchestration:
    def test_healthy_reuse(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """clone 健康 → reused → 不杀 Chrome、不 Popen、不 stop。"""
        p = _fake_playwright(monkeypatch)
        endpoint = "ws://127.0.0.1:59222/devtools/browser/abc"
        monkeypatch.setattr(cdp._skill, "ensure_endpoint",
                            lambda pd=None: _result("reused", endpoint))
        registered: list = []
        monkeypatch.setattr(cdp._skill, "_register_holder",
                            lambda d, ep: registered.append((d, ep)) or MagicMock())

        s = cdp._skill.CdpSession(profile_dir=str(tmp_path))

        assert s._own_browser is False
        assert s._proc is None
        assert s.cdp_endpoint == endpoint
        assert registered == [(s._clone_dir, endpoint)]
        p.stop.assert_not_called()

    def test_reuse_opens_own_page(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """复用场景 _attach 走 new_page（own page）。"""
        _fake_playwright(monkeypatch)
        endpoint = "ws://127.0.0.1:59222/devtools/browser/abc"
        monkeypatch.setattr(cdp._skill, "ensure_endpoint",
                            lambda pd=None: _result("reused", endpoint))
        own_page = MagicMock()
        ctx = MagicMock()
        ctx.new_page.return_value = own_page
        monkeypatch.setattr(cdp._skill.CdpSession, "_attach",
                            lambda self, ep, action="reused": setattr(self, "_page", own_page))

        s = cdp._skill.CdpSession(profile_dir=str(tmp_path))
        assert s._page is own_page

    def test_rebuilt_fallback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """probe 不健康 → rebuilt → own_browser True、接管 proc。"""
        _fake_playwright(monkeypatch)
        endpoint = "ws://127.0.0.1:59222/devtools/browser/abc"
        monkeypatch.setattr(cdp._skill, "ensure_endpoint",
                            lambda pd=None: _result("rebuilt", endpoint, proc="PROC"))

        s = cdp._skill.CdpSession(profile_dir=str(tmp_path))
        assert s._own_browser is True
        assert s._proc == "PROC"

    def test_connect_failure_rebuilds(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """connect 失败 → 杀端口 + 杀 clone → _launch 重建 → stop 一次。"""
        p = _fake_playwright(monkeypatch)
        endpoint = "ws://127.0.0.1:59222/devtools/browser/abc"
        monkeypatch.setattr(cdp._skill, "ensure_endpoint",
                            lambda pd=None: _result("reused", endpoint))
        p.chromium.connect_over_cdp.side_effect = [RuntimeError("boom"), MagicMock()]
        killed_ports: list = []
        killed_dirs: list = []
        monkeypatch.setattr(cdp._skill, "_kill_chrome_on_port",
                            lambda port: killed_ports.append(port))
        monkeypatch.setattr(cdp._skill, "_kill_clone_chrome",
                            lambda d: killed_dirs.append(d) or 0)
        monkeypatch.setattr(cdp._skill, "_launch_cloned_logged_in_browser",
                            lambda d, source=None: (MagicMock(), "ws://127.0.0.1:5599/devtools/browser/x"))

        s = cdp._skill.CdpSession(profile_dir=str(tmp_path))

        assert killed_ports == [59222]
        assert killed_dirs == [s._clone_dir]
        assert s._own_browser is True
        assert p.stop.call_count == 1


# ---------------------------------------------------------------------------
# D · close() D6 契约（只关灯不 taskkill）
# ---------------------------------------------------------------------------


class TestCloseD6Contract:
    def test_reused_close_only_disconnects(self, monkeypatch: pytest.MonkeyPatch):
        s = _reused_session()
        holder = MagicMock()
        s._holder = holder
        calls: list = []
        monkeypatch.setattr(cdp._skill, "_shutdown_if_last_holder",
                            lambda d, ep, h: calls.append((d, ep, h)))
        monkeypatch.setattr(cdp._skill, "_kill_chrome_on_port", _sentinel_raise("_kill_chrome_on_port"))

        s.close()

        assert calls == [(s._clone_dir, s.cdp_endpoint, holder)]
        assert s._holder is None

    def test_owned_close_no_direct_taskkill(self, monkeypatch: pytest.MonkeyPatch):
        s = _reused_session()
        s._own_browser = True
        s._holder = MagicMock()
        calls: list = []
        monkeypatch.setattr(cdp._skill, "_shutdown_if_last_holder",
                            lambda d, ep, h: calls.append((d, ep, h)))
        monkeypatch.setattr(cdp._skill, "_kill_chrome_on_port", _sentinel_raise("_kill_chrome_on_port"))

        s.close()

        assert len(calls) == 1
        assert s._live is False

    def test_close_clears_holder_and_live(self, monkeypatch: pytest.MonkeyPatch):
        s = _reused_session()
        s._holder = MagicMock()
        monkeypatch.setattr(cdp._skill, "_shutdown_if_last_holder", lambda d, ep, h: None)

        s.close()

        assert s._holder is None
        assert s._live is False


# ---------------------------------------------------------------------------
# E · _launch 条件杀 Chrome（fresh 不杀 / stale 才杀 / 缺 Chrome 报错）
# ---------------------------------------------------------------------------


class TestLaunchConditionalKill:
    def _patch_common(self, monkeypatch: pytest.MonkeyPatch, closed: list) -> None:
        monkeypatch.setattr(cdp._skill, "_clone_is_fresh", lambda d: True)
        monkeypatch.setattr(cdp._skill, "_ensure_chrome_closed", lambda: closed.append("kill"))
        monkeypatch.setattr(cdp._skill.ens, "ensure_profile", lambda *a, **k: None)
        monkeypatch.setattr(cdp._skill, "_find_system_chrome", lambda: "C:/fake/chrome.exe")
        monkeypatch.setattr(cdp._skill, "subprocess",
                            SimpleNamespace(Popen=MagicMock(), DEVNULL=-3))
        monkeypatch.setattr(cdp._skill, "_free_port", lambda: 59222)
        monkeypatch.setattr(cdp._skill, "_read_devtools_port", lambda d: 59222)
        monkeypatch.setattr(cdp._skill, "_probe_chrome_devtools",
                            lambda port: {"Browser": "Chrome/122"})

    def test_fresh_no_kill(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        closed: list = []
        self._patch_common(monkeypatch, closed)

        proc, endpoint = cdp._skill._launch_cloned_logged_in_browser(tmp_path)

        assert closed == []
        assert endpoint == "ws://127.0.0.1:59222"
        assert proc is not None

    def test_stale_kills_chrome(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        closed: list = []
        self._patch_common(monkeypatch, closed)
        monkeypatch.setattr(cdp._skill, "_clone_is_fresh", lambda d: False)

        cdp._skill._launch_cloned_logged_in_browser(tmp_path)

        assert closed == ["kill"]

    def test_missing_chrome_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        closed: list = []
        self._patch_common(monkeypatch, closed)
        monkeypatch.setattr(cdp._skill, "_find_system_chrome", lambda: None)

        with pytest.raises(RuntimeError, match="Chrome"):
            cdp._skill._launch_cloned_logged_in_browser(tmp_path)


# ---------------------------------------------------------------------------
# F · clone_is_fresh（项目侧无参版）
# ---------------------------------------------------------------------------


class TestCloneIsFresh:
    def _patch(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(pcf, "CLONE_DIR", tmp_path)
        monkeypatch.delenv("CDP_SYNC_INTERVAL_DAYS", raising=False)

    def test_fresh_today(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        self._patch(monkeypatch, tmp_path)
        _make_clone(tmp_path, marker=date.today().isoformat())
        assert pcf.clone_is_fresh() is True

    def test_stale_3days(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        self._patch(monkeypatch, tmp_path)
        _make_clone(tmp_path, marker=(date.today() - timedelta(days=3)).isoformat())
        assert pcf.clone_is_fresh() is False

    def test_missing_marker(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        self._patch(monkeypatch, tmp_path)
        _make_clone(tmp_path, marker=None)
        assert pcf.clone_is_fresh() is False

    def test_missing_cookie(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        self._patch(monkeypatch, tmp_path)
        _make_clone(tmp_path, with_cookies=False)
        assert pcf.clone_is_fresh() is False

    def test_corrupted_marker(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        self._patch(monkeypatch, tmp_path)
        _make_clone(tmp_path, marker="garbage!!")
        assert pcf.clone_is_fresh() is False

    def test_timestamp_marker(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        self._patch(monkeypatch, tmp_path)
        _make_clone(tmp_path, marker=str(int(_time_mod.mktime(date.today().timetuple()))))
        assert pcf.clone_is_fresh() is True

    def test_interval_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        self._patch(monkeypatch, tmp_path)
        monkeypatch.setenv("CDP_SYNC_INTERVAL_DAYS", "30")
        _make_clone(tmp_path, marker=(date.today() - timedelta(days=7)).isoformat())
        assert pcf.clone_is_fresh() is True


# ---------------------------------------------------------------------------
# H · kill_chrome_on_port（2026-09-11 实踩回归：list 形式）
# ---------------------------------------------------------------------------


class TestKillChromeOnPort:
    def _fake_run(self, calls: list, line: str | None):
        def _run(cmd, *args, **kwargs):
            cmd_s = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            if "netstat" in cmd_s:
                if line is None:
                    raise RuntimeError("netstat fail")
                return MagicMock(stdout=line)
            calls.append(cmd)
            return MagicMock()
        return _run

    def _patch_subprocess(self, monkeypatch: pytest.MonkeyPatch, calls: list, line: str | None):
        monkeypatch.setattr(cdp._skill, "subprocess",
                            SimpleNamespace(run=self._fake_run(calls, line)))

    def test_kills_listener_pid(self, monkeypatch: pytest.MonkeyPatch):
        calls: list = []
        self._patch_subprocess(monkeypatch, calls,
                               "  TCP    127.0.0.1:59222    0.0.0.0:0    LISTENING    4321\n")
        cdp._skill._kill_chrome_on_port(59222)
        assert len(calls) == 1
        assert "4321" in " ".join(calls[0])

    def test_no_listener_no_kill(self, monkeypatch: pytest.MonkeyPatch):
        calls: list = []
        self._patch_subprocess(monkeypatch, calls,
                               "  TCP    127.0.0.1:99999    0.0.0.0:0    LISTENING    4321\n")
        cdp._skill._kill_chrome_on_port(59222)
        assert calls == []

    def test_netstat_failure_swallowed(self, monkeypatch: pytest.MonkeyPatch):
        calls: list = []
        self._patch_subprocess(monkeypatch, calls, None)
        cdp._skill._kill_chrome_on_port(59222)  # 不应抛
        assert calls == []


# ---------------------------------------------------------------------------
# I · restart_fresh（复用会话重建 / 自有会话拒绝）
# ---------------------------------------------------------------------------


class TestRestartFresh:
    def test_reused_rebuilds(self, monkeypatch: pytest.MonkeyPatch):
        s = _reused_session()
        killed_ports: list = []
        monkeypatch.setattr(cdp._skill, "_kill_chrome_on_port",
                            lambda port: killed_ports.append(port))
        monkeypatch.setattr(cdp._skill, "_kill_clone_chrome", lambda d: 0)
        monkeypatch.setattr(cdp._skill, "_launch_cloned_logged_in_browser",
                            lambda d, source=None: (MagicMock(), "ws://127.0.0.1:7777/devtools/browser/y"))
        registered: list = []
        monkeypatch.setattr(cdp._skill, "_register_holder",
                            lambda d, ep: registered.append((d, ep)) or MagicMock())
        _fake_playwright(monkeypatch)

        ok = s.restart_fresh()

        assert ok is True
        assert killed_ports == [59222]
        assert s._own_browser is True
        assert s.cdp_endpoint == "ws://127.0.0.1:7777/devtools/browser/y"
        assert s._proc is not None

    def test_owned_refuses(self):
        s = _reused_session()
        s._own_browser = True
        assert s.restart_fresh() is False


# ---------------------------------------------------------------------------
# J · __init__ 失败回滚（2026-09-10 真机 loop 泄漏故障背景）
# ---------------------------------------------------------------------------


class TestInitFailureRollback:
    def test_launch_failure_rollback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """ensure_endpoint 抛 → pw stop 一次 + holder 未注册不炸。"""
        p = _fake_playwright(monkeypatch)
        monkeypatch.setattr(cdp._skill, "ensure_endpoint",
                            MagicMock(side_effect=RuntimeError("no chrome")))
        with pytest.raises(RuntimeError, match="no chrome"):
            cdp._skill.CdpSession(profile_dir=str(tmp_path))
        assert p.stop.call_count == 1

    def test_double_failure_stops_twice(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """复用 connect 失败 + 重建也失败 → 外层 rollback stop 第二次。"""
        p = _fake_playwright(monkeypatch)
        endpoint = "ws://127.0.0.1:59222/devtools/browser/abc"
        monkeypatch.setattr(cdp._skill, "ensure_endpoint",
                            lambda pd=None: _result("reused", endpoint))
        p.chromium.connect_over_cdp.side_effect = RuntimeError("boom")
        monkeypatch.setattr(cdp._skill, "_kill_chrome_on_port", lambda port: None)
        monkeypatch.setattr(cdp._skill, "_kill_clone_chrome", lambda d: 0)
        monkeypatch.setattr(cdp._skill, "_launch_cloned_logged_in_browser",
                            MagicMock(side_effect=RuntimeError("relaunch fail")))

        with pytest.raises(RuntimeError, match="relaunch fail"):
            cdp._skill.CdpSession(profile_dir=str(tmp_path))
        assert p.stop.call_count == 2

    def test_success_no_stop(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        p = _fake_playwright(monkeypatch)
        endpoint = "ws://127.0.0.1:59222/devtools/browser/abc"
        monkeypatch.setattr(cdp._skill, "ensure_endpoint",
                            lambda pd=None: _result("reused", endpoint))
        cdp._skill.CdpSession(profile_dir=str(tmp_path))
        p.stop.assert_not_called()


# ---------------------------------------------------------------------------
# from_endpoint 回滚
# ---------------------------------------------------------------------------


class TestFromEndpointRollback:
    def test_connect_failure_stops_playwright(self, monkeypatch: pytest.MonkeyPatch):
        p = _fake_playwright(monkeypatch)
        p.chromium.connect_over_cdp.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            cdp._skill.CdpSession.from_endpoint("ws://127.0.0.1:59222/devtools/browser/abc")
        p.stop.assert_called_once()

    def test_success_no_stop(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        p = _fake_playwright(monkeypatch)
        monkeypatch.setattr(cdp._skill, "_find_clone_chrome_port", lambda d: None)

        s = cdp._skill.CdpSession.from_endpoint("ws://127.0.0.1:59222/devtools/browser/abc",
                                                profile_dir=str(tmp_path))
        assert s._own_browser is False
        assert s._holder is None
        p.stop.assert_not_called()


# ---------------------------------------------------------------------------
# K · _get_chrome_cmdlines（2026-09-11 实踩回归：PowerShell CIM + list）
# ---------------------------------------------------------------------------


class TestGetChromeCmdlines:
    def _patch_win(self, monkeypatch: pytest.MonkeyPatch, fake_run):
        monkeypatch.setattr(cdp._skill.sys, "platform", "win32")
        monkeypatch.setattr(cdp._skill, "subprocess", SimpleNamespace(run=fake_run))

    def test_tab_output_parsed(self, monkeypatch: pytest.MonkeyPatch):
        out = "ProcessId\tCommandLine\n34220\tchrome.exe --user-data-dir=C:\\clone --remote-debugging-port=55873\n11568\tchrome.exe --type=crashpad-handler --user-data-dir=C:\\clone\n"
        calls: list = []

        def fake_run(cmd, *a, **k):
            cmd_s = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            calls.append(cmd_s)
            return MagicMock(stdout=out)

        self._patch_win(monkeypatch, fake_run)
        rows = cdp._skill._get_chrome_cmdlines()
        assert rows == [
            (34220, "chrome.exe --user-data-dir=C:\\clone --remote-debugging-port=55873"),
            (11568, "chrome.exe --type=crashpad-handler --user-data-dir=C:\\clone"),
        ]

    def test_skips_invalid_rows(self, monkeypatch: pytest.MonkeyPatch):
        out = "\nProcessId\tCommandLine\nabc\tnot-pid\n42\tchrome.exe --x\n"
        self._patch_win(monkeypatch, lambda cmd, *a, **k: MagicMock(stdout=out))
        rows = cdp._skill._get_chrome_cmdlines()
        assert rows == [(42, "chrome.exe --x")]

    def test_whitespace_fallback(self, monkeypatch: pytest.MonkeyPatch):
        out = "34220 chrome.exe --flag\n"
        self._patch_win(monkeypatch, lambda cmd, *a, **k: MagicMock(stdout=out))
        rows = cdp._skill._get_chrome_cmdlines()
        assert rows == [(34220, "chrome.exe --flag")]

    def test_subprocess_failure_returns_empty(self, monkeypatch: pytest.MonkeyPatch):
        def fake_run(cmd, *a, **k):
            raise RuntimeError("boom")
        self._patch_win(monkeypatch, fake_run)
        assert cdp._skill._get_chrome_cmdlines() == []

    def test_non_windows_returns_empty(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(cdp._skill.sys, "platform", "linux")
        monkeypatch.setattr(cdp._skill, "subprocess",
                            SimpleNamespace(run=_sentinel_raise("subprocess.run")))
        assert cdp._skill._get_chrome_cmdlines() == []


# ---------------------------------------------------------------------------
# L · _find_clone_chrome_port（cmd 不 lower → 测试用小写路径保持一致）
# ---------------------------------------------------------------------------


class TestFindCloneChromePort:
    def _rows(self, monkeypatch: pytest.MonkeyPatch, rows: list):
        monkeypatch.setattr(cdp._skill, "_get_chrome_cmdlines", lambda: rows)

    def test_plain_match(self, monkeypatch: pytest.MonkeyPatch):
        clone_dir = Path("c:/clone")
        self._rows(monkeypatch, [(111, "chrome.exe --user-data-dir=c:\\clone --remote-debugging-port=55873")])
        assert cdp._skill._find_clone_chrome_port(clone_dir) == 55873

    def test_quoted_match(self, monkeypatch: pytest.MonkeyPatch):
        clone_dir = Path("c:/clone")
        self._rows(monkeypatch, [(222, 'chrome.exe --user-data-dir="c:\\clone" --remote-debugging-port=6001')])
        assert cdp._skill._find_clone_chrome_port(clone_dir) == 6001

    def test_no_match_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        clone_dir = Path("c:/clone")
        self._rows(monkeypatch, [(333, "chrome.exe --user-data-dir=c:\\other --remote-debugging-port=55873")])
        assert cdp._skill._find_clone_chrome_port(clone_dir) is None

    def test_skips_non_match_continue(self, monkeypatch: pytest.MonkeyPatch):
        clone_dir = Path("c:/clone")
        self._rows(monkeypatch, [
            (444, "chrome.exe --type=crashpad-handler --user-data-dir=c:\\clone"),
            (111, "chrome.exe --user-data-dir=c:\\clone --remote-debugging-port=55873"),
        ])
        assert cdp._skill._find_clone_chrome_port(clone_dir) == 55873

    def test_portless_rows_only(self, monkeypatch: pytest.MonkeyPatch):
        clone_dir = Path("c:/clone")
        self._rows(monkeypatch, [(555, "chrome.exe --user-data-dir=c:\\clone")])
        assert cdp._skill._find_clone_chrome_port(clone_dir) is None

    def test_empty_rows(self, monkeypatch: pytest.MonkeyPatch):
        self._rows(monkeypatch, [])
        assert cdp._skill._find_clone_chrome_port(Path("c:/clone")) is None


# ---------------------------------------------------------------------------
# M · _rewrite_devtools_port_file
# ---------------------------------------------------------------------------


class TestRewriteDevtoolsPortFile:
    def test_success_two_lines(self, tmp_path: Path):
        ok = cdp._skill._rewrite_devtools_port_file(tmp_path, "ws://127.0.0.1:55873/devtools/browser/abc")
        assert ok is True
        content = (tmp_path / "DevToolsActivePort").read_text(encoding="utf-8")
        assert content == "55873\n/devtools/browser/abc"

    def test_bad_endpoint_false(self, tmp_path: Path):
        ok = cdp._skill._rewrite_devtools_port_file(tmp_path, "ws://bad-endpoint")
        assert ok is False
        assert not (tmp_path / "DevToolsActivePort").exists()

    def test_no_path_only_port(self, tmp_path: Path):
        ok = cdp._skill._rewrite_devtools_port_file(tmp_path, "ws://127.0.0.1:55873")
        assert ok is True
        content = (tmp_path / "DevToolsActivePort").read_text(encoding="utf-8")
        assert content == "55873"

    def test_unwritable_returns_false(self, tmp_path: Path):
        blocker = tmp_path / "DevToolsActivePort"
        blocker.write_text("x", encoding="utf-8")
        blocker.chmod(0o444)
        try:
            ok = cdp._skill._rewrite_devtools_port_file(tmp_path, "ws://127.0.0.1:55873/devtools/browser/abc")
        finally:
            blocker.chmod(0o666)
        assert ok is False


# ---------------------------------------------------------------------------
# N · _kill_clone_chrome（2026-09-10 微修回归：taskkill 抛仍计数）
# ---------------------------------------------------------------------------


class TestKillCloneChrome:
    def test_kills_matching_pids(self, monkeypatch: pytest.MonkeyPatch):
        rows = [
            (111, "chrome.exe --user-data-dir=c:\\clone --remote-debugging-port=55873"),
            (222, "chrome.exe --user-data-dir=c:\\other"),
            (333, 'chrome.exe --user-data-dir="c:\\clone" --remote-debugging-port=6001'),
        ]
        monkeypatch.setattr(cdp._skill, "_get_chrome_cmdlines", lambda: rows)
        calls: list = []

        def fake_run(cmd, *a, **k):
            cmd_s = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            calls.append(cmd_s)
            return MagicMock()

        monkeypatch.setattr(cdp._skill, "subprocess", SimpleNamespace(run=fake_run))

        killed = cdp._skill._kill_clone_chrome(Path("c:/clone"))

        assert killed == 2
        joined = " ".join(calls)
        assert "111" in joined and "333" in joined and "222" not in joined

    def test_no_match_zero(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(cdp._skill, "_get_chrome_cmdlines",
                            lambda: [(222, "chrome.exe --user-data-dir=c:\\other")])
        assert cdp._skill._kill_clone_chrome(Path("c:/clone")) == 0

    def test_taskkill_failure_still_counts(self, monkeypatch: pytest.MonkeyPatch):
        """② 微修回归：per-pid taskkill 抛异常仍计数（外层 try 兜底）。"""
        monkeypatch.setattr(cdp._skill, "_get_chrome_cmdlines",
                            lambda: [(111, "chrome.exe --user-data-dir=c:\\clone --remote-debugging-port=55873")])

        def fake_run(cmd, *a, **k):
            raise RuntimeError("taskkill fail")

        monkeypatch.setattr(cdp._skill, "subprocess", SimpleNamespace(run=fake_run))
        assert cdp._skill._kill_clone_chrome(Path("c:/clone")) == 1

