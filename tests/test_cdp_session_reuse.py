"""SharedCdpSession 健康复用路径 TDD（2026-09-10 优化）。

背景：原 __init__ 无条件「杀光所有 Chrome → 全量复制 → 重启克隆浏览器」，
双 Agent 并发时互杀对方调试 Chrome（connect ECONNREFUSED），叠加 Playwright
失败泄漏后一次被杀即整批全废。优化以 cdp-automation-profile 的 3 天 clone
marker 机制为准：

  1. 克隆调试 Chrome 健康（DevToolsActivePort 端口可探测）→ 直接 connect_over_cdp
     复用（_own_browser=False，close 只断开不杀），跳过杀 Chrome / 复制 / 重启。
  2. 重建路径条件化杀：仅 clone 陈旧/缺失（要全量复制、需释放源 cookie 独占锁）
     时才关 Chrome；clone 新鲜时重启克隆浏览器用不到源锁，不杀日常 Chrome。
  3. 复用探测失败时按旧端口精准清僵尸（不误伤日常 Chrome）。
  4. 登录墙兜底 restart_fresh()：复用会话撞登录墙标记时重建全新会话。
"""
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from shared import cdp_session as cdp
import profile_clone_fetch as pcf


# ── 公共 mock 基建 ────────────────────────────────────────────

def _fake_playwright(monkeypatch):
    """替换 playwright.sync_api.sync_playwright，避免测试真启 driver。返回 mock p。"""
    from playwright import sync_api
    p = MagicMock()
    holder = MagicMock()
    holder.start.return_value = p
    monkeypatch.setattr(sync_api, "sync_playwright", lambda: holder)
    return p


def _sentinel_raise(name):
    def _boom(*args, **kwargs):
        raise AssertionError(f"不应被调用：{name}")
    return _boom


def _make_clone(tmp_path: Path, marker: str | None = "2026-09-10",
                with_cookies: bool = True) -> Path:
    """在 tmp_path 造一个克隆目录形态（cookie db + marker）。"""
    if with_cookies:
        cookie = tmp_path / "Default" / "Network"
        cookie.mkdir(parents=True, exist_ok=True)
        (cookie / "Cookies").write_text("db", encoding="utf-8")
    if marker is not None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / pcf.MARKER_DATE).write_text(marker, encoding="utf-8")
    return tmp_path


def _reused_session(endpoint="ws://127.0.0.1:59222/devtools/browser/abc"):
    """绕过 __init__ 直接构造「复用态」会话（_own_browser=False）。"""
    s = cdp.SharedCdpSession.__new__(cdp.SharedCdpSession)
    s._p = MagicMock()
    s._own_browser = False
    s._ctx = MagicMock()
    s._page = MagicMock()
    s._browser = MagicMock()
    s._proc = None
    s.cdp_endpoint = endpoint
    s._headless = True
    s._live = True
    s._use_cdp = True
    return s


# ── A. _read_devtools_port ────────────────────────────────────

class TestReadDevtoolsPort:
    def test_reads_port_from_first_line(self, tmp_path):
        (tmp_path / "DevToolsActivePort").write_text("59222\n/devtools/browser/abc", encoding="utf-8")
        assert cdp._read_devtools_port(tmp_path) == 59222

    def test_missing_file_returns_none(self, tmp_path):
        assert cdp._read_devtools_port(tmp_path) is None

    def test_invalid_content_returns_none(self, tmp_path):
        (tmp_path / "DevToolsActivePort").write_text("not-a-port", encoding="utf-8")
        assert cdp._read_devtools_port(tmp_path) is None

    def test_empty_file_returns_none(self, tmp_path):
        (tmp_path / "DevToolsActivePort").write_text("", encoding="utf-8")
        assert cdp._read_devtools_port(tmp_path) is None


# ── B. _probe_reuse_endpoint（复用探测：快速失败、无假地址兜底） ──

