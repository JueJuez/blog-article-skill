"""回归测试：公众号 token 过期但 is_token_valid 误判为有效时，discover 持续 401
应触发自动重登并刷新后重试，而非静默全挂。

复现 2026-07-28 的 bug：旧 is_token_valid 探针打 list_articles（过期返回 200 空），
discover 的 resolve_mp 才 401，导致预检失明、公众号长期抓不到且不弹码。

运行：
    python tests/test_wechat_relogin_fallback.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest import mock

import monitors.run as run
import monitors.wechat as wechat


def test_fallback_triggers_relogin():
    """预检失明（is_token_valid 误判有效）→ 全源零结果 + 持续 401 → 自动重登 → 重试成功"""
    counter = {"n": 0}
    m_rel = mock.MagicMock(return_value="fake_qr.png")
    m_wait = mock.MagicMock(return_value=("new_tok", "new_vid"))

    def discover_side_effect(state, first_run_limit=50, mode="auto"):
        counter["n"] += 1
        # 前 3 次（首轮 _discover_wechat_retry 的 3 次重试）持续 401；
        # 第 4 次起（兜底重登后的重试轮）成功返回。
        if counter["n"] <= 3:
            raise RuntimeError("requests.HTTPError: 401 Unauthorized")
        return [{"id": "abc123", "title": "测试文章", "publishTime": 1700000000,
                 "url": "https://mp.weixin.qq.com/s/abc123", "source": "wechat"}]

    with mock.patch.object(run, "trigger_relogin", m_rel), \
         mock.patch.object(run, "_wait_for_token_refresh", m_wait), \
         mock.patch.object(wechat.WereadClient, "is_token_valid", return_value=True), \
         mock.patch.object(wechat.WechatSource, "discover", side_effect=discover_side_effect):
        subs = {"wechat": [{"share_url": "https://mp.weixin.qq.com/s/xyz", "name": "测试号"}]}
        result = run.discover_all(subs, {"sources": {}}, mode="auto")

    # 兜底应触发一次重登
    assert m_rel.call_count == 1, f"预期触发 1 次重登，实际 {m_rel.call_count}"
    # 重登后重试应成功取到 1 篇
    assert len(result) == 1, f"预期取到 1 篇，实际 {len(result)}"
    assert result[0]["id"] == "abc123"
    print("[PASS] test_fallback_triggers_relogin")


def test_valid_token_no_relogin():
    """token 确实有效 → 不触发重登，直接取到文章"""
    m_rel = mock.MagicMock(return_value="fake_qr.png")
    m_wait = mock.MagicMock(return_value=("x", "y"))

    def discover_ok(state, first_run_limit=50, mode="auto"):
        return [{"id": "ok1", "title": "t", "publishTime": 1, "url": "u", "source": "wechat"}]

    with mock.patch.object(run, "trigger_relogin", m_rel), \
         mock.patch.object(run, "_wait_for_token_refresh", m_wait), \
         mock.patch.object(wechat.WereadClient, "is_token_valid", return_value=True), \
         mock.patch.object(wechat.WechatSource, "discover", side_effect=discover_ok):
        subs = {"wechat": [{"share_url": "u", "name": "n"}]}
        result = run.discover_all(subs, {"sources": {}}, mode="auto")

    assert m_rel.call_count == 0, "有效 token 不应触发重登"
    assert len(result) == 1
    print("[PASS] test_valid_token_no_relogin")


def test_transient_401_no_relogin():
    """单次瞬错 401 但重试成功 → 不触发重登（区分代理抖动 vs token 过期）"""
    counter = {"n": 0}
    m_rel = mock.MagicMock(return_value="fake_qr.png")
    m_wait = mock.MagicMock(return_value=("x", "y"))

    def discover_flaky(state, first_run_limit=50, mode="auto"):
        counter["n"] += 1
        if counter["n"] == 1:
            raise RuntimeError("401 transient")
        return [{"id": "t1", "title": "t", "publishTime": 1, "url": "u", "source": "wechat"}]

    with mock.patch.object(run, "trigger_relogin", m_rel), \
         mock.patch.object(run, "_wait_for_token_refresh", m_wait), \
         mock.patch.object(wechat.WereadClient, "is_token_valid", return_value=True), \
         mock.patch.object(wechat.WechatSource, "discover", side_effect=discover_flaky):
        subs = {"wechat": [{"share_url": "u", "name": "n"}]}
        result = run.discover_all(subs, {"sources": {}}, mode="auto")

    assert m_rel.call_count == 0, "瞬错 401 不应触发重登"
    assert len(result) == 1
    print("[PASS] test_transient_401_no_relogin")


def test_preckeck_invalid_triggers_relogin():
    """预检正确判失效（is_token_valid=False）→ 走原重登路径，刷新后成功取到文章"""
    m_rel = mock.MagicMock(return_value="fake_qr.png")
    m_wait = mock.MagicMock(return_value=("new_tok", "new_vid"))

    def discover_ok(state, first_run_limit=50, mode="auto"):
        return [{"id": "v1", "title": "t", "publishTime": 1, "url": "u", "source": "wechat"}]

    with mock.patch.object(run, "trigger_relogin", m_rel), \
         mock.patch.object(run, "_wait_for_token_refresh", m_wait), \
         mock.patch.object(wechat.WereadClient, "is_token_valid", return_value=False), \
         mock.patch.object(wechat.WechatSource, "discover", side_effect=discover_ok):
        subs = {"wechat": [{"share_url": "u", "name": "n"}]}
        result = run.discover_all(subs, {"sources": {}}, mode="auto")

    assert m_rel.call_count == 1, "预检失效应触发 1 次重登"
    assert len(result) == 1, "重登刷新后应成功取到 1 篇"
    print("[PASS] test_preckeck_invalid_triggers_relogin")


if __name__ == "__main__":
    test_fallback_triggers_relogin()
    test_valid_token_no_relogin()
    test_transient_401_no_relogin()
    test_preckeck_invalid_triggers_relogin()
    print("\n✅ 全部回归测试通过")
