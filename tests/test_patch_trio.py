"""tests/test_patch_trio.py — 生成侧补丁 + ObsidianOutput 范围外修复（DECISION-20260907 拍板1）。

三个被测对象：
- Patch B  articles.main.generate_filename：落盘日期前缀强制
  （{YYYYMMDD}_{【分类】}{标题}.md，排序即时间线；未命名兜底不变）
- Patch A  videos.main：单视频入口闸门 is_summarized（P1-2，重复跑不再重复抓取落盘）
- Patch D  articles.obsidian.ObsidianOutput：补 ensure_folder_path / ensure_series_node /
  save(parent_token=)，签名对齐 feishu —— 修复 folder 路由下系列笔记静默跳过 vault 落盘
"""
import os
import re
import tempfile
import time
import unittest
from unittest import mock

import articles.main as articles_main
import videos.main as videos_main
from articles.obsidian import ObsidianOutput


# ── 通用 helper ──────────────────────────────────────────────────────────────


class _TmpMixin:
    def _make_tmp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return tmp.name


# ── Patch B：generate_filename 日期前缀 ──────────────────────────────────────


class TestGenerateFilenamePrefix(unittest.TestCase):

    def test_normal_no_category(self):
        name = articles_main.generate_filename("我的总结")
        self.assertRegex(name, r"^\d{8}_我的总结\.md$")

    def test_normal_with_category(self):
        name = articles_main.generate_filename("我的总结", category="投资")
        self.assertRegex(name, r"^\d{8}_【投资】我的总结\.md$")

    def test_publish_time_wins_over_now(self):
        ts = int(time.mktime(time.strptime("2026-01-02", "%Y-%m-%d")))
        name = articles_main.generate_filename("标题甲", publish_time=ts)
        self.assertTrue(name.startswith("20260102_"))

    def test_invalid_chars_replaced(self):
        name = articles_main.generate_filename("a/b:c*d")
        self.assertRegex(name, r"^\d{8}_a_b_c_d\.md$")

    def test_title_truncated_to_50(self):
        name = articles_main.generate_filename("字" * 80)
        m = re.match(r"^\d{8}_(.+)\.md$", name)
        self.assertIsNotNone(m)
        self.assertEqual(len(m.group(1)), 50)

    def test_short_title_falls_back_to_unnamed(self):
        name = articles_main.generate_filename("a")
        self.assertRegex(name, r"^未命名笔记-\d{8}-\d{6}\.md$")

    def test_empty_title_falls_back_to_unnamed(self):
        name = articles_main.generate_filename("")
        self.assertRegex(name, r"^未命名笔记-\d{8}-\d{6}\.md$")


# ── Patch D：ObsidianOutput 三方法 ───────────────────────────────────────────


