"""飞书写入端 frontmatter 剥离测试（PLAN-20260906 任务3）。

背景：frontmatter 已停产（任务2），但历史登记/草稿里仍存有带 `---` 头的内容，
重写落飞书时 YAML 头会被当正文渲染成大量分隔线。写入端统一剥离：
仅当首行恰为 `---` 且闭合 `---` 出现在扫描窗口内才动作，否则原样返回。
"""
import asyncio
import contextlib

import pytest

from articles.feishu import FeishuOutput, _strip_frontmatter

_FM = "---\nsource_url: https://a.com/x\nnote_type: structured\n---"


class TestStripFrontmatter:
    """_strip_frontmatter 纯函数：正常 / 边界 / 异常。"""

    def test_strips_yaml_block(self):
        content = _FM + "\n\n# 标题\n\n正文"
        assert _strip_frontmatter(content) == "# 标题\n\n正文"

    def test_keeps_plain_content(self):
        content = "# 普通笔记\n\n正文"
        assert _strip_frontmatter(content) == content

    def test_keeps_empty(self):
        assert _strip_frontmatter("") == ""

    def test_keeps_unclosed(self):
        # 只有开 `---` 没有闭合：不是合法 frontmatter，不能吞掉正文
        content = "---\nsource_url: https://a.com/x\n\n# 正文开头"
        assert _strip_frontmatter(content) == content

    def test_keeps_close_beyond_window(self):
        # 闭合 `---` 在扫描窗口（前 20 行）之外：视为正文分隔线，不剥离
        lines = ["---"] + [f"key{i}: v{i}" for i in range(30)] + ["---", "", "# 标题"]
        content = "\n".join(lines)
        assert _strip_frontmatter(content) == content

    def test_keeps_separator_in_body(self):
        # 首行不是 `---`，正文中的 `---` 是 markdown 分隔线，必须保留
        content = "# 标题\n\n---\n\n正文"
        assert _strip_frontmatter(content) == content


@pytest.fixture
def feishu_stub(monkeypatch):
    """把 FeishuOutput 的外部交互全部 stub 掉，只留内容通路供捕获。"""
    captured = {}

    monkeypatch.setattr(FeishuOutput, "is_available", lambda self: True)
    monkeypatch.setattr(FeishuOutput, "ensure_inbox_node", lambda self: "inbox_tok")
    monkeypatch.setattr(FeishuOutput, "_node_creation_lock",
                        lambda self, p, t: contextlib.nullcontext())
    monkeypatch.setattr(FeishuOutput, "_find_child_node", lambda self, p, t: None)
    monkeypatch.setattr(FeishuOutput, "_resolve_parent",
                        lambda self, p: ["--parent-token", p or "inbox_tok"])
    return captured


class TestSaveIntegration:
    """接入点：save / save_async 发给 CLI 的内容已剥离 frontmatter。"""

    def test_save_strips_before_cli(self, feishu_stub, monkeypatch):
        def _fake_cli(self, args, timeout=90, input_text=None, max_retries=4, retry_base=3.0):
            # 只捕获 +create 调用；_verify_node_present 的 list 调用不带 input_text，勿覆盖
            if "+create" in args:
                feishu_stub["input_text"] = input_text
            return {"ok": True, "data": {"document": {"url": "u", "document_id": "n"}}}

        monkeypatch.setattr(FeishuOutput, "_run_cli_command", _fake_cli)
        out = FeishuOutput()
        ok = out.save(_FM + "\n\n# 标题\n\n正文", "x.md", title="T")
        assert ok is True
        assert feishu_stub["input_text"] == "# 标题\n\n正文"

    def test_save_keeps_plain_content(self, feishu_stub, monkeypatch):
        def _fake_cli(self, args, timeout=90, input_text=None, max_retries=4, retry_base=3.0):
            if "+create" in args:
                feishu_stub["input_text"] = input_text
            return {"ok": True, "data": {"document": {"url": "u", "document_id": "n"}}}

        monkeypatch.setattr(FeishuOutput, "_run_cli_command", _fake_cli)
        out = FeishuOutput()
        ok = out.save("# 普通笔记\n\n正文", "x.md", title="T")
        assert ok is True
        assert feishu_stub["input_text"] == "# 普通笔记\n\n正文"

    def test_save_async_strips_before_cli(self, feishu_stub, monkeypatch):
        async def _fake_cli_async(self, args, timeout=90, input_text=None,
                                  max_retries=4, retry_base=3.0):
            feishu_stub["input_text"] = input_text
            return {"ok": True, "data": {"document": {"url": "u", "document_id": "n"}}}

        monkeypatch.setattr(FeishuOutput, "_run_cli_command_async", _fake_cli_async)
        out = FeishuOutput()
        ok = asyncio.run(out.save_async(_FM + "\n\n# 标题\n\n正文", "x.md", title="T"))
        assert ok is True
        assert feishu_stub["input_text"] == "# 标题\n\n正文"
