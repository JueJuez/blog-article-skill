"""回归测试：save_summary_only folder 为空时自动走统一路由器（L8 修复 2026-09-03）。

保护的需求（背景：批量总结曾有 78 篇因调用方漏传 folder 全部落进【待归类】）：
- save_summary_only 未传 folder 时，按 author/url 经 shared.routing.resolve_folder 自动归档；
- 显式传了 folder 的调用方（monitors 管线 / land_scys_batch / drain_pending 等）行为不变；
- author 会补进 tags（与 skill_main L7 手贴路径一致）。
"""
import pytest

import articles.main as am
from shared.routing import resolve_folder


@pytest.fixture
def capture_save(monkeypatch):
    """拦截真实落盘与去重，捕获 save_summarized_article 收到的 folder/tags。"""
    captured = {}

    def _fake_save(content, original_url="", author="", tags=None, original_title="",
                   meta=None, note_type="", publish_time=0, folder="", obsidian=False,
                   draft_only=False):
        captured.update(folder=folder, tags=list(tags or []), author=author)
        return (f"note::{original_title}", f"{folder}/{original_title}.md")

    monkeypatch.setattr(am, "save_summarized_article", _fake_save)
    monkeypatch.setattr(am.dedup, "is_summarized", lambda **kw: {})
    monkeypatch.setattr(am.dedup, "mark_summarized", lambda **kw: None)
    return captured


def test_no_folder_routes_by_author(capture_save):
    res = am.save_summary_only({
        "summarized_content": "# 笔记\n内容",
        "original_url": "https://www.bilibili.com/video/BV1xx",
        "author": "趋势浪子",
        "original_title": "测试笔记",
    })
    assert res["success"] is True
    assert capture_save["folder"] == "【我的总结】/作者/趋势浪子"
    assert "趋势浪子" in capture_save["tags"]


def test_explicit_folder_unchanged(capture_save):
    res = am.save_summary_only({
        "summarized_content": "# 笔记\n内容",
        "original_url": "https://www.bilibili.com/video/BV1xx",
        "author": "趋势浪子",
        "original_title": "测试笔记",
        "folder": "自定义/目录",
    })
    assert res["success"] is True
    assert capture_save["folder"] == "自定义/目录"


def test_no_author_falls_to_inbox(capture_save):
    res = am.save_summary_only({
        "summarized_content": "# 笔记\n内容",
        "original_url": "",
        "original_title": "无主笔记",
    })
    assert res["success"] is True
    # 与直接调 resolve_folder（无作者、无分类）的结果一致 = 兜底收件箱
    assert capture_save["folder"] == resolve_folder({"author": "", "url": "", "title": "无主笔记"})
