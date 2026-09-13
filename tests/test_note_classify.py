# -*- coding: utf-8 -*-
"""方案 A 落地验证：命名空间语义标签生成 + 分类路由跳过 / 标签。

运行：python tests/test_note_classify.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.note_classify import infer_semantic_tags
from shared.routing import category_from_tags

try:
    from prompts.templates import format_note_with_prompt
    _HAS_FORMAT = True
except Exception:
    _HAS_FORMAT = False

try:
    from unittest.mock import patch, MagicMock
    from articles.main import save_summarized_article
    _HAS_SAVE = True
except Exception:
    _HAS_SAVE = False


S_INVEST = (
    "# 拆解伊利股份：乳制品龙头的护城河\n\n"
    "**30秒速览**：伊利股份作为乳制品龙头，凭借品牌与渠道护城河维持高 ROE，"
    "本文从财报角度分析其估值与分红，属于公司分析。\n\n"
    "## 一、公司概况\n伊利股份是国内乳制品龙头……"
)

S_BUSINESS = (
    "# 如何用虚拟资料在小红书变现\n\n"
    "**30秒速览**：本文讲通过制作 Excel 模板等虚拟产品在小红书变现，属于虚拟产品赛道，"
    "附带可复用的实操方法论。\n\n"
    "## 一、选品\n虚拟资料指……"
)

S_AI = (
    "# 用 Claude Code 提升编程效率\n\n"
    "**30秒速览**：本文介绍如何用 Claude Code 做 AI 编程，提升日常开发效率，附实操步骤。\n\n"
    "## 一、安装\nClaude Code 是……"
)

S_CONTENT = (
    "# 小红书涨粉的选题方法论\n\n"
    "**30秒速览**：本文拆解小红书账号涨粉的选题与文案方法论，属于平台运营复盘。\n\n"
    "## 一、选题\n小红书……"
)

S_PSYCH = (
    "# 从心理学角度理解 MBTI 人格分析\n\n"
    "**30秒速览**：本文从心理学角度讲 MBTI 人格分析，帮助理解亲密关系中的差异。\n\n"
    "## 一、概念\nMBTI……"
)


class TestInferSemanticTags(unittest.TestCase):
    def test_invest_pig_submap(self):
        tags = infer_semantic_tags(
            S_INVEST, folder="【监控】/B站/价投小猪仔/小猪仔拆公司",
            author="价投小猪仔", note_type="structured")
        self.assertIn("投资/公司分析", tags)
        self.assertIn("topic/伊利股份", tags)
        self.assertIn("用途/教学可用", tags)
        self.assertIn("来源/价投小猪仔", tags)
        self.assertIn("类型/结构化复盘", tags)
        for t in tags:
            if t.startswith("投资"):
                self.assertIn("/", t)
                self.assertNotIn("#", t)

    def test_business_virtual_product(self):
        tags = infer_semantic_tags(
            S_BUSINESS, folder="生财有术/虚拟产品",
            author="生财有术", note_type="case")
        self.assertIn("商业/虚拟产品", tags)
        self.assertIn("topic/小红书", tags)
        self.assertIn("topic/虚拟产品", tags)
        self.assertIn("用途/素材可用", tags)
        self.assertIn("用途/方法论可复用", tags)
        self.assertIn("类型/案例拆解", tags)

    def test_ai_datwhale(self):
        tags = infer_semantic_tags(
            S_AI, folder="【我的总结】/作者/Datawhale",
            author="Datawhale", note_type="structured")
        self.assertIn("AI科技/AI编程", tags)
        self.assertIn("topic/Claude Code", tags)
        self.assertIn("来源/Datawhale", tags)

    def test_content_creation(self):
        tags = infer_semantic_tags(
            S_CONTENT, folder="生财有术/自媒体",
            author="生财有术", note_type="dissection")
        self.assertIn("内容创作/平台运营", tags)
        self.assertIn("topic/小红书", tags)
        self.assertIn("类型/创作解剖", tags)

    def test_inbox_fallback_no_domain_when_comprehensive(self):
        tags = infer_semantic_tags(S_PSYCH, folder="【待归类】", author="")
        domain_tags = [t for t in tags if "/" in t
                       and not t.startswith("topic/") and not t.startswith("用途/")
                       and not t.startswith("来源/") and not t.startswith("类型/")]
        self.assertEqual(domain_tags, [])
        self.assertIn("topic/MBTI", tags)
        self.assertIn("topic/心理学", tags)

    def test_no_duplicate_and_no_slash_in_topic(self):
        tags = infer_semantic_tags(S_INVEST, folder="【监控】/B站/价投小猪仔/小猪仔拆公司",
                                   author="价投小猪仔", note_type="structured")
        self.assertEqual(len(tags), len(set(tags)))
        for t in tags:
            if t.startswith("topic/"):
                self.assertNotIn("/", t[len("topic/"):])


class TestCategoryRouting(unittest.TestCase):
    def test_skip_namespace_tags(self):
        self.assertEqual(category_from_tags(["文章总结", "投资/公司分析", "来源/价投小猪仔"]), "")
        self.assertEqual(category_from_tags(["投资/公司分析", "用途/教学可用"]), "")

    def test_keep_plain_category(self):
        self.assertEqual(category_from_tags(["投资交易", "投资/公司分析"]), "投资交易")
        self.assertEqual(category_from_tags(["实盘记录"]), "实盘记录")

    def test_empty(self):
        self.assertEqual(category_from_tags([]), "")
        self.assertEqual(category_from_tags(["文章总结", "转载"]), "")


class TestFormatOutput(unittest.TestCase):
    @unittest.skipUnless(_HAS_FORMAT, "prompts.templates 不可导入（依赖缺失）")
    def test_hashtag_line_includes_namespace(self):
        out = format_note_with_prompt(
            "# 标题\n正文", author="价投小猪仔", url="http://example.com",
            tags=["文章总结", "转载", "投资/公司分析", "topic/伊利股份"],
            add_metadata=True)
        self.assertIn("#文章总结 #转载 #投资/公司分析 #topic/伊利股份", out)


class FakeManager:
    def __init__(self, *a, **k):
        pass

    def get_available_outputs(self):
        return []

    def save_all(self, note, fn, title=""):
        pass


class TestSaveIntegration(unittest.TestCase):
    """端到端：save_summarized_article 真的把命名空间标签写进笔记且不污染分类。"""
    @unittest.skipUnless(_HAS_SAVE, "articles.main 不可导入（依赖缺失）")
    def test_save_injects_namespace_tags(self):
        with patch("articles.main.OutputManager", FakeManager), \
             patch("articles.main.dedup") as mock_dedup:
            mock_dedup.mark_summarized = MagicMock()
            note, fn = save_summarized_article(
                S_INVEST, original_url="http://example.com/x", author="价投小猪仔",
                tags=["文章总结"], original_title="拆解伊利股份", note_type="structured",
                folder="【监控】/B站/价投小猪仔/小猪仔拆公司", publish_time=0)
        # 五个维度标签都进笔记
        for expected in ("#投资/公司分析", "#topic/伊利股份", "#用途/教学可用",
                         "#来源/价投小猪仔", "#类型/结构化复盘", "#文章总结", "#转载"):
            self.assertIn(expected, note)
        # 标签行绝不能出现双井号（标签重复加 #）；正文里的 ## 二级标题是合法 Markdown
        tag_line = note.split("\n\n", 1)[0]
        self.assertNotIn("##", tag_line)
        # dedup 被调用（登记），确认函数完整跑通
        self.assertTrue(mock_dedup.mark_summarized.called)


if __name__ == "__main__":
    unittest.main(verbosity=2)
