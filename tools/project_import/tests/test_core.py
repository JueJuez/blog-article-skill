import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock
import pathlib

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from assets import extractor, collector, analyzer, tracker, storage
from assets import content_source, project_finder, feishu_writer, local_writer, name_resolver, ingest_repo, quality_gate, main as main_mod


class ExtractorTest(unittest.TestCase):
    def test_extract_basic(self):
        text = "看看 https://github.com/langchain-ai/langchain-mcp-server 和 https://github.com/axios/axios"
        urls = extractor.extract_repo_urls(text)
        self.assertEqual(urls, [
            "https://github.com/axios/axios",
            "https://github.com/langchain-ai/langchain-mcp-server",
        ])

    def test_extract_gitee(self):
        text = "github https://github.com/foo/bar 与 gitee https://gitee.com/baz/qux 和 gitee ssh git@gitee.com:x/y.git"
        urls = extractor.extract_repo_urls(text)
        self.assertEqual(urls, [
            "https://gitee.com/baz/qux",
            "https://gitee.com/x/y",
            "https://github.com/foo/bar",
        ])

    def test_extract_dedup_in_batch(self):
        text = ("https://github.com/foo/bar https://github.com/foo/bar "
                "https://github.com/foo/bar.git")
        urls = extractor.extract_repo_urls(text)
        self.assertEqual(urls, ["https://github.com/foo/bar"])

    def test_ignores_non_repo_pages(self):
        text = ("https://github.com/settings/profile "
                "https://github.com/sponsors/foo "
                "https://github.com/trending")
        self.assertEqual(extractor.extract_repo_urls(text), [])

    def test_ssh_format(self):
        text = "git@github.com:owner/repo.git"
        self.assertEqual(extractor.extract_repo_urls(text),
                         ["https://github.com/owner/repo"])

    def test_parse_repo_platform(self):
        self.assertEqual(extractor.parse_repo("https://gitee.com/a/b"), ("gitee", "a", "b"))
        self.assertEqual(extractor.parse_repo("https://github.com/a/b"), ("github", "a", "b"))

    def test_filter_imported_and_pending(self):
        with tempfile.TemporaryDirectory() as d:
            imported = os.path.join(d, "imported.txt")
            pending = os.path.join(d, "pending.json")
            with open(imported, "w", encoding="utf-8") as f:
                f.write("foo/bar\n")
            with open(pending, "w", encoding="utf-8") as f:
                f.write('[{"_owner_repo": "baz/qux"}]')
            tracker.IMPORTED_FILE = __import__("pathlib").Path(imported)
            storage.PENDING_FILE = pending
            urls = [
                "https://github.com/foo/bar",
                "https://github.com/baz/qux",
                "https://github.com/new/repo",
            ]
            new, imp = extractor.filter_imported(urls)
            self.assertEqual(imp, ["https://github.com/foo/bar"])
            self.assertEqual(set(new), {
                "https://github.com/baz/qux",
                "https://github.com/new/repo",
            })
            new2, pend = extractor.filter_pending(new)
            self.assertEqual(pend, ["https://github.com/baz/qux"])
            self.assertEqual(new2, ["https://github.com/new/repo"])


class CollectorTest(unittest.TestCase):
    def test_stars_to_score(self):
        cases = [
            (0, 1), (10, 1), (11, 2), (100, 2), (101, 3),
            (500, 3), (1000, 4), (5000, 5), (10000, 6),
            (30000, 7), (100000, 8), (500000, 9), (500001, 10),
        ]
        for stars, expected in cases:
            self.assertEqual(collector.stars_to_score(stars), expected,
                             msg=f"stars={stars}")


