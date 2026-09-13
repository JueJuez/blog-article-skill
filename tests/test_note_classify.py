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

S_XP_READING = (
    "# 万字拆解《高效能人士的7个习惯》\n\n"
    "**30秒速览**：本文拆解《高效能人士的7个习惯》这本经典书，讲个人成长与高效习惯养成，属于读书笔记。\n\n"
    "## 一、书籍背景\n《高效能人士的7个习惯》是史蒂芬·柯维……"
)

S_XP_OPINION = (
    "# 节后跳槽指南！如何跳出高薪？\n\n"
    "**30秒速览**：本文讲职场人节后如何跳槽、谈薪、跳出高薪低成长的陷阱，属于观点随笔。\n\n"
    "## 一、跳槽时机\n节后跳槽要注意……"
)


class TestInferSemanticTags(unittest.TestCase):
    def test_invest_pig_submap(self):
        tags = infer_semantic_tags(
            S_INVEST, folder="【监控】/B站/价投小猪仔/小猪仔拆公司",
            author="价投小猪仔", note_type="structured")
        self.assertIn("投资/公司分析", tags)
        self.assertIn("伊利股份", tags)
        self.assertIn("教学可用", tags)
        self.assertIn("结构化复盘", tags)
        self.assertNotIn("来源/", " ".join(tags))
        for t in tags:
            if t.startswith("投资"):
                self.assertIn("/", t)
                self.assertNotIn("#", t)

    def test_business_virtual_product(self):
        tags = infer_semantic_tags(
            S_BUSINESS, folder="生财有术/虚拟产品",
            author="生财有术", note_type="case")
        self.assertIn("商业/虚拟产品", tags)
        self.assertIn("小红书", tags)
        self.assertIn("虚拟产品", tags)
        self.assertIn("素材可用", tags)
        self.assertIn("方法论可复用", tags)
        self.assertIn("案例拆解", tags)

    def test_ai_datwhale(self):
        tags = infer_semantic_tags(
            S_AI, folder="【我的总结】/作者/Datawhale",
            author="Datawhale", note_type="structured")
        self.assertIn("AI科技/AI编程", tags)
        self.assertIn("Claude Code", tags)
        self.assertNotIn("来源/", " ".join(tags))

    def test_content_creation(self):
        tags = infer_semantic_tags(
            S_CONTENT, folder="生财有术/自媒体",
            author="生财有术", note_type="dissection")
        self.assertIn("内容创作/平台运营", tags)
        self.assertIn("小红书", tags)
        self.assertIn("创作解剖", tags)

    def test_inbox_fallback_no_domain_when_comprehensive(self):
        tags = infer_semantic_tags(S_PSYCH, folder="【待归类】", author="")
        domain_tags = [t for t in tags if "/" in t]
        self.assertEqual(domain_tags, ["综合/未分类"])
        self.assertIn("MBTI", tags)
        self.assertIn("心理学", tags)

    def test_author_archived_xiapeng_not_invest(self):
        # 按作者归档的笔记（夏鹏本鹏讲读书/职场）不得被错归投资
        tags = infer_semantic_tags(
            S_XP_READING, folder="【我的总结】/作者/夏鹏本鹏",
            author="夏鹏本鹏", note_type="reading")
        domain = [t for t in tags if "/" in t]
        self.assertEqual(domain, ["个人成长/读书方法"])
        self.assertNotIn("投资", [t.split("/", 1)[0] for t in domain])

        tags2 = infer_semantic_tags(
            S_XP_OPINION, folder="【我的总结】/作者/夏鹏本鹏",
            author="夏鹏本鹏", note_type="opinion")
        domain2 = [t for t in tags2 if "/" in t]
        self.assertEqual(domain2, ["个人成长/职场"])
        self.assertNotIn("来源/", " ".join(tags2))

    def test_no_duplicate_and_no_slash_in_topic(self):
        tags = infer_semantic_tags(S_INVEST, folder="【监控】/B站/价投小猪仔/小猪仔拆公司",
                                   author="价投小猪仔", note_type="structured")
        self.assertEqual(len(tags), len(set(tags)))


