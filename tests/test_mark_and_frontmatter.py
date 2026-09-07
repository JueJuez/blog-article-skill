"""任务2（PLAN-20260906 §4）：mark 收敛 + YAML frontmatter 停产。

- mark 收敛：save_summarized_article 正常分支统一登记（save_all 后），直接调用即写
  登记表（url 键优先，无 url 时按 content_key 写内容键，两者皆无不登记）；
  summarize_and_save / save_summary_only 两个调用方的自行 mark 删除。
- frontmatter 停产（DECISION-20260906 延后项，2026-09-07 实施）：_yaml_frontmatter 删除，
  笔记不再携带 tokens/model/source_url/note_type 元信息头；
  正文元信息行（#标签 / **作者** / **来源链接**）保留。
"""
import inspect
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

import articles.main as am
import articles.dedup as dedup
from articles.base import BaseOutput


class StubOutput(BaseOutput):
    """内存收集式输出，隔离真实落盘。"""

    name = "stub"

    def __init__(self):
        super().__init__("stub")
        self.saved = []

    def is_available(self):
        return True

    def get_output_path(self, filename: str) -> str:
        return os.path.join("stub", filename)

    def save(self, content: str, filename: str, title: str = "") -> bool:
        self.saved.append((filename, content))
        return True


@pytest.fixture
def stub_output(monkeypatch):
    out = StubOutput()
    monkeypatch.setattr(am.OutputManager, "get_available_outputs", lambda self: [out])
    return out


def _fake_ai_result() -> dict:
    return {"summary": "这是AI生成的总结内容。",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model": "mock-model", "source": "mock"}


def _ledger() -> dict:
    """直接读（conftest autouse 已隔离到 tmp 的）登记表文件。"""
    with open(dedup._INDEX_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# save_summarized_article 统一 mark
# ---------------------------------------------------------------------------

class TestSaveMarksLedger:
    """save_summarized_article 直接调用即登记：url 键 / 内容键 / 皆无不标 / draft-only 仍标。"""

    def test_url_key_marked(self, stub_output):
        url = "https://example.com/post-1"
        formatted, filename = am.save_summarized_article(
            "总结内容。" * 100, original_url=url, original_title="测试标题")
        rec = dedup.is_summarized(url=url)
        assert rec
        assert rec["filename"] == filename
        assert rec["source_url"] == url
        assert rec["title"] == "测试标题"
        # 任务1 schema：link 字段默认空串
        assert rec["feishu_link"] == ""
        assert rec["obsidian_link"] == ""

    def test_content_key_marked_when_no_url(self, stub_output):
        content = "这是没有来源链接的粘贴原文。" * 60
        formatted, filename = am.save_summarized_article(
            "已总结成笔记。", content_key=content, original_title="内容键标题")
        rec = dedup.is_summarized(content=content)
        assert rec
        assert rec["filename"] == filename
        assert rec["source_url"] == ""

    def test_no_key_not_marked(self, stub_output, monkeypatch):
        calls = []
        monkeypatch.setattr(dedup, "mark_summarized", lambda **kw: calls.append(kw))
        am.save_summarized_article("无来源的总结。", original_title="无键标题")
        assert calls == []

    def test_draft_only_still_marks(self, stub_output, monkeypatch, tmp_path):
        monkeypatch.setattr(am, "_get_draft_dir", lambda: str(tmp_path / "drafts"))
        url = "https://example.com/draft-1"
        formatted, dp = am.save_summarized_article(
            "草稿总结。", original_url=url, original_title="草稿标题", draft_only=True)
        assert dp.endswith(".md")
        assert stub_output.saved == []  # draft-only 不落正式端
        rec = dedup.is_summarized(url=url)
        assert rec


# ---------------------------------------------------------------------------
# 调用方 mark 收敛
# ---------------------------------------------------------------------------

class TestMarkConvergence:
    """登记只发生在 save_summarized_article 内：调用方不再各自 mark。"""

    def test_summarize_and_save_marks_url_once(self, stub_output, monkeypatch):
        url = "https://example.com/conv-1"
        monkeypatch.setattr(am, "fetch_web_content",
                            lambda u: ("抓取标题", "抓取到的正文内容。" * 200, 0))
        monkeypatch.setattr(am, "summarize_content", lambda *a, **kw: _fake_ai_result())
        summarized, formatted, filename, title, err = am.summarize_and_save(url, force=True)
        assert err is None
        rec = dedup.get_entry(url=url)
        assert rec
        assert rec["filename"] == filename
        assert len(_ledger()) == 1

    def test_paste_marks_content_key(self, stub_output, monkeypatch):
        content = "粘贴的原文正文内容，用于内容键去重。" * 100
        monkeypatch.setattr(am, "summarize_content", lambda *a, **kw: _fake_ai_result())
        summarized, formatted, filename, title, err = am.summarize_and_save(content, force=True)
        assert err is None
        rec = dedup.get_entry(content=content)
        assert rec
        assert rec["filename"] == filename
        assert len(_ledger()) == 1

    def test_save_summary_only_marks_once(self, stub_output):
        url = "https://example.com/conv-2"
        summary = "这是足够长的总结正文内容，用于通过质量门禁。" * 40
        res = am.save_summary_only({"summarized_content": summary, "original_url": url,
                                    "original_title": "机械门禁测试标题", "folder": "测试/单元"})
        assert res["success"] is True, res.get("message")
        rec = dedup.get_entry(url=url)
        assert rec
        assert rec["filename"] == res["filename"]
        assert len(_ledger()) == 1

    def test_callers_no_longer_mark(self):
        """mark 收敛：summarize_and_save / save_summary_only 源码不再自带 mark 调用。"""
        assert "mark_summarized" not in inspect.getsource(am.summarize_and_save)
        assert "mark_summarized" not in inspect.getsource(am.save_summary_only)


# ---------------------------------------------------------------------------
# frontmatter 停产
# ---------------------------------------------------------------------------

class TestFrontmatterStopped:
    """frontmatter 停产（DECISION-20260906 延后项）：检索改走登记表。"""

    def test_no_frontmatter_emitted(self, stub_output):
        formatted, filename = am.save_summarized_article(
            "总结内容。", original_url="https://example.com/fm-1",
            original_title="元信息标题",
            meta={"usage": {"prompt_tokens": 1, "completion_tokens": 2},
                  "model": "mock-model"},
            note_type="opinion", publish_time=1727000000)
        assert not formatted.startswith("---")
        assert "source_url:" not in formatted
        assert "note_type:" not in formatted
        assert "tokens:" not in formatted
        assert "mock-model" not in formatted

    def test_yaml_frontmatter_helper_removed(self):
        assert not hasattr(am, "_yaml_frontmatter")

    def test_body_metadata_kept(self, stub_output):
        """停产的是 YAML 头；正文元信息行（#标签 / **来源链接**）保留。"""
        formatted, _ = am.save_summarized_article(
            "正文内容。", original_url="https://example.com/fm-2",
            original_title="正文元信息标题", author="作者甲", tags=["文章总结"])
        assert "**来源链接**：[原文链接](https://example.com/fm-2)" in formatted
        assert "#文章总结" in formatted
