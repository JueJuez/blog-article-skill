# -*- coding: utf-8 -*-
"""分享版 HTML 导出守护测试（2026-10-06）。

锁定三件事：
1. `articles/html_export.py` 渲染契约：callout 转样式块、普通引用块保留、
   ```chart JSON 块转 Chart.js 容器（缺 Chart.js 降级表格）、元数据行进页眉、
   H2 生成目录锚点、**产物自包含**（零外链资源）。
2. 落盘传导：`save_summarized_article(share_html=True)` 落盘后自动在同目录
   生成同名 .html；默认 False 不产生。
3. `save_summary_only` 的 `share_html` 键透传到 save_summarized_article。

运行：
    python tests/test_html_export.py
"""
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from articles import html_export as hx


SAMPLE_MD = """#标签/A #领域/测试

**作者**：张三 | **来源链接**：[原文链接](https://example.com/a)
**发布时间**：2026-10-01 10:00

## 一、核心论点

正文段落，讲一个**机制**。

> [!warning] 风险提示
> 警告内容，**加粗**保留。

> 普通引用块保留原话

```chart
type: bar
title: 近三年营收
labels: ["2023", "2024", "2025"]
series:
  - title: 营收（万元）
    data: [120, 180, 340]
```
"""


@pytest.fixture()
def sample_note(tmp_path):
    p = tmp_path / "20261001_【案例】测试笔记.md"
    p.write_text(SAMPLE_MD, encoding="utf-8")
    return str(p)


class TestRender:
    def test_same_dir_same_stem(self, sample_note):
        out = hx.export_note_html(sample_note)
        assert out == os.path.splitext(sample_note)[0] + ".html"
        assert os.path.isfile(out)

    def test_self_contained(self, sample_note):
        out = hx.render_note_html(sample_note)
        html = open(out, encoding="utf-8").read()
        assert hx.self_contained_violations(html) == []
        assert 'src="http' not in html and 'href="http' not in html.replace(
            '<a href="https://example.com/a"', "")

    def test_metadata_to_header(self, sample_note):
        html = open(hx.render_note_html(sample_note), encoding="utf-8").read()
        assert "张三" in html
        assert "2026-10-01 10:00" in html
        assert 'href="https://example.com/a"' in html
        assert "标签/A" in html
        # 元数据行不得再以原始 md 形式出现在正文里
        assert "**作者**" not in html

    def test_title_strips_date_prefix(self, sample_note):
        html = open(hx.render_note_html(sample_note), encoding="utf-8").read()
        assert "【案例】测试笔记" in html
        assert "<title>【案例】测试笔记</title>" in html

    def test_callout_and_quote(self, sample_note):
        html = open(hx.render_note_html(sample_note), encoding="utf-8").read()
        assert 'class="callout warning"' in html
        assert "风险提示" in html
        assert "<blockquote>" in html  # 普通引用块保留

    def test_toc_anchors(self, sample_note):
        html = open(hx.render_note_html(sample_note), encoding="utf-8").read()
        assert '<h2 id="sec-0">' in html
        assert 'href="#sec-0"' in html

    def test_chart_block_rendered(self, sample_note):
        html = open(hx.render_note_html(sample_note), encoding="utf-8").read()
        assert 'class="chart-block"' in html
        assert "data-chart=" in html
        spec = json.loads(_first_chart_payload(html))
        assert spec["type"] == "bar" and spec["labels"][0] == "2023"
        assert spec["datasets"][0]["label"] == "营收（万元）"
        assert spec["datasets"][0]["data"] == [120.0, 180.0, 340.0]
        assert spec["title"] == "近三年营收"

    def test_chart_multi_series(self, tmp_path):
        p = tmp_path / "n.md"
        p.write_text(
            '## 一、x\n\n```chart\ntype: bar\ntitle: 营收 vs 利润\nlabels: ["2024", "2025"]\n'
            'series:\n  - title: 营收\n    data: [100, 200]\n  - title: 利润\n    data: [10, 40]\n```\n',
            encoding="utf-8")
        html = open(hx.render_note_html(str(p)), encoding="utf-8").read()
        spec = json.loads(_first_chart_payload(html))
        assert [d["label"] for d in spec["datasets"]] == ["营收", "利润"]
        assert spec["datasets"][1]["data"] == [10.0, 40.0]

    def test_chart_bad_json_keeps_code_block(self, tmp_path):
        p = tmp_path / "n.md"
        p.write_text("## 一、x\n\n```chart\n{bad json}\n```\n", encoding="utf-8")
        html = open(hx.render_note_html(str(p)), encoding="utf-8").read()
        assert '<figure class="chart-block"' not in html
        assert "{bad json}" in html

    def test_chart_label_mismatch_keeps_code_block(self, tmp_path):
        p = tmp_path / "n.md"
        p.write_text(
            '## 一、x\n\n```chart\ntype: bar\nlabels: ["a", "b"]\n'
            'series:\n  - title: x\n    data: [1, 2, 3]\n```\n', encoding="utf-8")
        html = open(hx.render_note_html(str(p)), encoding="utf-8").read()
        assert '<figure class="chart-block"' not in html

    def test_chart_fallback_table_without_chartjs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hx, "_chartjs_file", lambda: "")
        p = tmp_path / "n.md"
        p.write_text('## 一、x\n\n```chart\n{"type":"pie","title":"占比","labels":["a","b"],'
                     '"data":[3,7]}\n```\n', encoding="utf-8")
        html = open(hx.render_note_html(str(p)), encoding="utf-8").read()
        assert "chart-fallback" in html and "<table>" in html

    def test_chart_fallback_multi_series_table(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hx, "_chartjs_file", lambda: "")
        p = tmp_path / "n.md"
        p.write_text(
            '## 一、x\n\n```chart\ntype: bar\ntitle: 对比\nlabels: ["2024", "2025"]\n'
            'series:\n  - title: 营收\n    data: [100, 200]\n  - title: 利润\n    data: [10, 40]\n```\n',
            encoding="utf-8")
        html = open(hx.render_note_html(str(p)), encoding="utf-8").read()
        assert "<th>营收</th><th>利润</th>" in html
        assert "<td>40</td>" in html


