# -*- coding: utf-8 -*-
"""微信读书登录判定重构回归：以 wr_rt（刷新令牌）为准，wr_skey 缺失不再误判未登录。

覆盖：
  - has_login_cookie / has_refresh_token 只读 cookie 名
  - try_silent_renew：skey 已存在立即 True；wr_rt 在且续期成功 True；续期失败 False
"""
import sys, time
sys.path.insert(0, '.')

import pytest
from unittest.mock import MagicMock, patch
import monitors.weread as w


class FakeSession:
    """模拟 SharedCdpSession：context.cookies() 返回可变 cookie 列表。"""
    def __init__(self, names):
        self._cookies = [{"name": n, "value": "x", "domain": ".weread.qq.com"} for n in names]
        self.context = MagicMock()
        self.context.cookies = MagicMock(side_effect=lambda url: list(self._cookies))
        self.page = MagicMock()
        self.page.goto = MagicMock()
        self.page.wait_for_timeout = MagicMock()


def test_has_login_cookie_only_when_skey():
    assert w.has_login_cookie(FakeSession(["wr_skey", "wr_rt"])) is True
    assert w.has_login_cookie(FakeSession(["wr_rt"])) is False
    assert w.has_login_cookie(FakeSession([])) is False


def test_has_refresh_token_only_when_wrt():
    assert w.has_refresh_token(FakeSession(["wr_rt"])) is True
    assert w.has_refresh_token(FakeSession(["wr_skey"])) is False   # skey 在但无 rt → 不可自愈
    assert w.has_refresh_token(FakeSession([])) is False


def test_silent_renew_skey_present_shortcuts_true():
    s = FakeSession(["wr_skey", "wr_rt"])
    with patch.object(w, "_find_or_open_weread_page", return_value=s.page):
        assert w.try_silent_renew(s) is True
        s.page.goto.assert_not_called()  # 已有 skey，不应触发导航


def test_silent_renew_success_after_goto():
    # 起始只有 wr_rt，导航后续期出 wr_skey
    s = FakeSession(["wr_rt"])
    def _after_goto(url, **kw):
        s._cookies.append({"name": "wr_skey", "value": "x", "domain": ".weread.qq.com"})
    s.page.goto.side_effect = _after_goto
    with patch.object(w, "_find_or_open_weread_page", return_value=s.page), \
         patch("monitors.weread.time.sleep"):
        assert w.try_silent_renew(s, wait_s=3) is True
        s.page.goto.assert_called_once()


def test_silent_renew_failure_returns_false():
    # 只有 wr_rt 但续期始终不出现 skey
    s = FakeSession(["wr_rt"])
    with patch.object(w, "_find_or_open_weread_page", return_value=s.page), \
         patch("monitors.weread.time.sleep"):
        assert w.try_silent_renew(s, wait_s=2) is False


def test_silent_renew_goto_raises_still_false():
    s = FakeSession(["wr_rt"])
    s.page.goto.side_effect = RuntimeError("nav timeout")
    with patch.object(w, "_find_or_open_weread_page", return_value=s.page), \
         patch("monitors.weread.time.sleep"):
        assert w.try_silent_renew(s, wait_s=2) is False
