"""CDP daemon 心跳保活（长抓取不被 idle reaper 误杀）的回归测试。

背景（2026-10-24）：daemon 的 reaper 按 CDP_IDLE_TIMEOUT（默认 300s）判断「没人用」就杀
Chrome，而 last_access 只在 ACQUIRE/RELEASE 刷新。scys 篇间等 15~40s、每批歇 3~8 分钟，
不主动打招呼就会被误杀 → 表现为 "Target page, context or browser has been closed"。
修法两层：① skill 侧会话自动心跳线程；② 本对抗路径的显式 `_touch` / `_sleep_with_touch`。
"""
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(BASE_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(BASE_DIR / "scripts"))

import scys_batch_fetch as sbf  # noqa: E402


class _StubSession:
    def __init__(self, raises: bool = False):
        self.calls = 0
        self.raises = raises

    def keepalive(self):
        self.calls += 1
        if self.raises:
            raise RuntimeError("daemon unreachable")
        return True


class TestTouch:
    def test_no_keepalive_attr_is_noop(self):
        class _Bare:
            pass

        sbf._touch(_Bare())  # 不该抛 AttributeError（注入对象不一定是本类 session）
        sbf._touch(None)

    def test_calls_keepalive_once(self):
        s = _StubSession()
        sbf._touch(s)
        assert s.calls == 1

    def test_swallows_exception(self):
        s = _StubSession(raises=True)
        sbf._touch(s)  # daemon 不在/抖一下不能把抓取流程带崩
        assert s.calls == 1


class TestSleepWithTouch:
    def test_splits_and_touches(self, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(sbf.time, "sleep", lambda sec: slept.append(sec))
        s = _StubSession()
        sbf._sleep_with_touch(s, 75.0)
        assert slept == [30.0, 30.0, 15.0]  # 30s 分段，覆盖 300s idle 阈值
        assert s.calls == 3

    def test_zero_seconds_still_noop(self, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(sbf.time, "sleep", lambda sec: slept.append(sec))
        s = _StubSession()
        sbf._sleep_with_touch(s, 0)
        assert slept == [] and s.calls == 0


class TestSessionContract:
    def test_shared_session_inherits_keepalive(self):
        # 自动心跳在会话构造时启用（daemon 模式），此处只锁「对象必须带这个方法」这一契约，
        # 不实例化（实例化会真连 Chrome）。
        from shared.cdp_session import SharedCdpSession

        assert callable(getattr(SharedCdpSession, "keepalive", None))