class TestObsidianSeriesPaths(unittest.TestCase):

    def setUp(self):
        self.tmp = self._make_tmp()
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    _make_tmp = _TmpMixin._make_tmp

    def _out(self, vault=None):
        target = vault if vault is not None else self.vault
        with mock.patch.dict(os.environ, {"OBSIDIAN_VAULT_PATH": target}):
            return ObsidianOutput()

    def test_ensure_folder_path_creates_and_returns_deepest(self):
        out = self._out()
        got = out.ensure_folder_path(["【我的总结】", "作者", "奇衡DK-CAPITAL"])
        want = os.path.normpath(os.path.join(self.vault, "【我的总结】", "作者", "奇衡DK-CAPITAL"))
        self.assertEqual(os.path.normpath(got), want)
        self.assertTrue(os.path.isdir(want))

    def test_ensure_folder_path_empty_dirs(self):
        self.assertEqual(self._out().ensure_folder_path([]), "")

    def test_ensure_folder_path_skips_empty_segments(self):
        out = self._out()
        got = out.ensure_folder_path(["a", "", "b"])
        self.assertEqual(os.path.normpath(got), os.path.normpath(os.path.join(self.vault, "a", "b")))

    def test_ensure_folder_path_unavailable_vault(self):
        out = self._out(vault=os.path.join(self.tmp, "no_such_vault"))
        self.assertEqual(out.ensure_folder_path(["a"]), "")

    def test_ensure_series_node_under_parent(self):
        out = self._out()
        parent = os.path.join(self.vault, "up")
        os.makedirs(parent)
        got = out.ensure_series_node("千刀千法", parent_token=parent)
        self.assertEqual(os.path.normpath(got),
                         os.path.normpath(os.path.join(parent, "千刀千法")))
        self.assertTrue(os.path.isdir(got))

    def test_ensure_series_node_default_parent_is_vault(self):
        out = self._out()
        got = out.ensure_series_node("千刀千法")
        self.assertEqual(os.path.normpath(got),
                         os.path.normpath(os.path.join(self.vault, "千刀千法")))
        self.assertTrue(os.path.isdir(got))

    def test_save_with_parent_token_lands_in_dir(self):
        out = self._out()
        ser = out.ensure_series_node("千刀千法")
        self.assertTrue(out.save("内容", "第01集_开篇.md", parent_token=ser))
        p = os.path.join(ser, "第01集_开篇.md")
        self.assertTrue(os.path.isfile(p))
        with open(p, encoding="utf-8") as f:
            self.assertEqual(f.read(), "内容")

    def test_save_without_parent_token_lands_in_inbox(self):
        out = self._out()
        self.assertTrue(out.save("内容", "普通笔记.md"))
        self.assertTrue(os.path.isfile(os.path.join(self.vault, "【待归类】", "普通笔记.md")))

    def test_save_unavailable_returns_false(self):
        out = self._out(vault=os.path.join(self.tmp, "no_such_vault"))
        self.assertFalse(out.save("内容", "x.md", parent_token=self.vault))


# ── Patch A：单视频入口闸门 ──────────────────────────────────────────────────


class TestSingleVideoGate(unittest.TestCase):

    URL = "https://www.bilibili.com/video/BV1xx411c7mD"

    @mock.patch.object(videos_main, "is_summarized")
    @mock.patch.object(videos_main.fetch, "fetch_transcript")
    def test_hit_skips_and_never_fetches(self, mf, mi):
        mi.return_value = {"filename": "20260101_旧笔记.md", "title": "旧笔记"}
        r = videos_main._handle_single_video(self.URL, {})
        self.assertTrue(r["success"])
        self.assertTrue(r.get("skipped"))
        self.assertEqual(r["filename"], "20260101_旧笔记.md")
        mi.assert_called_once_with(url=self.URL)
        mf.assert_not_called()

    @mock.patch.object(videos_main, "is_summarized")
    @mock.patch.object(videos_main.fetch, "fetch_transcript")
    def test_hit_suppressed_still_skips(self, mf, mi):
        mi.return_value = {"filename": "20260101_旧笔记.md"}
        r = videos_main._handle_single_video(self.URL, {"force": False}, suppress=True)
        self.assertTrue(r.get("skipped"))
        mf.assert_not_called()

    @mock.patch.object(videos_main, "is_summarized")
    @mock.patch.object(videos_main.fetch, "fetch_transcript")
    def test_force_bypasses_gate(self, mf, mi):
        mf.return_value = ("标题", [], "")
        r = videos_main._handle_single_video(self.URL, {"force": True})
        mi.assert_not_called()
        mf.assert_called_once_with(self.URL)
        self.assertFalse(r["success"])

    @mock.patch.object(videos_main, "is_summarized")
    @mock.patch.object(videos_main.fetch, "fetch_transcript")
    def test_not_summarized_proceeds_to_fetch(self, mf, mi):
        mi.return_value = {}
        mf.return_value = ("标题", [("t", "字幕内容")], "作者")
        with mock.patch.object(videos_main, "_finalize_single",
                               return_value={"success": True}) as fin:
            r = videos_main._handle_single_video(self.URL, {})
        mi.assert_called_once()
        mf.assert_called_once_with(self.URL)
        fin.assert_called_once()
        self.assertTrue(r["success"])

    @mock.patch.object(videos_main, "is_summarized")
    @mock.patch.object(videos_main.fetch, "fetch_transcript")
    def test_empty_url_bypasses_gate(self, mf, mi):
        mf.return_value = ("标题", [], "")
        videos_main._handle_single_video("", {})
        mi.assert_not_called()


if __name__ == "__main__":
    unittest.main()