class TestProbeReuseEndpoint:
    def test_returns_ws_on_first_success(self, monkeypatch):
        import login_cdp_fetch
        monkeypatch.setattr(login_cdp_fetch, "probe_chrome_devtools",
                            lambda port, timeout=2.0: {"webSocketDebuggerUrl": "ws://x/devtools/browser/1"})
        monkeypatch.setattr(cdp.time, "sleep", lambda *_: None)
        assert cdp._probe_reuse_endpoint(9222) == "ws://x/devtools/browser/1"

    def test_returns_none_when_all_fail(self, monkeypatch):
        import login_cdp_fetch
        monkeypatch.setattr(login_cdp_fetch, "probe_chrome_devtools", lambda port, timeout=2.0: None)
        monkeypatch.setattr(cdp.time, "sleep", lambda *_: None)
        assert cdp._probe_reuse_endpoint(9222) is None

    def test_returns_none_when_probe_raises(self, monkeypatch):
        import login_cdp_fetch
        def _boom(port, timeout=2.0):
            raise RuntimeError("net down")
        monkeypatch.setattr(login_cdp_fetch, "probe_chrome_devtools", _boom)
        monkeypatch.setattr(cdp.time, "sleep", lambda *_: None)
        assert cdp._probe_reuse_endpoint(9222) is None


# ── C. __init__ 健康复用路径 ──────────────────────────────────

class TestInitReuse:
    def test_healthy_browser_is_reused_without_launch(self, monkeypatch):
        _fake_playwright(monkeypatch)
        monkeypatch.setattr(cdp, "_read_devtools_port", lambda d: 59222)
        monkeypatch.setattr(cdp, "_probe_reuse_endpoint",
                            lambda port: "ws://127.0.0.1:59222/devtools/browser/x")
        monkeypatch.setattr(cdp, "_launch_cloned_logged_in_browser", _sentinel_raise("_launch"))
        monkeypatch.setattr(cdp, "_kill_chrome_on_port", _sentinel_raise("_kill_chrome_on_port"))
        s = cdp.SharedCdpSession()
        assert s._own_browser is False
        assert s._proc is None
        assert s.cdp_endpoint == "ws://127.0.0.1:59222/devtools/browser/x"

    def test_reuse_opens_own_page_not_pages0(self, monkeypatch):
        """共享 context 下复用会话必须开自己的新页（pages[0] 可能是别人的页面，close 会误关）。"""
        _fake_playwright(monkeypatch)
        monkeypatch.setattr(cdp, "_read_devtools_port", lambda d: 59222)
        monkeypatch.setattr(cdp, "_probe_reuse_endpoint", lambda port: "ws://x")
        monkeypatch.setattr(cdp, "_launch_cloned_logged_in_browser", _sentinel_raise("_launch"))
        p = _fake_playwright(monkeypatch)
        own_page = MagicMock()
        p.chromium.connect_over_cdp.return_value.contexts[0].new_page.return_value = own_page
        s = cdp.SharedCdpSession()
        assert s._page is own_page

    def test_no_existing_browser_falls_back_to_launch(self, monkeypatch):
        _fake_playwright(monkeypatch)
        monkeypatch.setattr(cdp, "_read_devtools_port", lambda d: None)
        monkeypatch.setattr(cdp, "_launch_cloned_logged_in_browser",
                            lambda p: (MagicMock(), "ws://127.0.0.1:5599", "PROC"))
        killed = []
        monkeypatch.setattr(cdp, "_kill_chrome_on_port", lambda port: killed.append(port))
        s = cdp.SharedCdpSession()
        assert s._own_browser is True
        assert s._proc == "PROC"
        assert s.cdp_endpoint == "ws://127.0.0.1:5599"
        assert killed == []   # 端口未知 → 无从按端口清理

    def test_unhealthy_listener_is_killed_by_port_before_relaunch(self, monkeypatch):
        """探测失败但端口文件在（僵尸/半死实例握着克隆目录锁）→ 按端口精准清理后重建。"""
        _fake_playwright(monkeypatch)
        monkeypatch.setattr(cdp, "_read_devtools_port", lambda d: 59222)
        monkeypatch.setattr(cdp, "_probe_reuse_endpoint", lambda port: None)
        monkeypatch.setattr(cdp, "_launch_cloned_logged_in_browser",
                            lambda p: (MagicMock(), "ws://127.0.0.1:5599", "PROC"))
        killed = []
        monkeypatch.setattr(cdp, "_kill_chrome_on_port", lambda port: killed.append(port))
        s = cdp.SharedCdpSession()
        assert killed == [59222]
        assert s._own_browser is True

    def test_connect_failure_falls_back_to_launch(self, monkeypatch):
        _fake_playwright(monkeypatch)
        p = _fake_playwright(monkeypatch)
        p.chromium.connect_over_cdp.side_effect = RuntimeError("ECONNREFUSED")
        monkeypatch.setattr(cdp, "_read_devtools_port", lambda d: 59222)
        monkeypatch.setattr(cdp, "_probe_reuse_endpoint", lambda port: "ws://x")
        monkeypatch.setattr(cdp, "_launch_cloned_logged_in_browser",
                            lambda p: (MagicMock(), "ws://127.0.0.1:5599", "PROC"))
        killed = []
        monkeypatch.setattr(cdp, "_kill_chrome_on_port", lambda port: killed.append(port))
        s = cdp.SharedCdpSession()
        assert s._own_browser is True
        assert s._proc == "PROC"
        assert killed == [59222]   # connect 失败但实例可能还活着 → 按端口清


