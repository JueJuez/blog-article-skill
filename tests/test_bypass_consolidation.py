"""收口测试（2026-09-09）：三个旁路入口统一走 save_summary_only + publish_time 全链路透传。

背景（DECISION-20260905 同族缺口的补完）：
- 闸门覆盖不一致审计发现三个旁路入口各自为政：
  run.py --summarized（缺 publish_time）/ articles/_save_summary.py CLI（缺质量门禁+publish_time）
  / save_summarized_from_file（缺去重+质量门禁+publish_time）。
- 收口后四件事（去重/质量门禁/autoroute/publish_time）只在 save_summary_only 一处维护。
- 发布时间三处表达（文件名日期前缀 / 正文发布时间行 / 新鲜度标签）在 publish_time=0 时全部静默失效。
"""
import asyncio
import sys

import pytest

import articles.main as am

# 落盘门禁「内容缺失」下限 300 字（DECISION-20260915 content-first）：
# 用短占位串（如「笔记内容」）会让所有走 save_summary_only 的用例被拦，
# 故统一用足够长的正文占位；其具体内容与本文件各用例的断言目标无关。
NOTE_BODY = "笔记内容占位，用于满足内容缺失下限。" * 30



@pytest.fixture
def capture_full(monkeypatch):
    """拦截真实落盘与去重，捕获 save_summarized_article 收到的全部 kwargs（含 publish_time）。"""
    captured = {}

    def _fake_save(summarized_content, **kwargs):
        folder = kwargs.get("folder", "")
        title = kwargs.get("original_title", "")
        captured.update(summarized_content=summarized_content, folder=folder,
                        tags=list(kwargs.get("tags") or []),
                        author=kwargs.get("author", ""),
                        publish_time=kwargs.get("publish_time", 0),
                        original_url=kwargs.get("original_url", ""))
        return (f"note::{title}", f"{folder}/{title or '无题'}.md")

    monkeypatch.setattr(am, "save_summarized_article", _fake_save)
    monkeypatch.setattr(am.dedup, "is_summarized", lambda **kw: {})
    monkeypatch.setattr(am.dedup, "mark_summarized", lambda **kw: None)
    return captured


