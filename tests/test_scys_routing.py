"""回归测试：fetch_web_content 对 scys.com 链接自动走 CDP 登录态抓取。

保护的需求（用户 2026-08-20）：
- 用户对模型说「帮我总结这篇 scys 帖子」应与普通文章同一条路径（articles 入口无感分流）。
- scys.com/articleDetail/ 链接 → 自动调 login_cdp_fetch.fetch（需登录态），不 requests。
- 非 scys 域名 → 维持原 _download 逻辑。
- scys 抓取失败（如 Chrome 未开 debug 端口）→ 返回 None 并提示，不抛异常炸管道。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from articles.fetch import fetch_web_content, is_scys_url


class TestIsScysUrl:
    def test_article_detail_url_is_scys(self):
        assert is_scys_url("https://scys.com/articleDetail/xq_topic/22255411815528241")

    def test_plain_scys_domain(self):
        assert is_scys_url("https://scys.com/tags?projectId=2892644")

    def test_other_domain_not_scys(self):
        assert not is_scys_url("https://mp.weixin.qq.com/s/abc123")

    def test_not_a_url(self):
        assert not is_scys_url("这不是链接，是正文文本")


class TestScysRouting:
    def test_scys_url_calls_cdp_fetch(self, monkeypatch, tmp_path):
        calls = []

        def fake_cdp_fetch(url, out_path=None, **kwargs):
            calls.append(url)
            (tmp_path / "fake.md").write_text("# 测试标题\n\n" + "正文" * 200, encoding="utf-8")
            return {"title": "测试标题", "url": url, "chars": 500,
                    "login_wall_hit": [], "output": str(tmp_path / "fake.md")}

        import articles.fetch as fetch_mod
        monkeypatch.setattr(fetch_mod, "_scys_cdp_fetch", fake_cdp_fetch)
        result = fetch_web_content("https://scys.com/articleDetail/xq_topic/123")
        assert calls == ["https://scys.com/articleDetail/xq_topic/123"]
        assert result is not None
        assert result[0] == "测试标题"
        assert "正文" in result[1] or len(result[1]) > 0

    def test_scys_cdp_failure_returns_none(self, monkeypatch, capsys):
        def fake_cdp_fetch(url, out_path=None, **kwargs):
            raise ConnectionError("CDP 不可达：用户主 Chrome 未启 debug 端口")

        import articles.fetch as fetch_mod
        monkeypatch.setattr(fetch_mod, "_scys_cdp_fetch", fake_cdp_fetch)
        result = fetch_web_content("https://scys.com/articleDetail/xq_topic/123")
        assert result is None
        out = capsys.readouterr().out
        assert "CDP" in out or "登录" in out


class TestMixedUrlBatch:
    """混合场景：用户一次提供多篇链接，普通博客 + scys 混合。

    skill_main 为单篇入口（多链接由外层模型逐篇循环调 fetch_web_content），
    每条 URL 独立分流：scys → CDP，普通 → requests。钉死该行为防回归。
    """

    def test_mixed_urls_route_independently(self, monkeypatch, tmp_path):
        import articles.fetch as fetch_mod

        cdp_calls = []
        dl_urls = []

        def fake_cdp_fetch(url, out_path=None, **kwargs):
            cdp_calls.append(url)
            f = tmp_path / f"scys_{len(cdp_calls)}.md"
            f.write_text("# scys标题\n\n" + "生财正文" * 100, encoding="utf-8")
            return {"title": "scys标题", "url": url, "chars": 500,
                    "login_wall_hit": [], "output": str(f)}

        def fake_download(url):
            dl_urls.append(url)
            return ("<html><body><article><h1>普通博客标题</h1><p>"
                    + "普通博客正文内容，用于测试混合分流场景。" * 30
                    + "</p></article></body></html>", None)

        monkeypatch.setattr(fetch_mod, "_scys_cdp_fetch", fake_cdp_fetch)
        monkeypatch.setattr(fetch_mod, "_download", fake_download)

        urls = [
            "https://scys.com/articleDetail/xq_topic/22255411815528241",
            "https://mp.weixin.qq.com/s/abc123",
            "https://scys.com/articleDetail/xq_topic/45544211585418858",
            "https://www.ruanyifeng.com/blog/2026/08/weekly.html",
        ]
        results = {}
        for u in urls:
            results[u] = fetch_web_content(u)

        assert len(cdp_calls) == 2
        assert urls[0] in cdp_calls and urls[2] in cdp_calls
        assert set(dl_urls) == {urls[1], urls[3]}
        for u, r in results.items():
            assert r is not None, f"{u} 应抓取成功"
            assert len(r[1]) > 100


class TestScysProjectsConfig:
    def test_config_loads_with_projects(self):
        import importlib
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import scys_batch_fetch
        importlib.reload(scys_batch_fetch)
        cfg = scys_batch_fetch.load_config()
        assert "AI产品开发" in cfg["projects"]
        assert isinstance(cfg["projects"]["AI产品开发"], int)
        assert cfg["defaults"]["since_days"] > 0

    def test_config_batch_limit_reasonable(self):
        import importlib
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import scys_batch_fetch
        importlib.reload(scys_batch_fetch)
        cfg = scys_batch_fetch.load_config()
        assert 1 <= cfg["defaults"].get("batch_limit", 30) <= 100
