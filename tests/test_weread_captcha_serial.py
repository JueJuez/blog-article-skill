"""tests/test_weread_captcha_serial.py — 连环码熔断判定的滑动窗口逻辑（2026-09-27 修复）。

覆盖：
  - 窗口内连续两次「确定」提交 → 熔断
  - 两次验证间隔 > 窗口（含跨午夜/跨日）→ 不熔断（修复前按"自然日第2次"会误熔断）
  - 旧格式 captcha_events 字符串字段被忽略、不污染判定
"""
import json
import time

import pytest

from monitors import weread as wr


@pytest.fixture(autouse=True)
def _isolated_quota(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "QUOTA_PATH", str(tmp_path / "quota.json"))


def _clock(monkeypatch, start: float):
    state = {"t": start}
    monkeypatch.setattr(wr.time, "time", lambda: state["t"])
    return state


def test_two_within_window_triggers(monkeypatch):
    clk = _clock(monkeypatch, 1_000_000.0)
    assert wr.record_captcha_event() is None        # 第1次
    clk["t"] += 60                                 # 60s 后第2次（窗口内）
    assert wr.record_captcha_event() is not None    # 熔断
    assert wr.risk_blocked_seconds() > 0


def test_two_far_apart_no_trigger(monkeypatch):
    clk = _clock(monkeypatch, 1_000_000.0)
    assert wr.record_captcha_event() is None
    clk["t"] += 6 * 3600                          # 6 小时后（跨午夜常见场景）
    assert wr.record_captcha_event() is None
    assert wr.risk_blocked_seconds() == 0


def test_cross_midnight_long_gap_no_false_trigger(monkeypatch):
    # 26号 23:50 → 27号 05:50，间隔 6h，跨午夜，不应误熔断
    t0 = time.mktime(time.strptime("2026-09-26 23:50:00", "%Y-%m-%d %H:%M:%S"))
    clk = _clock(monkeypatch, t0)
    assert wr.record_captcha_event() is None
    clk["t"] += 6 * 3600
    assert wr.record_captcha_event() is None
    assert wr.risk_blocked_seconds() == 0


def test_old_string_field_ignored(monkeypatch, tmp_path):
    # 旧格式 captcha_events 是 ["17:51","00:04"] 字符串列表，新逻辑应忽略
    p = tmp_path / "quota.json"
    p.write_text(json.dumps({"date": "2026-09-27", "captcha_events": ["17:51", "00:04"],
                             "risk_until": 1}))  # 1=1970，早已过期，不应造成假锁
    monkeypatch.setattr(wr, "QUOTA_PATH", str(p))
    clk = _clock(monkeypatch, 2_000_000.0)
    assert wr.record_captcha_event() is None        # 只有1个新事件（旧串被丢弃）
    assert wr.risk_blocked_seconds() == 0
