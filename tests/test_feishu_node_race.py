"""飞书容器节点创建并发安全测试（TOCTOU 防护）。

验证 _ensure_child_node / ensure_series_node 在「建失败→重查复用」兜底下，
不会因并发抢建而重复建节点或落空；并验证跨进程文件锁的获取/释放行为。
"""
import os

import pytest

from articles.feishu import FeishuOutput


@pytest.fixture
def fo(monkeypatch):
    f = FeishuOutput()
    monkeypatch.setenv("FEISHU_WIKI_SPACE", "space_test")
    f.wiki_space = "space_test"
    monkeypatch.setattr(f, "is_available", lambda: True)
    FeishuOutput._series_node_cache.clear()
    FeishuOutput._folder_path_cache.clear()
    calls = {"list": [], "create": []}

    def fake_run(args, **kw):
        if args[1] == "+node-list":
            calls["list"].append(args)
            return {"ok": True, "data": {"nodes": []}}
        if args[1] == "+node-create":
            calls["create"].append(args)
            return {"ok": True, "data": {"node_token": "NEW_" + args[args.index("--title") + 1]}}
        return {"ok": False}

    monkeypatch.setattr(f, "_run_cli_command", fake_run)
    f._calls = calls
    return f


def test_find_child_sanitize_match(fo, monkeypatch):
    # node-list 返回 sanitize 后的标题（30％），用原始 30% 应能命中并返回 token
    def fake_list(args, **kw):
        return {"ok": True, "data": {"nodes": [
            {"title": "第44集_阿胶，给个机会，再跌30％吧！", "node_token": "X1"}]}}

    monkeypatch.setattr(fo, "_run_cli_command", fake_list)
    assert fo._find_child_token("PARENT", "第44集_阿胶，给个机会，再跌30%吧！") == "X1"


def test_ensure_child_creates_when_absent(fo):
    tok = fo._ensure_child_node("PARENT", "土斯")
    assert tok == "NEW_土斯"
    assert len(fo._calls["create"]) == 1


def test_ensure_child_reuses_when_present(fo, monkeypatch):
    def fake_list(args, **kw):
        return {"ok": True, "data": {"nodes": [{"title": "土斯", "node_token": "EXIST"}]}}

    monkeypatch.setattr(fo, "_run_cli_command", fake_list)
    assert fo._ensure_child_node("PARENT", "土斯") == "EXIST"
    assert len(fo._calls["create"]) == 0  # 已存在不应再建


def test_ensure_child_race_retry_reuse(fo, monkeypatch):
    # 模拟并发抢建：本进程 create 被飞书拒绝（重名），re-list 查到另一进程已建的节点 → 复用
    state = {"created": False}

    def fake_run(args, **kw):
        if args[1] == "+node-list":
            if not state["created"]:
                return {"ok": True, "data": {"nodes": []}}
            return {"ok": True, "data": {"nodes": [{"title": "土斯", "node_token": "RACED"}]}}
        if args[1] == "+node-create":
            state["created"] = True
            return {"ok": False, "error": {"message": "duplicate title"}}
        return {"ok": False}

    monkeypatch.setattr(fo, "_run_cli_command", fake_run)
    tok = fo._ensure_child_node("PARENT", "土斯")
    assert tok == "RACED"  # 建失败后重查复用：不返回空、不重复建


def test_ensure_series_node_race_retry(fo, monkeypatch):
    state = {"created": False}

    def fake_run(args, **kw):
        if args[1] == "+node-list":
            if not state["created"]:
                return {"ok": True, "data": {"nodes": []}}
            return {"ok": True, "data": {"nodes": [
                {"title": "价值投资，知行合一", "node_token": "SR"}]}}
        if args[1] == "+node-create":
            state["created"] = True
            return {"ok": False, "error": {"message": "duplicate"}}
        return {"ok": False}

    monkeypatch.setattr(fo, "_run_cli_command", fake_run)
    tok = fo.ensure_series_node("价值投资，知行合一", parent_token="PARENT")
    assert tok == "SR"


def test_node_creation_lock_acquire_release(tmp_path, monkeypatch):
    import articles.feishu as ff
    monkeypatch.setattr(ff, "_NODE_LOCK_DIR", str(tmp_path))
    f = FeishuOutput()
    f.wiki_space = "space_test"
    with f._node_creation_lock("P", "土斯") as _:
        keys = list(tmp_path.glob("node_*.lock"))
        assert len(keys) == 1  # 持锁期间锁文件存在
    assert list(tmp_path.glob("node_*.lock")) == []  # 释放后删除