class TestCategoryRouting(unittest.TestCase):
    def test_skip_namespace_tags(self):
        self.assertEqual(category_from_tags(["文章总结", "投资/公司分析", "来源/价投小猪仔"]), "")
        # 注：topic/用途 现已裸标签化；裸用途会被当分类（用户手贴意图），隔离在 save 层（注入前锁定 category）
        self.assertEqual(category_from_tags(["投资/公司分析", "教学可用"]), "教学可用")

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
            tags=["投资/公司分析", "伊利股份"],
            add_metadata=True)
        self.assertIn("#投资/公司分析", out)
        self.assertIn("#伊利股份", out)


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
        # 四个维度标签都进笔记（注意：#文章总结/#转载/#来源/#更早 均已不再生成；类型/用途/topic 均为裸标签）
        for expected in ("#投资/公司分析", "#伊利股份", "#教学可用",
                         "#结构化复盘"):
            self.assertIn(expected, note)
        self.assertNotIn("#更早", note)  # 时效标签已移除
        # 标签行绝不能出现双井号（标签重复加 #）；正文里的 ## 二级标题是合法 Markdown
        tag_line = note.split("\n\n", 1)[0]
        self.assertNotIn("##", tag_line)
        # dedup 被调用（登记），确认函数完整跑通
        self.assertTrue(mock_dedup.mark_summarized.called)

    @unittest.skipUnless(_HAS_SAVE, "articles.main 不可导入（依赖缺失）")
    def test_save_removes_redundant_author(self):
        # 模拟消费队列：入队预填 tags=["夏鹏本鹏"]（裸作者），验证落盘后：
        # ① 裸「夏鹏本鹏」被移除（作者已在文件夹路径，path: 可聚合，裸标签纯噪声）
        # ② 领域=个人成长（不再错归投资）③ 不生成 #文章总结/#转载/#来源/ 这类无信息量/重复标签
        with patch("articles.main.OutputManager", FakeManager), \
             patch("articles.main.dedup") as mock_dedup:
            mock_dedup.mark_summarized = MagicMock()
            note, fn = save_summarized_article(
                S_XP_READING, original_url="http://example.com/xp", author="夏鹏本鹏",
                tags=["夏鹏本鹏"], original_title="万字拆解高效能人士的7个习惯",
                note_type="reading",
                folder="【我的总结】/作者/夏鹏本鹏", publish_time=0)
        tag_line = note.split("\n", 1)[0].strip()
        tokens = [t[1:] for t in tag_line.split() if t.startswith("#")]
        self.assertNotIn("夏鹏本鹏", tokens)          # 裸作者已被移除
        self.assertIn("个人成长/读书方法", tokens)    # 领域正确（不再错归投资）
        self.assertNotIn("投资", [t.split("/")[0] for t in tokens if "/" in t])
        self.assertNotIn("来源/", " ".join(tokens))    # 来源标签已移除
        self.assertNotIn("文章总结", tokens)           # 无信息量标签已停生成
        self.assertNotIn("转载", tokens)

    @unittest.skipUnless(_HAS_SAVE, "articles.main 不可导入（依赖缺失）")
    def test_save_locks_category_from_auto_tags(self):
        # 路由隔离回归：folder 为空时，自动注入的裸 topic/用途 不得抢「分类」污染文件名【分类】前缀。
        # （裸标签 #伊利股份 / #教学可用 若被 category_from_tags 误当分类，会生成 【伊利股份】拆解伊利股份.md）
        with patch("articles.main.OutputManager", FakeManager), \
             patch("articles.main.dedup") as mock_dedup:
            mock_dedup.mark_summarized = MagicMock()
            note, fn = save_summarized_article(
                S_INVEST, original_url="http://example.com/x", author="价投小猪仔",
                tags=[], original_title="拆解伊利股份", note_type="structured",
                folder="", publish_time=0)  # folder 空 -> 走文件名分类前缀逻辑
        self.assertNotIn("【伊利股份】", fn)
        self.assertNotIn("【教学可用】", fn)
        self.assertNotIn("【", fn)  # 原始 tags 空且 folder 空，category 锁定为空，无【分类】前缀


class TestTopicBlock(unittest.TestCase):
    def test_extract_and_strip(self):
        from shared.note_classify import extract_and_strip_topics
        content = "# 标题\n正文内容……\n\n【核心主题词】复利 | 护城河 | 价值投资 | 长期主义\n"
        cleaned, topics = extract_and_strip_topics(content)
        self.assertEqual(topics, ["复利", "护城河", "价值投资", "长期主义"])
        self.assertNotIn("【核心主题词】", cleaned)
        self.assertNotIn("复利", cleaned)

    def test_extract_dedupe_and_cap(self):
        from shared.note_classify import extract_and_strip_topics
        content = "x\n【核心主题词】A、B、A、C、D、E、F、G\n"  # 7 个不同 -> 去重后截断 5
        cleaned, topics = extract_and_strip_topics(content)
        self.assertEqual(topics, ["A", "B", "C", "D", "E"])
        self.assertEqual(len(topics), 5)

    def test_extract_with_spaces(self):
        from shared.note_classify import extract_and_strip_topics
        content = "x\n【核心主题词】Claude Code | 护城河\n"
        cleaned, topics = extract_and_strip_topics(content)
        self.assertIn("Claude Code", topics)  # 含空格词不被拆断

    def test_topics_param_used_and_capped(self):
        tags = infer_semantic_tags(
            S_INVEST, folder="【监控】/B站/价投小猪仔/小猪仔拆公司",
            author="价投小猪仔", note_type="structured",
            topics=["复利", "护城河", "价值投资", "长期主义", "认知", "多余词"])
        bare = [t for t in tags if "/" not in t]  # 裸标签 = topic + 用途
        for t in ("复利", "护城河", "价值投资", "长期主义", "认知"):
            self.assertIn(t, bare)              # topics 参数优先
        self.assertNotIn("多余词", bare)        # 截断到 5
        self.assertIn("投资/公司分析", tags)    # 领域仍由代码推导

    def test_topics_block_stripped_in_save(self):
        # 端到端：save_summarized_article 从正文剥离【核心主题词】并转为 #topic/，正文不再残留标记
        body = S_INVEST + "\n\n【核心主题词】伊利股份 | 护城河 | 价值投资\n"
        with patch("articles.main.OutputManager", FakeManager), \
             patch("articles.main.dedup") as mock_dedup:
            mock_dedup.mark_summarized = MagicMock()
            note, fn = save_summarized_article(
                body, original_url="http://example.com/x", author="价投小猪仔",
                tags=[], original_title="拆解伊利股份", note_type="structured",
                folder="【监控】/B站/价投小猪仔/小猪仔拆公司", publish_time=0)
        self.assertIn("#护城河", note)
        self.assertIn("#价值投资", note)
        self.assertNotIn("【核心主题词】", note)  # 区块已从正文剥离


if __name__ == "__main__":
    unittest.main(verbosity=2)