# ── D. close() 所有权语义 ─────────────────────────────────────

class TestCloseOwnership:
    def test_reused_session_close_does_not_kill_browser(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cdp.subprocess, "run", lambda *a, **k: calls.append(a))
        s = _reused_session()
        s.close()
        assert calls == []   # 复用共享浏览器：只断开，绝不 taskkill

    def test_owned_session_close_kills_process_tree(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cdp.subprocess, "run", lambda *a, **k: calls.append(a))
        s = _reused_session()
        s._own_browser = True
        s._proc = MagicMock()
        s._proc.poll.return_value = None
        s._proc.pid = 4321
        s.close()
        assert any("taskkill" in " ".join(map(str, a)) and "4321" in " ".join(map(str, a))
                   for a in calls)

    def test_owned_session_close_skips_dead_proc(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cdp.subprocess, "run", lambda *a, **k: calls.append(a))
        s = _reused_session()
        s._own_browser = True
        s._proc = MagicMock()
        s._proc.poll.return_value = 1   # 已退出
        s.close()
        assert not any("taskkill" in " ".join(map(str, a)) for a in calls)


# ── E. _launch_cloned_logged_in_browser 条件化杀 ──────────────

class TestConditionalKill:
    def _patch_launch_env(self, monkeypatch, fresh: bool):
        monkeypatch.setattr(pcf, "clone_is_fresh", lambda: fresh)
        monkeypatch.setattr(pcf, "ensure_profile_clone", lambda src=None: Path("C:/fake/clone"))
        monkeypatch.setattr(cdp, "_find_system_chrome", lambda: "C:/fake/chrome.exe")
        monkeypatch.setattr(cdp.subprocess, "Popen", lambda *a, **k: MagicMock())
        monkeypatch.setattr(cdp, "_probe_endpoint",
                            lambda port, **k: "ws://127.0.0.1:9222/devtools/browser/x")
        return []

    def test_fresh_clone_skips_kill_chrome(self, monkeypatch):
        killed = self._patch_launch_env(monkeypatch, fresh=True)
        monkeypatch.setattr(cdp, "_ensure_chrome_closed", lambda: killed.append("kill"))
        cdp._launch_cloned_logged_in_browser(MagicMock())
        assert killed == []   # clone 新鲜 → 重启克隆浏览器无需释放源 cookie 锁

    def test_stale_clone_kills_chrome_before_copy(self, monkeypatch):
        killed = self._patch_launch_env(monkeypatch, fresh=False)
        monkeypatch.setattr(cdp, "_ensure_chrome_closed", lambda: killed.append("kill"))
        cdp._launch_cloned_logged_in_browser(MagicMock())
        assert killed == ["kill"]   # 要全量复制 → 必须先释放独占锁

    def test_missing_chrome_exe_raises(self, monkeypatch):
        self._patch_launch_env(monkeypatch, fresh=True)
        monkeypatch.setattr(cdp, "_find_system_chrome", lambda: None)
        with pytest.raises(RuntimeError):
            cdp._launch_cloned_logged_in_browser(MagicMock())


# ── F. clone_is_fresh（3 天 marker 判定，与 SKILL 同约定） ────

