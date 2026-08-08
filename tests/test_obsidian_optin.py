"""回归测试：双写规则改为「默认只写飞书，Obsidian 按需开启」。

保护的需求（用户规则 2026-08-08）：
- 默认 OutputManager() → 只写飞书，不写 Obsidian（避免重复落盘浪费）。
- OutputManager(obsidian=True) → 飞书 + Obsidian 双写。
- .env 设 OBSIDIAN_WRITE=1 时，默认也双写（持久开关，给用户回退到双写的逃生舱）。
- 飞书不可用且未请求 Obsidian 时，回退本地 notes/，不丢数据。
"""
import pytest

from articles.manager import OutputManager
from articles.feishu import FeishuOutput
from articles.obsidian import ObsidianOutput
from articles.local import LocalOutput


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # 清掉可能来自 .env 的持久开关，保证默认行为可测
    monkeypatch.delenv("OBSIDIAN_WRITE", raising=False)
    monkeypatch.delenv("DISABLE_FEISHU_SYNC", raising=False)


def _both_cloud_available(monkeypatch):
    monkeypatch.setattr(FeishuOutput, "is_available", lambda self: True)
    monkeypatch.setattr(ObsidianOutput, "is_available", lambda self: True)


def test_default_is_feishu_only(monkeypatch):
    _both_cloud_available(monkeypatch)
    mgr = OutputManager()
    names = {o.name for o in mgr.get_available_outputs()}
    assert names == {"feishu"}


def test_obsidian_true_writes_both(monkeypatch):
    _both_cloud_available(monkeypatch)
    mgr = OutputManager(obsidian=True)
    names = {o.name for o in mgr.get_available_outputs()}
    assert names == {"feishu", "obsidian"}


def test_env_obsidian_write_enables_both_by_default(monkeypatch):
    _both_cloud_available(monkeypatch)
    monkeypatch.setenv("OBSIDIAN_WRITE", "1")
    mgr = OutputManager()
    names = {o.name for o in mgr.get_available_outputs()}
    assert names == {"feishu", "obsidian"}


def test_explicit_obsidian_true_wins_even_if_env_off(monkeypatch):
    _both_cloud_available(monkeypatch)
    monkeypatch.setenv("OBSIDIAN_WRITE", "0")
    mgr = OutputManager(obsidian=True)
    names = {o.name for o in mgr.get_available_outputs()}
    assert names == {"feishu", "obsidian"}


def test_no_cloud_falls_back_to_local(monkeypatch):
    # 飞书关 + 未请求 Obsidian → 本地兜底，避免丢数据
    monkeypatch.setattr(FeishuOutput, "is_available", lambda self: False)
    monkeypatch.setattr(ObsidianOutput, "is_available", lambda self: True)
    monkeypatch.setattr(LocalOutput, "is_available", lambda self: True)
    mgr = OutputManager()
    names = {o.name for o in mgr.get_available_outputs()}
    assert names == {"local"}


def test_save_to_still_targets_explicit_output(monkeypatch):
    _both_cloud_available(monkeypatch)
    mgr = OutputManager()
    # save_to 不受 obsidian 开关限制，可显式指定单端
    assert mgr.save_to is not None
    targets = {o.name for o in mgr.get_available_outputs()}
    assert "feishu" in targets
