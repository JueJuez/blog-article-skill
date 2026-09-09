"""monitors B站 cookie 失效检测接入测试（2026-09-09）。

补齐管线已有 nav 探测 + CDP 轮换（videos/fetch.rotate_bili_cookie_if_dead），
但 monitors 的 B站失效表现为 -101/空数据而非 412，被动钩子不会触发，
故监控轮次开始需主动探测。本文件覆盖三类场景：

- 正常：cookie 有效 → 不轮换不重建 session；失效轮换成功 → _COOKIE/env 刷新、session 强制重建
- 边界：未配置 BILI_COOKIE → 跳过检测（不惊动 Chrome）；无 B站订阅 → 不检测
- 异常：轮换函数抛异常 → 吞掉标 failed，绝不阻断监控轮次
"""
import os

import monitors.bilibili as bili
import monitors.run as run_mod


def _reset_bili_state(monkeypatch, cookie="old-cookie", session=object()):
    """隔离 bilibili 模块状态：_COOKIE/_SESSION/LAST_COOKIE_EVENT/环境变量。"""
    monkeypatch.setattr(bili, "_COOKIE", cookie)
    monkeypatch.setattr(bili, "_SESSION", session)
    monkeypatch.setattr(bili, "LAST_COOKIE_EVENT", "")
    if cookie:
        monkeypatch.setenv("BILI_COOKIE", cookie)
    else:
        monkeypatch.delenv("BILI_COOKIE", raising=False)


def _patch_rotate(monkeypatch, result=None, exc=None):
    """替换 videos.fetch.rotate_bili_cookie_if_dead（被测函数惰性导入，调用时解析）。"""
    calls = []

    def fake(trigger="manual", force=False, interval_days=None):
        calls.append(trigger)
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr("videos.fetch.rotate_bili_cookie_if_dead", fake)
    return calls


# ---------- 正常场景 ----------

def test_refresh_valid_keeps_cookie_and_session(monkeypatch):
    _reset_bili_state(monkeypatch)
    session = object()
    monkeypatch.setattr(bili, "_SESSION", session)
    calls = _patch_rotate(monkeypatch, result=("valid", None))

    evt = bili.refresh_cookie_if_dead()

    assert evt == "valid"
    assert calls == ["monitors"]
    assert bili._COOKIE == "old-cookie"
    assert bili._SESSION is session  # session 未重建
    assert os.environ["BILI_COOKIE"] == "old-cookie"  # 环境未被动


def test_refresh_rotated_updates_cookie_env_and_rebuilds_session(monkeypatch):
    _reset_bili_state(monkeypatch)
    calls = _patch_rotate(monkeypatch, result=("rotated", "fresh-cookie"))

    evt = bili.refresh_cookie_if_dead()

    assert evt == "rotated"
    assert calls == ["monitors"]
    assert bili._COOKIE == "fresh-cookie"
    assert os.environ["BILI_COOKIE"] == "fresh-cookie"  # 子进程继承
    assert bili._SESSION is None  # 标记重建，_get_session() 下次带新 cookie


def test_refresh_failed_keeps_old_cookie(monkeypatch):
    _reset_bili_state(monkeypatch)
    session = object()
    monkeypatch.setattr(bili, "_SESSION", session)
    _patch_rotate(monkeypatch, result=("failed", None))

    evt = bili.refresh_cookie_if_dead()

    assert evt == "failed"
    assert bili._COOKIE == "old-cookie"  # 沿用旧 cookie，不误清
    assert bili._SESSION is session
    assert os.environ["BILI_COOKIE"] == "old-cookie"


# ---------- 边界场景 ----------

def test_refresh_skips_when_cookie_missing(monkeypatch):
    _reset_bili_state(monkeypatch, cookie="")
    # 未配置 cookie 时不得调用轮换（CDP 会 kill 用户 Chrome，游客态用户不该被惊动）
    calls = _patch_rotate(monkeypatch, result=("rotated", "should-not-happen"))

    evt = bili.refresh_cookie_if_dead()

    assert evt == "skipped_no_cookie"
    assert calls == []


def test_health_label_only_abnormal_states(monkeypatch):
    for evt, expect in [("rotated", "cookie已轮换"), ("failed", "cookie检测失败"),
                        ("valid", ""), ("skipped_no_cookie", ""), ("", ""),
                        ("unknown_x", "")]:
        monkeypatch.setattr(bili, "LAST_COOKIE_EVENT", evt)
        assert bili.cookie_health_label() == expect, f"evt={evt}"


# ---------- 异常场景 ----------

def test_refresh_exception_swallowed_and_marked_failed(monkeypatch):
    _reset_bili_state(monkeypatch)
    _patch_rotate(monkeypatch, exc=RuntimeError("nav 网络炸了"))

    evt = bili.refresh_cookie_if_dead()  # 不得抛异常阻断监控轮次

    assert evt == "failed"
    assert bili._COOKIE == "old-cookie"


# ---------- run.py 接线 ----------

def _patch_discover_env(monkeypatch):
    """让 discover_all 走到 B站 循环的最小桩：跳过微信认证、网络发现与节流 sleep。"""
    monkeypatch.setattr(run_mod, "load_weread_auth", lambda: (None, None))
    monkeypatch.setattr(run_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(run_mod.BilibiliSource, "discover",
                        lambda self, state, **kw: [])


def test_discover_all_invokes_cookie_check_before_bili(monkeypatch):
    _patch_discover_env(monkeypatch)
    calls = []
    monkeypatch.setattr(bili, "refresh_cookie_if_dead",
                        lambda: calls.append(1) or "valid")

    run_mod.discover_all({"bilibili": [{"uid": "1"}], "wechat": []}, {}, mode="auto")

    assert calls == [1]  # 有 B站订阅 → 恰好检测一次


def test_discover_all_skips_cookie_check_without_bili_subs(monkeypatch):
    _patch_discover_env(monkeypatch)
    calls = []
    monkeypatch.setattr(bili, "refresh_cookie_if_dead",
                        lambda: calls.append(1) or "valid")

    run_mod.discover_all({"wechat": []}, {}, mode="auto")

    assert calls == []  # 无 B站订阅 → 不检测（省 nav 请求，不打扰）