class AnalyzerTest(unittest.TestCase):
    def test_parse_plain_json(self):
        r = analyzer.parse_llm_response('{"summary":"x","project_type":"MCP","doc_score":7,"func_score":8}')
        self.assertIsNotNone(r)
        self.assertEqual(r.project_type, "MCP")
        self.assertEqual(r.doc_score, 7)

    def test_parse_fenced_json(self):
        r = analyzer.parse_llm_response('```json\n{"summary":"y","doc_score":5}\n```')
        self.assertIsNotNone(r)
        self.assertEqual(r.summary, "y")

    def test_parse_invalid(self):
        self.assertIsNone(analyzer.parse_llm_response("not json at all"))
        self.assertIsNone(analyzer.parse_llm_response(""))

    def test_to_feishu_fields_all_mapped(self):
        # 用代码内提交的 DEFAULT_FIELD_MAP 验证 to_feishu_fields 写出全部 15 列
        # （含此前为 null 的项目类型 / 运行形式 / 社区评分 / 状态），不依赖私有 feishu_fields.json
        r = analyzer.AnalysisResult(
            summary="s", project_type="MCP", run_form="MCP-stdio",
            target_user="Agent调用", domain="通用工具",
            doc_score=7, func_score=8)
        fields = r.to_feishu_fields(
            "repo", "https://github.com/o/repo", 300,
            field_map=feishu_writer.DEFAULT_FIELD_MAP)
        # 15 个逻辑字段全部写出（无字段被跳过）
        self.assertEqual(len(fields), 15)
        self.assertEqual(fields["fldWipEsqn"], "repo")        # 项目名称
        self.assertEqual(fields["fld3urADAF"], "MCP")        # 项目类型（此前为 null）
        self.assertEqual(fields["fldtoRnPG9"], "MCP-stdio")  # 运行形式（此前为 null）
        self.assertEqual(fields["fldNXvEbeG"], "Agent调用")  # 给谁用
        self.assertEqual(fields["fldS6Xnn6h"], "通用工具")   # 功能领域
        self.assertEqual(fields["fldz26W1X4"], "已入库")     # 状态（此前为 null）
        # 评分
        self.assertEqual(fields["fldqHZ3KZt"], 3)            # 社区评分 = stars_to_score(300)=3（此前为 null）
        self.assertEqual(fields["fldOdZy7KC"], 7)            # 文档评分
        self.assertEqual(fields["fldCbpLXal"], 8)            # 功能评分
        self.assertEqual(fields["fldQIa5t33"], 18)           # 综合评分 = 3+7+8
        # 评估日期为完整 datetime 格式 YYYY-MM-DD HH:MM:SS
        self.assertRegex(fields["fldztznzza"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class TrackerTest(unittest.TestCase):
    def test_append_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "imported.txt")
            tracker.IMPORTED_FILE = __import__("pathlib").Path(p)
            tracker.append_to_imported_list("Foo/Bar")
            tracker.append_to_imported_list("foo/bar")  # 重复（大小写不敏感）
            tracker.append_to_imported_list("baz/qux")
            with open(p, encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip()]
            self.assertEqual(lines, ["foo/bar", "baz/qux"])


class FinderTest(unittest.TestCase):
    """content_source / project_finder：输入路由 + 抽取 + 来源标记（mock 掉网络）。"""

    def test_direct_text(self):
        cands, _ = project_finder.find(
            "看看 https://github.com/a/b 和 https://gitee.com/c/d", dedup=False)
        self.assertEqual({c.url for c in cands},
                         {"https://github.com/a/b", "https://gitee.com/c/d"})
        for c in cands:
            self.assertEqual(c.source_kind, "direct")

    def test_article_url(self):
        with mock.patch("articles.fetch.fetch_web_content",
                        return_value=("标题", "正文提到 https://github.com/art/foo 项目", 0)):
            cands, _ = project_finder.find("https://blog.example.com/post/1", dedup=False)
        self.assertEqual([c.url for c in cands], ["https://github.com/art/foo"])
        self.assertEqual(cands[0].source_kind, "body")

    def test_video_description_priority(self):
        with mock.patch("assets.content_source._bili_description",
                        return_value="项目地址 https://gitee.com/vid/desc 欢迎 star"), \
             mock.patch("assets.content_source._bili_subtitle",
                        return_value="字幕里只提了 repo-name 但没给链接"):
            cands, _ = project_finder.find(
                "https://www.bilibili.com/video/BVxxxx", dedup=False)
        self.assertEqual([c.url for c in cands], ["https://gitee.com/vid/desc"])
        self.assertEqual(cands[0].source_kind, "description")

    def test_video_name_only_no_url_phase1(self):
        # phase1：字幕里只有仓库名、无 URL，不应被抽取
        with mock.patch("assets.content_source._bili_description", return_value=""), \
             mock.patch("assets.content_source._bili_subtitle",
                        return_value="这个工具叫 awesome-tool 很牛但没给链接"):
            cands, _ = project_finder.find(
                "https://www.bilibili.com/video/BVyyyy", dedup=False)
        self.assertEqual(cands, [])

    def test_youtube_url_routes(self):
        with mock.patch("assets.content_source._yt_description",
                        return_value="see https://github.com/yt/proj"), \
             mock.patch("assets.content_source._yt_subtitle", return_value=""):
            cands, _ = project_finder.find(
                "https://www.youtube.com/watch?v=abc", dedup=False)
        self.assertEqual([c.url for c in cands], ["https://github.com/yt/proj"])
        self.assertEqual(cands[0].platform, "youtube")
        self.assertEqual(cands[0].source_kind, "description")

    def test_dedup_skip(self):
        # 注意：owner 名不能用 "new"（在 extractor 的 _NON_REPO_OWNERS 黑名单里，
        # 对应 github.com/new 新建页），否则会被当作非法 owner 丢弃。用 brandnew 代替。
        with tempfile.TemporaryDirectory() as d:
            imported = os.path.join(d, "imported.txt")
            with open(imported, "w", encoding="utf-8") as f:
                f.write("a/b\n")
            tracker.IMPORTED_FILE = pathlib.Path(imported)
            storage.PENDING_FILE = os.path.join(d, "pending.json")
            cands, skipped = project_finder.find(
                "https://github.com/a/b https://github.com/brandnew/repo", dedup=True)
            self.assertEqual([c.url for c in cands], ["https://github.com/brandnew/repo"])
            self.assertEqual(skipped, ["https://github.com/a/b"])


class LocalWriterTest(unittest.TestCase):
    """local_writer：frontmatter 渲染 + 文件名映射 + 写入幂等。"""

    def test_owner_repo_to_filename(self):
        self.assertEqual(local_writer.owner_repo_to_filename("a/b"), "a__b.md")
        # 非法文件名字符 -> 下划线
        self.assertEqual(
            local_writer.owner_repo_to_filename("foo/bar: baz?"),
            "foo__bar_baz_.md")

    def test_parse_owner_repo_from_url(self):
        self.assertEqual(
            local_writer.parse_owner_repo_from_url("https://github.com/foo/bar"),
            "foo/bar")
        self.assertEqual(
            local_writer.parse_owner_repo_from_url("https://gitee.com/x/y.git"),
            "x/y")
        self.assertEqual(
            local_writer.parse_owner_repo_from_url("https://github.com/foo/bar#readme"),
            "foo/bar")
        self.assertEqual(local_writer.parse_owner_repo_from_url("not a url"), "")

    def test_extract_raw_url(self):
        # Bitable 单元格是 markdown 链接
        self.assertEqual(
            local_writer.extract_raw_url("[text](https://github.com/o/r)"),
            "https://github.com/o/r")
        # 纯文本原样返回
        self.assertEqual(
            local_writer.extract_raw_url("https://github.com/o/r"),
            "https://github.com/o/r")
        self.assertEqual(local_writer.extract_raw_url(""), "")

    def test_render_markdown_basic(self):
        md = local_writer.render_markdown({
            "owner_repo": "foo/bar",
            "url": "https://github.com/foo/bar",
            "tags": ["AI", "MCP"],
        })
        self.assertTrue(md.startswith("---\n"))
        self.assertIn("owner_repo: foo/bar", md)
        # URL 含半角冒号 -> 自动加 YAML 引号（YAML 安全）
        self.assertIn('url: "https://github.com/foo/bar"', md)
        self.assertIn("tags:\n  - AI\n  - MCP", md)
        self.assertTrue(md.endswith("---\n"))

    def test_render_markdown_quotes_special_chars(self):
        # 含半角冒号的值必须加 YAML 引号，否则解析会出错
        md = local_writer.render_markdown({
            "summary": "运行方式: stdio",
            "domain": "通用工具",
            "doc_score": 7,
        })
        self.assertIn('summary: "运行方式: stdio"', md)
        # 数字按原类型渲染（不加引号）
        self.assertIn("doc_score: 7", md)

    def test_build_frontmatter_stores_values(self):
        # build_frontmatter 只负责组装 dict；评分计算在 write_project_md 里做
        fm = local_writer.build_frontmatter(
            "foo/bar", "https://github.com/foo/bar", 1000,
            project_type="MCP", doc_score=7, func_score=8,
            community_score=4, total_score=19,
            source_kind="article")
        self.assertEqual(fm["community_score"], 4)
        self.assertEqual(fm["total_score"], 19)
        self.assertEqual(fm["platform"], "github")
        self.assertEqual(fm["status"], "已入库")
        # 未给 imported_at 时自动生成 datetime 格式
        self.assertRegex(fm["imported_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_write_computes_community_score(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["PROJECT_LIBRARY_DIR"] = d
            try:
                local_writer.write_project_md(
                    "foo/bar", "https://github.com/foo/bar", 1000,
                    project_type="MCP", doc_score=7, func_score=8)
                path = os.path.join(d, "foo__bar.md")
                with open(path, encoding="utf-8") as f:
                    content = f.read()
                # stars_to_score(1000) = 4；total = 4 + 7 + 8 = 19
                self.assertIn("community_score: 4", content)
                self.assertIn("total_score: 19", content)
                self.assertIn("platform: github", content)
            finally:
                os.environ.pop("PROJECT_LIBRARY_DIR", None)

    def test_write_and_skip_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["PROJECT_LIBRARY_DIR"] = d
            try:
                # 首次写入
                r1 = local_writer.write_project_md(
                    "foo/bar", "https://github.com/foo/bar", 100,
                    project_type="MCP", doc_score=7, func_score=8,
                    summary="测试项目", tags=["AI"])
                self.assertEqual(r1, "written")
                path = os.path.join(d, "foo__bar.md")
                self.assertTrue(os.path.exists(path))
                # 文件内容包含 frontmatter
                with open(path, encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("owner_repo: foo/bar", content)
                # 二次写入（文件已存在）应幂等跳过
                r2 = local_writer.write_project_md(
                    "foo/bar", "https://github.com/foo/bar", 100,
                    project_type="MCP")
                self.assertEqual(r2, "skipped")
            finally:
                os.environ.pop("PROJECT_LIBRARY_DIR", None)

    def test_write_unconfigured(self):
        saved = os.environ.pop("PROJECT_LIBRARY_DIR", None)
        saved_vault = os.environ.pop("OBSIDIAN_VAULT_PATH", None)
        try:
            self.assertEqual(local_writer.write_project_md(
                "foo/bar", "https://github.com/foo/bar", 0), "unconfigured")
        finally:
            if saved is not None:
                os.environ["PROJECT_LIBRARY_DIR"] = saved
            if saved_vault is not None:
                os.environ["OBSIDIAN_VAULT_PATH"] = saved_vault


class XiaoheiheTest(unittest.TestCase):
    """小黑盒(xiaoheihe.cn)：无头浏览器渲染正文后抠仓库链接。"""

    def test_fetch_xiaoheihe_returns_rendered_body(self):
        fake_stdout = "GitHub 最全的古诗词数据库 https://github.com/chinese-poetry/chinese-poetry"
        with mock.patch.object(content_source.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=fake_stdout, stderr="")
            out = content_source.fetch_xiaoheihe(
                "https://www.xiaoheihe.cn/bbs/post_share?link_id=x")
        self.assertIn("https://github.com/chinese-poetry/chinese-poetry", out)

    def test_fetch_xiaoheihe_empty_on_failure(self):
        with mock.patch.object(content_source.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="", stderr="err")
            self.assertEqual(
                content_source.fetch_xiaoheihe("https://www.xiaoheihe.cn/x"), "")

    def test_resolve_xiaoheihe_routes_to_body(self):
        body = "介绍 https://github.com/chinese-poetry/chinese-poetry 这个项目"
        with mock.patch.object(content_source, "fetch_xiaoheihe", return_value=body):
            sources, plat = content_source.resolve(
                "https://www.xiaoheihe.cn/bbs/post_share?link_id=x")
        self.assertEqual(plat, "xiaoheihe")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].kind, "body")
        self.assertIn("https://github.com/chinese-poetry/chinese-poetry", sources[0].text)

    def test_xiaoheihe_name_fallback_to_search(self):
        # 正文无 URL，但标题给出项目名；应触发 name-search 兜底
        body = "[1] ChinaTextbook: 把教材开源\n[2] ebook2audiobook: 有声书"
        fake_hits = {
            "ChinaTextbook": {
                "url": "https://github.com/xx/ChinaTextbook",
                "owner_repo": "xx/ChinaTextbook",
                "stars": 100,
                "desc": "教材",
            },
            "ebook2audiobook": {
                "url": "https://github.com/yy/ebook2audiobook",
                "owner_repo": "yy/ebook2audiobook",
                "stars": 200,
                "desc": "有声书",
            },
        }
        with mock.patch.object(content_source, "fetch_xiaoheihe", return_value=body), \
             mock.patch.object(
                 project_finder, "search_github_by_name",
                 side_effect=lambda n: fake_hits.get(n)):
            cands, _ = project_finder.find(
                "https://www.xiaoheihe.cn/bbs/post_share?link_id=x", dedup=False)
        urls = {c.url for c in cands}
        self.assertEqual(
            urls,
            {"https://github.com/xx/ChinaTextbook",
             "https://github.com/yy/ebook2audiobook"})
        for c in cands:
            self.assertEqual(c.source_kind, "name-search")
            self.assertEqual(c.platform, "xiaoheihe")


class NameSearchTest(unittest.TestCase):
    """按项目名搜索收录（GitHub 搜索 API，未配 LLM 时的兜底收录入口）。"""

    def _fake_response(self, payload):
        fake = mock.Mock()
        fake.__enter__ = lambda s: s
        fake.__exit__ = lambda s, *a: False
        fake.read = lambda: __import__("json").dumps(payload).encode()
        return fake

    def test_extract_project_name_candidates_heading(self):
        text = "[1] ChinaTextbook: 开源教材\n[2] ebook2audiobook: 有声书"
        names = name_resolver.extract_project_name_candidates(text, platform="xiaoheihe")
        self.assertEqual(names, ["ChinaTextbook", "ebook2audiobook"])

    def test_extract_project_name_candidates_fallback(self):
        # 无标题时，含上下文关键字的行里的 CamelCase 项目名会被提取
        text = "这个项目叫 DeepTutor，在 GitHub 上开源。"
        names = name_resolver.extract_project_name_candidates(text, platform="xiaoheihe")
        self.assertEqual(names, ["DeepTutor"])

    def test_search_exact_match_preferred(self):
        payload = {"items": [
            {
                "html_url": "https://github.com/other/NotToolKnit",
                "full_name": "other/NotToolKnit",
                "name": "NotToolKnit",
                "stargazers_count": 9999,
                "description": "更高星但不是目标",
            },
            {
                "html_url": "https://github.com/ZihangDong/toolknit-desktop",
                "full_name": "ZihangDong/toolknit-desktop",
                "name": "toolknit-desktop",
                "stargazers_count": 876,
                "description": "多功能工具箱",
            },
        ]}
        with mock.patch.object(
                name_resolver.urllib.request, "urlopen",
                return_value=self._fake_response(payload)):
            res = name_resolver.search_github_by_name("toolknit-desktop")
        # 即便第一项星更高，优先精确匹配 name
        self.assertEqual(res["owner_repo"], "ZihangDong/toolknit-desktop")
        self.assertEqual(res["stars"], 876)

    def test_search_no_match_returns_none(self):
        payload = {"items": []}
        with mock.patch.object(
                name_resolver.urllib.request, "urlopen",
                return_value=self._fake_response(payload)):
            res = name_resolver.search_github_by_name("zzznotexist")
        self.assertIsNone(res)


class NameResolverWebFallbackTest(unittest.TestCase):
    """GitHub 网页搜索兜底：当搜索 API 被限流/无结果时，解析 github.com/search HTML。"""

    def _fake_html(self, owner: str, repo: str) -> bytes:
        html = (
            f'<div class="search-title">'
            f'<a data-component="Link" href="/{owner}/{repo}">{owner}/{repo}</a>'
            f'</div>'
        )
        return html.encode("utf-8")

    def _fake_response(self, body: bytes):
        fake = mock.Mock()
        fake.__enter__ = lambda s: s
        fake.__exit__ = lambda s, *a: False
        fake.read = lambda: body
        return fake

    def test_fallback_used_when_api_rate_limited(self):
        api_err = urllib.error.HTTPError(
            "https://api.github.com/search/repositories", 403,
            "rate limited", {}, None)
        html = self._fake_html("tapxworld", "chinatextbook")
        with mock.patch.object(
                name_resolver.urllib.request, "urlopen",
                side_effect=[api_err, self._fake_response(html)]) as m:
            res = name_resolver.search_github_by_name("ChinaTextbook")
        self.assertIsNotNone(res)
        self.assertEqual(res["owner_repo"], "tapxworld/chinatextbook")
        self.assertIn(
            "github.com/search?q=ChinaTextbook",
            m.call_args_list[1][0][0].full_url)

    def test_fallback_disabled_returns_none(self):
        payload = {"items": []}
        with mock.patch.object(
                name_resolver.urllib.request, "urlopen",
                return_value=self._fake_response(json.dumps(payload).encode())):
            res = name_resolver.search_github_by_name(
                "zzz", use_web_fallback=False)
        self.assertIsNone(res)


class IngestRepoTest(unittest.TestCase):
    """ingest_repo.py：agent 产出的分析 JSON -> 直接写入本地库。"""

    def test_ingest_writes_and_appends_imported(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["PROJECT_LIBRARY_DIR"] = d
            try:
                analysis_path = os.path.join(d, "analysis.json")
                with open(analysis_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "summary": "test project summary",
                        "project_type": "项目",
                        "run_form": "不适用",
                        "target_user": "本地运行",
                        "domain": "通用工具",
                        "tags": ["test"],
                        "highlights": "highlight",
                        "doc_score": 5,
                        "func_score": 5,
                    }, f)
                with mock.patch.object(
                        ingest_repo, "collect_project_data",
                        return_value=("readme", 100, None)):
                    with mock.patch.object(
                            ingest_repo, "append_to_imported_list") as mock_append:
                        ret = ingest_repo.main([
                            "foo/bar", analysis_path,
                            "--source-kind", "name-search"])
                self.assertEqual(ret, 0)
                path = os.path.join(d, "foo__bar.md")
                self.assertTrue(os.path.exists(path))
                with open(path, encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("owner_repo: foo/bar", content)
                self.assertIn("source_kind: name-search", content)
                self.assertIn("community_score: 2", content)
                self.assertIn("total_score: 12", content)
                mock_append.assert_called_once_with("foo/bar")
            finally:
                os.environ.pop("PROJECT_LIBRARY_DIR", None)

    def test_ingest_skips_existing(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["PROJECT_LIBRARY_DIR"] = d
            # 隔离门禁：本测试只验证「已存在则跳过」语义（doc/func 双双低于阈值
            # 会触发门禁，干扰 skip 判定），临时关闭门禁。
            saved_enabled = os.environ.get("QUALITY_GATE_ENABLED")
            os.environ["QUALITY_GATE_ENABLED"] = "0"
            try:
                open(os.path.join(d, "foo__bar.md"), "w", encoding="utf-8").close()
                analysis_path = os.path.join(d, "analysis.json")
                with open(analysis_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "summary": "x", "project_type": "项目",
                        "run_form": "不适用", "target_user": "本地运行",
                        "domain": "通用工具", "tags": [],
                        "highlights": "x", "doc_score": 1, "func_score": 1,
                    }, f)
                with mock.patch.object(
                        ingest_repo, "collect_project_data",
                        return_value=("readme", 100, None)):
                    ret = ingest_repo.main(["foo/bar", analysis_path])
                self.assertEqual(ret, 0)
            finally:
                os.environ.pop("PROJECT_LIBRARY_DIR", None)
                if saved_enabled is None:
                    os.environ.pop("QUALITY_GATE_ENABLED", None)
                else:
                    os.environ["QUALITY_GATE_ENABLED"] = saved_enabled

    def test_ingest_low_quality_routes_to_review(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["PROJECT_LIBRARY_DIR"] = d
            gate_file = os.path.join(d, "pending_review.json")
            saved_gate = quality_gate.GATE_FILE
            quality_gate.GATE_FILE = pathlib.Path(gate_file)
            saved_enabled = os.environ.get("QUALITY_GATE_ENABLED")
            os.environ["QUALITY_GATE_ENABLED"] = "1"  # 显式开启以验证路由
            try:
                analysis_path = os.path.join(d, "analysis.json")
                with open(analysis_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "summary": "junk", "project_type": "项目",
                        "run_form": "不适用", "target_user": "本地运行",
                        "domain": "通用工具", "tags": [],
                        "highlights": "x", "doc_score": 1, "func_score": 1,
                    }, f)
                # stars=5 < 阈值 100 -> 门禁触发
                with mock.patch.object(
                        ingest_repo, "collect_project_data",
                        return_value=("readme", 5, None)):
                    with mock.patch.object(
                            ingest_repo, "append_to_imported_list") as mock_append:
                        ret = ingest_repo.main([
                            "junk/repo", analysis_path,
                            "--source-kind", "name-search"])
                self.assertEqual(ret, 0)
                # 不应写入本地库
                self.assertFalse(os.path.exists(os.path.join(d, "junk__repo.md")))
                # 不应记入 imported.txt
                mock_append.assert_not_called()
                # 应进入待复核队列
                with open(gate_file, encoding="utf-8") as f:
                    items = json.load(f)
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["owner_repo"], "junk/repo")
                self.assertEqual(items[0]["reason"], "stars=5 < 阈值 100")
            finally:
                os.environ.pop("PROJECT_LIBRARY_DIR", None)
                quality_gate.GATE_FILE = saved_gate
                if saved_enabled is None:
                    os.environ.pop("QUALITY_GATE_ENABLED", None)
                else:
                    os.environ["QUALITY_GATE_ENABLED"] = saved_enabled


class QualityGateTest(unittest.TestCase):
    """quality_gate：阈值判定 + 待复核队列幂等。

    门禁默认关闭（opt-in），本组测试显式开启以验证「开启」行为。
    """

    def setUp(self):
        self._saved = os.environ.get("QUALITY_GATE_ENABLED")
        os.environ["QUALITY_GATE_ENABLED"] = "1"

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("QUALITY_GATE_ENABLED", None)
        else:
            os.environ["QUALITY_GATE_ENABLED"] = self._saved

    def test_low_stars_flagged(self):
        self.assertTrue(quality_gate.is_low_quality(8, 7, 8))    # stars<100
        self.assertTrue(quality_gate.is_low_quality(3, 3, 10))   # stars<100

    def test_high_stars_pass(self):
        self.assertFalse(quality_gate.is_low_quality(7, 8, 5000))

    def test_both_scores_low_with_high_stars(self):
        # stars 高但 doc/func 双双低于阈值 -> 判低质
        self.assertTrue(quality_gate.is_low_quality(3, 4, 5000))

    def test_one_score_ok_with_high_stars(self):
        # 仅一个低于阈值 -> 不判低质
        self.assertFalse(quality_gate.is_low_quality(7, 4, 5000))

    def test_disabled_env(self):
        saved = os.environ.get("QUALITY_GATE_ENABLED")
        os.environ["QUALITY_GATE_ENABLED"] = "0"
        try:
            self.assertFalse(quality_gate.is_low_quality(3, 3, 5))
        finally:
            if saved is None:
                os.environ.pop("QUALITY_GATE_ENABLED", None)
            else:
                os.environ["QUALITY_GATE_ENABLED"] = saved

    def test_route_to_review_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            gate_file = os.path.join(d, "pending_review.json")
            saved_gate = quality_gate.GATE_FILE
            quality_gate.GATE_FILE = pathlib.Path(gate_file)
            try:
                res = analyzer.AnalysisResult(doc_score=3, func_score=4)
                r1 = quality_gate.route_to_review(
                    "foo/bar", "https://github.com/foo/bar", 8,
                    res, "name-search", "stars=8 < 阈值 100")
                r2 = quality_gate.route_to_review(
                    "Foo/Bar", "https://github.com/foo/bar", 8,
                    res, "name-search", "stars=8 < 阈值 100")
                self.assertEqual(r1, "reviewed")
                self.assertEqual(r2, "reviewed")
                with open(gate_file, encoding="utf-8") as f:
                    items = json.load(f)
                self.assertEqual(len(items), 1)  # 幂等：仅 1 条
                self.assertEqual(items[0]["owner_repo"], "foo/bar")
            finally:
                quality_gate.GATE_FILE = saved_gate


class Phase4QualityGateTest(unittest.TestCase):
    """main.phase4_store：本地入库路径下，低质量项目转入复核而非写入。"""

    def test_phase4_routes_low_quality_to_review(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["PROJECT_LIBRARY_DIR"] = d
            gate_file = os.path.join(d, "pending_review.json")
            saved_gate = quality_gate.GATE_FILE
            quality_gate.GATE_FILE = pathlib.Path(gate_file)
            saved_enabled = os.environ.get("QUALITY_GATE_ENABLED")
            os.environ["QUALITY_GATE_ENABLED"] = "1"  # 显式开启以验证路由
            try:
                completed = [(
                    "https://github.com/junk/repo", "junk/repo", 8,
                    analyzer.AnalysisResult(
                        summary="x", project_type="项目", run_form="不适用",
                        target_user="本地运行", domain="通用工具",
                        doc_score=1, func_score=1),
                )]
                report = main_mod.phase4_store(completed, [], source_kind="name-search")
                # 不应写入本地库
                self.assertFalse(os.path.exists(os.path.join(d, "junk__repo.md")))
                # 待复核队列应有 1 条
                with open(gate_file, encoding="utf-8") as f:
                    items = json.load(f)
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["owner_repo"], "junk/repo")
                # 报告项应标记为 reviewed
                self.assertEqual(report["items"][0].status, "reviewed")
            finally:
                os.environ.pop("PROJECT_LIBRARY_DIR", None)
                quality_gate.GATE_FILE = saved_gate
                if saved_enabled is None:
                    os.environ.pop("QUALITY_GATE_ENABLED", None)
                else:
                    os.environ["QUALITY_GATE_ENABLED"] = saved_enabled


if __name__ == "__main__":
    unittest.main()
