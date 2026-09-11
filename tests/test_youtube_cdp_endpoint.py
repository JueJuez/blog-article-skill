# -*- coding: utf-8 -*-
"""PLAN-20260911 S3：YouTube 两条 CDP 消费方迁移 ensure_endpoint 的回归测试。

覆盖（计划 §5 / §6 第一步）：
- cdp_capture.capture_transcript：port=None → 经模块级缓存 _ensure_endpoint() 解析端口；
  显式 port 不触发编排；编排结果跨调用缓存（二次调用不重复编排）。
- videos.fetch.fetch_youtube_transcript_cdp：ensure_endpoint() → capture_transcript(port=ep.port)；
  编排失败回 None；显式 port 直用不编排；抓到空字幕回 None。

S5（2026-09-12 用户二次批准）：videos/cdp_launch.py 已删除，弃用标记测试一并移除；
任何对已删模块的 import 直接 ModuleNotFoundError，护栏由文件缺失天然承担。
"""

from types import SimpleNamespace

import pytest

import videos.cdp_capture as cdp_capture
from videos.fetch import fetch_youtube_transcript_cdp


# ---------------------------------------------------------------------------
# cdp_capture · 端点解析
# ---------------------------------------------------------------------------


class TestCaptureEndpointResolution:
    def test_port_none_resolves_via_ensure_endpoint(self, monkeypatch: pytest.MonkeyPatch):
        """port=None → _ensure_endpoint() 解析端口 → 以该端口拼 base URL。"""
        monkeypatch.setattr(cdp_capture, "_ensure_endpoint",
                            lambda: SimpleNamespace(port=59222))
        seen: list = []
        monkeypatch.setattr(cdp_capture, "hj",
                            lambda url, method="GET": seen.append(url) or (_ for _ in ()).throw(ConnectionError("x")))

        title, text = cdp_capture.capture_transcript("https://www.youtube.com/watch?v=abc")

        assert (title, text) == ("", "")
        assert seen and seen[0].startswith("http://127.0.0.1:59222/json/new?")

    def test_explicit_port_skips_ensure(self, monkeypatch: pytest.MonkeyPatch):
        """显式传 port → 不触发端点编排。"""
        def _boom():
            raise AssertionError("不应调用 _ensure_endpoint")
        monkeypatch.setattr(cdp_capture, "_ensure_endpoint", _boom)
        monkeypatch.setattr(cdp_capture, "hj",
                            lambda url, method="GET": (_ for _ in ()).throw(ConnectionError("x")))

        result = cdp_capture.capture_transcript("https://www.youtube.com/watch?v=abc", port=9333)

        assert result == ("", "")

    def test_ensure_result_cached_across_calls(self, monkeypatch: pytest.MonkeyPatch):
        """模块级缓存：二次调用不重复编排（多消费方同轮共享一次三段式）。"""
        from shared import cdp_session as shared_cdp

        calls: list = []
        result = SimpleNamespace(port=59222, endpoint="ws://127.0.0.1:59222",
                                 action="reused")

        def _fake_ensure(profile_dir=None):
            calls.append(1)
            return result

        monkeypatch.setattr(shared_cdp, "ensure_endpoint", _fake_ensure)
        monkeypatch.setattr(cdp_capture, "_ENDPOINT_CACHE", None, raising=False)

        first = cdp_capture._ensure_endpoint()
        second = cdp_capture._ensure_endpoint()

        assert calls == [1]
        assert first is second


# ---------------------------------------------------------------------------
# videos/fetch.py · fetch_youtube_transcript_cdp
# ---------------------------------------------------------------------------


class TestFetchYoutubeTranscriptCdp:
    def test_ensures_endpoint_then_captures(self, monkeypatch: pytest.MonkeyPatch):
        """port=None → ensure_endpoint() 取端点 → capture_transcript(port=ep.port)。"""
        import videos.cdp_capture as cap
        from shared import cdp_session as shared_cdp

        monkeypatch.setattr(shared_cdp, "ensure_endpoint",
                            lambda profile_dir=None: SimpleNamespace(port=59222))
        seen: dict = {}

        def _fake_capture(url, port=None, wait=45, out=None):
            seen.update(url=url, port=port, wait=wait)
            return ("标题", "字幕正文")

        monkeypatch.setattr(cap, "capture_transcript", _fake_capture)

        result = fetch_youtube_transcript_cdp("https://www.youtube.com/watch?v=abc")

        assert result == ("标题", "字幕正文")
        assert seen == {"url": "https://www.youtube.com/watch?v=abc", "port": 59222, "wait": 45}

    def test_ensure_failure_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        """编排失败（RuntimeError）→ 打印警告并返回 None，不抛异常。"""
        from shared import cdp_session as shared_cdp

        monkeypatch.setattr(shared_cdp, "ensure_endpoint",
                            lambda profile_dir=None: (_ for _ in ()).throw(RuntimeError("Chrome 未就绪")))

        assert fetch_youtube_transcript_cdp("https://www.youtube.com/watch?v=abc") is None

    def test_explicit_port_skips_ensure(self, monkeypatch: pytest.MonkeyPatch):
        """显式 port → 不编排，直接按该端口抓。"""
        import videos.cdp_capture as cap
        from shared import cdp_session as shared_cdp

        def _boom(profile_dir=None):
            raise AssertionError("不应调用 ensure_endpoint")
        monkeypatch.setattr(shared_cdp, "ensure_endpoint", _boom)
        seen: dict = {}
        monkeypatch.setattr(cap, "capture_transcript",
                            lambda url, port=None, wait=45, out=None: seen.update(port=port) or ("T", "x"))

        result = fetch_youtube_transcript_cdp("https://www.youtube.com/watch?v=abc", port=9222)

        assert result == ("T", "x")
        assert seen["port"] == 9222

    def test_capture_empty_text_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        """抓到空字幕（无 CC）→ 返回 None。"""
        import videos.cdp_capture as cap
        from shared import cdp_session as shared_cdp

        monkeypatch.setattr(shared_cdp, "ensure_endpoint",
                            lambda profile_dir=None: SimpleNamespace(port=59222))
        monkeypatch.setattr(cap, "capture_transcript",
                            lambda url, port=None, wait=45, out=None: ("T", ""))

        assert fetch_youtube_transcript_cdp("https://www.youtube.com/watch?v=abc") is None
