"""回归测试：save_summary_only folder 为空时自动走统一路由器（L8 修复 2026-09-03）。

保护的需求（背景：批量总结曾有 78 篇因调用方漏传 folder 全部落进【待归类】）：
- save_summary_only 未传 folder 时，按 author/url 经 shared.routing.resolve_folder 自动归档；
- 显式传了 folder 的调用方（monitors 管线 / land_scys_batch 等）行为不变；
- author 会补进 tags（与 skill_main L7 手贴路径一致）。

2026-09-05 增补（旁路入口收编）：save_summarized_from_file / skill_continue_summary /
async_save_summarized_from_file / articles/_save_summary.py CLI 这四个旁路入口同样
folder 为空时自动路由，堵住 78 篇事故的同类通道。
"""
import asyncio
import sys

import pytest

import articles.main as am
from shared.routing import resolve_folder

# 落盘门禁「内容缺失」下限 300 字（DECISION-20260915 content-first）：
# 用短占位串（如「笔记内容」）会让所有走 save_summary_only 的用例被拦，
# 故统一用足够长的正文占位；其具体内容与本文件各用例的断言目标无关。
NOTE_BODY = "笔记内容占位，用于满足内容缺失下限。" * 30



@pytest.fixture
def capture_save(monkeypatch):
    """拦截真实落盘与去重，捕获 save_summarized_article 收到的 folder/tags。"""
    captured = {}

    def _fake_save(*args, **kwargs):
        folder = kwargs.get("folder", "")
        title = kwargs.get("original_title", "")
        captured.update(folder=folder, tags=list(kwargs.get("tags") or []),
                        author=kwargs.get("author", ""))
        return (f"note::{title}", f"{folder}/{title}.md")

    monkeypatch.setattr(am, "save_summarized_article", _fake_save)
    monkeypatch.setattr(am.dedup, "is_summarized", lambda **kw: {})
    monkeypatch.setattr(am.dedup, "mark_summarized", lambda **kw: None)
    return captured


def test_no_folder_routes_by_author(capture_save):
    res = am.save_summary_only({
        "summarized_content": NOTE_BODY,
        "original_url": "https://www.bilibili.com/video/BV1xx",
        "author": "趋势浪子",
        "original_title": "测试笔记",
    })
    assert res["success"] is True
    assert capture_save["folder"] == "【我的总结】/作者/趋势浪子"
    assert "趋势浪子" in capture_save["tags"]


def test_explicit_folder_unchanged(capture_save):
    res = am.save_summary_only({
        "summarized_content": NOTE_BODY,
        "original_url": "https://www.bilibili.com/video/BV1xx",
        "author": "趋势浪子",
        "original_title": "测试笔记",
        "folder": "自定义/目录",
    })
    assert res["success"] is True
    assert capture_save["folder"] == "自定义/目录"


def test_no_author_falls_to_inbox(capture_save):
    res = am.save_summary_only({
        "summarized_content": NOTE_BODY,
        "original_url": "",
        "original_title": "无主笔记",
    })
    assert res["success"] is True
    # 与直接调 resolve_folder（无作者、无分类）的结果一致 = 兜底收件箱
    assert capture_save["folder"] == resolve_folder({"author": "", "url": "", "title": "无主笔记"})


# ---------------------------------------------------------------------------
# 旁路入口收编（2026-09-05）：四个漏传 folder 会落【待归类】的通道
# ---------------------------------------------------------------------------

def _tmp_summary_file(tmp_path) -> str:
    p = tmp_path / "summary.md"
    p.write_text(NOTE_BODY, encoding="utf-8")
    return str(p)


def test_autoroute_folder_empty_routes_and_appends_author():
    folder, tags = am.autoroute_folder(
        "", "趋势浪子", "https://www.bilibili.com/video/BV1xx", "测试笔记", ["既有"])
    assert folder == "【我的总结】/作者/趋势浪子"
    assert "趋势浪子" in tags
    assert "既有" in tags


def test_autoroute_folder_explicit_unchanged():
    folder, tags = am.autoroute_folder("自定义/目录", "趋势浪子", "", "测试笔记", [])
    assert folder == "自定义/目录"
    assert tags == []


def test_autoroute_folder_author_not_duplicated():
    _, tags = am.autoroute_folder("", "趋势浪子", "", "测试笔记", ["趋势浪子"])
    assert tags.count("趋势浪子") == 1


def test_from_file_routes_by_author(capture_save, tmp_path):
    am.save_summarized_from_file(
        _tmp_summary_file(tmp_path), original_url="https://www.bilibili.com/video/BV1xx",
        author="趋势浪子", original_title="测试笔记")
    assert capture_save["folder"] == "【我的总结】/作者/趋势浪子"
    assert "趋势浪子" in capture_save["tags"]


def test_from_file_explicit_folder_unchanged(capture_save, tmp_path):
    am.save_summarized_from_file(
        _tmp_summary_file(tmp_path), author="趋势浪子", original_title="测试笔记",
        folder="自定义/目录")
    assert capture_save["folder"] == "自定义/目录"


def test_from_file_missing_file_raises(capture_save, tmp_path):
    with pytest.raises(FileNotFoundError):
        am.save_summarized_from_file(str(tmp_path / "nope.md"))


def test_continue_summary_routes_by_author(capture_save):
    res = am.skill_continue_summary(
        "原文内容", NOTE_BODY,
        original_url="https://www.bilibili.com/video/BV1xx",
        author="趋势浪子", original_title="测试笔记")
    assert res["success"] is True
    assert capture_save["folder"] == "【我的总结】/作者/趋势浪子"


def test_continue_summary_explicit_folder_unchanged(capture_save):
    res = am.skill_continue_summary(
        "原文内容", NOTE_BODY, author="趋势浪子", original_title="测试笔记",
        folder="自定义/目录")
    assert res["success"] is True
    assert capture_save["folder"] == "自定义/目录"


def test_continue_summary_empty_content_fails(capture_save):
    res = am.skill_continue_summary("原文内容", "   ")
    assert res["success"] is False


def test_async_from_file_routes_by_author(capture_save, tmp_path):
    res = asyncio.run(am.async_save_summarized_from_file(
        _tmp_summary_file(tmp_path), original_url="https://www.bilibili.com/video/BV1xx",
        author="趋势浪子", original_title="测试笔记"))
    assert res[1] == "【我的总结】/作者/趋势浪子/测试笔记.md"


def test_async_from_file_explicit_folder_unchanged(capture_save, tmp_path):
    res = asyncio.run(am.async_save_summarized_from_file(
        _tmp_summary_file(tmp_path), author="趋势浪子", original_title="测试笔记",
        folder="自定义/目录"))
    assert res[1] == "自定义/目录/测试笔记.md"


def test_async_from_file_missing_file_raises(capture_save, tmp_path):
    with pytest.raises(FileNotFoundError):
        asyncio.run(am.async_save_summarized_from_file(str(tmp_path / "nope.md")))


def test_cli_routes_by_author(capture_save, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "_save_summary.py", "--direct", NOTE_BODY,
        "--url", "https://www.bilibili.com/video/BV1xx",
        "--author", "趋势浪子", "--title", "测试笔记"])
    from articles import _save_summary as cli
    assert cli.main() == 0
    assert capture_save["folder"] == "【我的总结】/作者/趋势浪子"


def test_cli_explicit_folder_unchanged(capture_save, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "_save_summary.py", "--direct", NOTE_BODY,
        "--author", "趋势浪子", "--title", "测试笔记", "--folder", "自定义/目录"])
    from articles import _save_summary as cli
    assert cli.main() == 0
    assert capture_save["folder"] == "自定义/目录"
