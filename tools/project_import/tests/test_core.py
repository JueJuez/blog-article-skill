import os
import sys
import tempfile
import unittest
from unittest import mock
import pathlib

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from assets import extractor, collector, analyzer, tracker, storage
from assets import content_source, project_finder, feishu_writer, local_writer


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


if __name__ == "__main__":
    unittest.main()