def _tmp_summary_file(tmp_path) -> str:
    p = tmp_path / "summary.md"
    p.write_text(NOTE_BODY, encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# 锚：save_summary_only 是唯一带全套闸门的保存口（publish_time 已支持，收口基准）
# ---------------------------------------------------------------------------

def test_save_summary_only_publish_time_passthrough(capture_full):
    res = am.save_summary_only({
        "summarized_content": NOTE_BODY,
        "original_url": "https://www.bilibili.com/video/BV1xx",
        "author": "趋势浪子", "original_title": "测试笔记",
        "publish_time": 1726000000,
    })
    assert res["success"] is True
    assert capture_full["publish_time"] == 1726000000


def test_save_summary_only_publish_time_default_zero(capture_full):
    res = am.save_summary_only({
        "summarized_content": NOTE_BODY,
        "original_url": "https://www.bilibili.com/video/BV1xx",
        "author": "趋势浪子", "original_title": "测试笔记",
    })
    assert res["success"] is True
    assert capture_full["publish_time"] == 0


# ---------------------------------------------------------------------------
# 1) articles/_save_summary.py CLI：--publish-time 透传 + 去重收口到 save_summary_only
# ---------------------------------------------------------------------------

def _run_cli(monkeypatch, *argv) -> int:
    monkeypatch.setattr(sys, "argv", ["_save_summary.py", *argv])
    from articles import _save_summary as cli
    return cli.main()


def test_cli_publish_time_passthrough(capture_full, monkeypatch):
    rc = _run_cli(monkeypatch, "--direct", NOTE_BODY,
                  "--url", "https://www.bilibili.com/video/BV1xx",
                  "--author", "趋势浪子", "--title", "测试笔记",
                  "--publish-time", "1726000000")
    assert rc == 0
    assert capture_full["publish_time"] == 1726000000


def test_cli_publish_time_default_zero(capture_full, monkeypatch):
    rc = _run_cli(monkeypatch, "--direct", NOTE_BODY,
                  "--url", "https://www.bilibili.com/video/BV1xx",
                  "--author", "趋势浪子", "--title", "测试笔记")
    assert rc == 0
    assert capture_full["publish_time"] == 0


def test_cli_dedup_skips_when_already_summarized(capture_full, monkeypatch):
    monkeypatch.setattr(am.dedup, "is_summarized", lambda **kw: {"filename": "旧笔记.md"})
    rc = _run_cli(monkeypatch, "--direct", NOTE_BODY,
                  "--url", "https://www.bilibili.com/video/BV1xx",
                  "--author", "趋势浪子", "--title", "测试笔记")
    assert rc == 0
    assert capture_full == {}  # 收口后由 save_summary_only 统一闸门拦截，不落盘


def test_cli_force_overrides_dedup(capture_full, monkeypatch):
    monkeypatch.setattr(am.dedup, "is_summarized", lambda **kw: {"filename": "旧笔记.md"})
    rc = _run_cli(monkeypatch, "--direct", NOTE_BODY, "--force",
                  "--url", "https://www.bilibili.com/video/BV1xx",
                  "--author", "趋势浪子", "--title", "测试笔记")
    assert rc == 0
    assert capture_full["summarized_content"] == NOTE_BODY  # force 豁免去重，照常落盘


# ---------------------------------------------------------------------------
# 2) save_summarized_from_file：publish_time 参数 + 去重收口 + tuple 兼容
# ---------------------------------------------------------------------------

def test_from_file_publish_time_passthrough(capture_full, tmp_path):
    am.save_summarized_from_file(
        _tmp_summary_file(tmp_path), original_url="https://www.bilibili.com/video/BV1xx",
        author="趋势浪子", original_title="测试笔记", publish_time=1726000000)
    assert capture_full["publish_time"] == 1726000000


def test_from_file_publish_time_default_zero(capture_full, tmp_path):
    am.save_summarized_from_file(
        _tmp_summary_file(tmp_path), original_url="https://www.bilibili.com/video/BV1xx",
        author="趋势浪子", original_title="测试笔记")
    assert capture_full["publish_time"] == 0


def test_from_file_dedup_skips_when_already_summarized(capture_full, tmp_path, monkeypatch):
    monkeypatch.setattr(am.dedup, "is_summarized", lambda **kw: {"filename": "旧笔记.md"})
    res = am.save_summarized_from_file(
        _tmp_summary_file(tmp_path), original_url="https://www.bilibili.com/video/BV1xx",
        author="趋势浪子", original_title="测试笔记")
    assert capture_full == {}  # 收口后新增统一去重闸门，不落盘
    assert res[1] == "旧笔记.md"  # tuple 兼容：第二元素为已存在文件名


def test_from_file_returns_tuple(capture_full, tmp_path):
    res = am.save_summarized_from_file(
        _tmp_summary_file(tmp_path), original_url="https://www.bilibili.com/video/BV1xx",
        author="趋势浪子", original_title="测试笔记")
    assert isinstance(res, tuple) and len(res) == 2
    assert res[1].endswith("测试笔记.md")


def test_async_from_file_publish_time_passthrough(capture_full, tmp_path):
    res = asyncio.run(am.async_save_summarized_from_file(
        _tmp_summary_file(tmp_path), original_url="https://www.bilibili.com/video/BV1xx",
        author="趋势浪子", original_title="测试笔记", publish_time=1726000000))
    assert res[1] == "【我的总结】/作者/趋势浪子/测试笔记.md"
    assert capture_full["publish_time"] == 1726000000


# ---------------------------------------------------------------------------
# 3) articles/run.py：--publish-time 参数 + 两个场景透传
# ---------------------------------------------------------------------------

def test_runpy_summarized_publish_time_passthrough(capture_full, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "run.py", "--summarized", NOTE_BODY,
        "--url", "https://www.bilibili.com/video/BV1xx",
        "--author", "趋势浪子", "--publish-time", "1726000000"])
    from articles import run as run_mod
    assert run_mod.main() == 0
    assert capture_full["publish_time"] == 1726000000


def test_runpy_file_publish_time_passthrough(capture_full, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "run.py", _tmp_summary_file(tmp_path),
        "--url", "https://www.bilibili.com/video/BV1xx",
        "--author", "趋势浪子", "--publish-time", "1726000000"])
    from articles import run as run_mod
    assert run_mod.main() == 0
    assert capture_full["publish_time"] == 1726000000


def test_runpy_summarized_default_zero(capture_full, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "run.py", "--summarized", NOTE_BODY,
        "--url", "https://www.bilibili.com/video/BV1xx",
        "--author", "趋势浪子"])
    from articles import run as run_mod
    assert run_mod.main() == 0
    assert capture_full["publish_time"] == 0