class TestCloneIsFresh:
    def _patch(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pcf, "CLONE_DIR", tmp_path)
        monkeypatch.delenv("CDP_SYNC_INTERVAL_DAYS", raising=False)

    def test_fresh_marker_today(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        _make_clone(tmp_path, marker=date.today().isoformat())
        assert pcf.clone_is_fresh() is True

    def test_stale_marker_beyond_interval(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        old = (date.today() - timedelta(days=3)).isoformat()
        _make_clone(tmp_path, marker=old)
        assert pcf.clone_is_fresh() is False

    def test_missing_marker_is_stale(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        _make_clone(tmp_path, marker=None)
        assert pcf.clone_is_fresh() is False

    def test_missing_cookie_db_is_stale(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        _make_clone(tmp_path, with_cookies=False)
        assert pcf.clone_is_fresh() is False

    def test_corrupted_marker_is_stale(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        _make_clone(tmp_path, marker="garbage!!")
        assert pcf.clone_is_fresh() is False

    def test_timestamp_marker_accepted(self, monkeypatch, tmp_path):
        """兼容 _touch_marker 旧格式（unix timestamp）：解析为今天 → fresh。"""
        self._patch(monkeypatch, tmp_path)
        import time as _t
        ts = int(_t.mktime(date.today().timetuple()))
        _make_clone(tmp_path, marker=str(ts))
        assert pcf.clone_is_fresh() is True

    def test_interval_env_respected(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        monkeypatch.setenv("CDP_SYNC_INTERVAL_DAYS", "30")
        old = (date.today() - timedelta(days=7)).isoformat()
        _make_clone(tmp_path, marker=old)
        assert pcf.clone_is_fresh() is True


# ── G. _probe_endpoint 的 fallback 开关（修假地址兜底） ───────

class TestProbeEndpointFallback:
    def _mock_dead_probe(self, monkeypatch):
        import login_cdp_fetch
        monkeypatch.setattr(login_cdp_fetch, "probe_chrome_devtools", lambda port, timeout=2.0: None)
        monkeypatch.setattr(cdp.time, "sleep", lambda *_: None)

    def test_default_fallback_returns_synthetic_ws(self, monkeypatch):
        """现有启动路径行为回归：默认失败仍返回拼接假地址（重试耗尽后的兜底）。"""
        self._mock_dead_probe(monkeypatch)
        assert cdp._probe_endpoint(9222) == "ws://127.0.0.1:9222"

    def test_fallback_false_returns_none(self, monkeypatch):
        self._mock_dead_probe(monkeypatch)
        assert cdp._probe_endpoint(9222, fallback=False) is None


# ── H. _kill_chrome_on_port（按端口精准清理） ─────────────────

class TestKillChromeOnPort:
    def _patch_run(self, monkeypatch, netstat_line):
        """按命令分发 mock：netstat 返回指定输出，taskkill 记录调用。返回 calls 列表。"""
        calls = []

        def fake_run(args, **kw):
            cmd = " ".join(map(str, args))
            if cmd.startswith("netstat"):
                return MagicMock(stdout=netstat_line)
            calls.append(args)
            return MagicMock(stdout="")

        monkeypatch.setattr(cdp.subprocess, "run", fake_run)
        return calls

    def test_kills_listener_pid(self, monkeypatch):
        line = "  TCP    127.0.0.1:59222    0.0.0.0:0    LISTENING    4321"
        calls = self._patch_run(monkeypatch, line)
        cdp._kill_chrome_on_port(59222)
        assert any("4321" in " ".join(map(str, a)) for a in calls)

    def test_no_listener_no_kill(self, monkeypatch):
        calls = self._patch_run(monkeypatch, "")
        cdp._kill_chrome_on_port(59222)
        assert calls == []

    def test_netstat_failure_swallowed(self, monkeypatch):
        def fake_run(args, **kw):
            raise RuntimeError("netstat broken")
        monkeypatch.setattr(cdp.subprocess, "run", fake_run)
        cdp._kill_chrome_on_port(59222)   # 不应抛出


# ── I. restart_fresh（登录墙兜底） ────────────────────────────

class TestRestartFresh:
    def test_reused_session_rebuilds_fresh(self, monkeypatch):
        _fake_playwright(monkeypatch)
        s = _reused_session()
        killed = []
        monkeypatch.setattr(cdp, "_kill_chrome_on_port", lambda port: killed.append(port))
        monkeypatch.setattr(cdp, "_launch_cloned_logged_in_browser",
                            lambda p: (MagicMock(), "ws://127.0.0.1:7777", "PROC2"))
        assert s.restart_fresh() is True
        assert killed == [59222]
        assert s._own_browser is True
        assert s.cdp_endpoint == "ws://127.0.0.1:7777"

    def test_owned_session_returns_false(self, monkeypatch):
        _fake_playwright(monkeypatch)
        s = _reused_session()
        s._own_browser = True
        monkeypatch.setattr(cdp, "_launch_cloned_logged_in_browser", _sentinel_raise("_launch"))
        assert s.restart_fresh() is False


# ── J. __init__/from_endpoint 失败回滚（loop 泄漏修复，2026-09-10） ──

class TestInitFailureRollback:
    """__init__ 失败必须回滚已启动的 sync_playwright（stop）。

    真机故障（2026-09-10）：沙箱拦截 Chrome 启动 → 首次构造在重建路径抛异常，
    已 start() 的 playwright 事件循环在本线程永久 running（泄漏）→ 同线程后续
    每次构造都报 "using Playwright Sync API inside the asyncio loop"，
    consume_migrate_queue --fetch 整批 15 篇全灭且 11 条误入 manual。
    """

    def test_launch_failure_stops_playwright_and_reraises(self, monkeypatch):
        p = _fake_playwright(monkeypatch)
        monkeypatch.setattr(cdp, "_read_devtools_port", lambda d: None)

        def _boom(_p):
            raise RuntimeError("chrome launch blocked")

        monkeypatch.setattr(cdp, "_launch_cloned_logged_in_browser", _boom)
        with pytest.raises(RuntimeError, match="chrome launch blocked"):
            cdp.SharedCdpSession()
        p.stop.assert_called_once()

    def test_reuse_connect_and_relaunch_both_fail_stops_playwright(self, monkeypatch):
        p = _fake_playwright(monkeypatch)
        p.chromium.connect_over_cdp.side_effect = RuntimeError("ECONNREFUSED")
        monkeypatch.setattr(cdp, "_read_devtools_port", lambda d: 59222)
        monkeypatch.setattr(cdp, "_probe_reuse_endpoint", lambda port: "ws://x")
        monkeypatch.setattr(cdp, "_kill_chrome_on_port", lambda port: None)

        def _boom(_p):
            raise RuntimeError("relaunch died")

        monkeypatch.setattr(cdp, "_launch_cloned_logged_in_browser", _boom)
        with pytest.raises(RuntimeError, match="relaunch died"):
            cdp.SharedCdpSession()
        p.stop.assert_called_once()

    def test_success_does_not_stop_playwright(self, monkeypatch):
        p = _fake_playwright(monkeypatch)
        monkeypatch.setattr(cdp, "_read_devtools_port", lambda d: None)
        monkeypatch.setattr(cdp, "_launch_cloned_logged_in_browser",
                            lambda _p: (MagicMock(), "ws://127.0.0.1:5599", "PROC"))
        cdp.SharedCdpSession()
        p.stop.assert_not_called()


class TestFromEndpointRollback:
    """from_endpoint 同类泄漏：start() 后 connect 失败也必须回滚。"""

    def test_connect_failure_stops_playwright_and_reraises(self, monkeypatch):
        p = _fake_playwright(monkeypatch)
        p.chromium.connect_over_cdp.side_effect = RuntimeError("bad endpoint")
        with pytest.raises(RuntimeError, match="bad endpoint"):
            cdp.SharedCdpSession.from_endpoint("ws://127.0.0.1:1/devtools/browser/x")
        p.stop.assert_called_once()

    def test_success_keeps_playwright_alive(self, monkeypatch):
        p = _fake_playwright(monkeypatch)
        s = cdp.SharedCdpSession.from_endpoint("ws://127.0.0.1:5599/devtools/browser/x")
        p.stop.assert_not_called()
        assert s._own_browser is False