def _first_chart_payload(html: str) -> str:
    import re
    m = re.search(r"data-chart='([^']+)'", html)
    return m.group(1).replace("&quot;", '"').replace("&#x27;", "'")


class TestShareHtmlPropagation:
    def _save(self, tmp_path, monkeypatch, share_html):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
        monkeypatch.setenv("OBSIDIAN_WRITE", "1")
        monkeypatch.setenv("DISABLE_FEISHU_SYNC", "1")
        (tmp_path / "vault").mkdir()
        from articles.main import save_summarized_article
        body = "## 一、核心论点\n\n" + "内容。" * 200  # ≥300 字过机械门禁
        save_summarized_article(
            body, original_url=f"https://example.com/{share_html}",
            author="测试", original_title="传导测试", obsidian=True,
            share_html=share_html)

    def test_share_html_true_generates(self, tmp_path, monkeypatch):
        self._save(tmp_path, monkeypatch, share_html=True)
        md_files = list((tmp_path / "vault").rglob("*.md"))
        assert md_files, "笔记应已落盘"
        htmls = list((tmp_path / "vault").rglob("*.html"))
        assert htmls, "share_html=True 应生成同目录 HTML"
        assert htmls[0].stem == md_files[0].stem

    def test_default_no_html(self, tmp_path, monkeypatch):
        self._save(tmp_path, monkeypatch, share_html=False)
        assert list((tmp_path / "vault").rglob("*.md"))
        assert not list((tmp_path / "vault").rglob("*.html"))

    def test_save_summary_only_passthrough(self, tmp_path, monkeypatch):
        """save_summary_only 读 input_data['share_html'] 并传给落盘函数。"""
        from articles import main as am
        captured = {}
        orig = am.save_summarized_article

        def spy(*a, **kw):
            captured["share_html"] = kw.get("share_html")
            return orig(*a, **kw)

        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
        monkeypatch.setenv("OBSIDIAN_WRITE", "1")
        monkeypatch.setenv("DISABLE_FEISHU_SYNC", "1")
        (tmp_path / "vault").mkdir()
        monkeypatch.setattr(am, "save_summarized_article", spy)
        am.save_summary_only({
            "summarized_content": "## 一、核心论点\n\n" + "内容。" * 200,
            "original_url": "https://example.com/passthrough",
            "original_title": "透传测试",
            "share_html": True,
        })
        assert captured.get("share_html") is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
