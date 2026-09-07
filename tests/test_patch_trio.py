"""tests/test_patch_trio.py — 生成侧三补丁 + ObsidianOutput 范围外修复（DECISION-20260907 拍板1）。

四个被测对象：
- Patch B  articles.main.generate_filename：落盘日期前缀强制
  （{YYYYMMDD}_{【分类】}{标题}.md，排序即时间线；未命名兜底不变）
- Patch C  shared.series_naming 三纯函数：parse_landed_name / find_page_conflict /
  scan_landed_names —— 系列课「同集数不同标题」一致性校验的唯一真源（不受 force 影响）
- Patch A  videos.main：单视频入口闸门 is_summarized（P1-2，重复跑不再重复抓取落盘）
  + _handle_bilibili_series 集数冲突跳过 + 成功保存后 mark_done（直连路径增量闭环）
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
from shared import series_state
from shared import series_naming as sn


# ── 通用 helper ──────────────────────────────────────────────────────────────


class _TmpMixin:
    def _make_tmp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return tmp.name


class _StubOutputManager:
    """stub：无任何可用输出 → _local_write_enabled()=True 且外部同步循环空转。"""

    def __init__(self, *args, **kwargs):
        pass

    def get_available_outputs(self):
        return []


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


# ── Patch C 纯函数 1：parse_landed_name ──────────────────────────────────────


class TestParseLandedName(unittest.TestCase):
    """落盘成稿名解析：raw / body / 总览 / 垃圾名一律不认。"""

    def test_landed_with_md_suffix(self):
        self.assertEqual(sn.parse_landed_name("第01集_开篇.md"), (1, "开篇"))

    def test_landed_without_md_suffix(self):
        self.assertEqual(sn.parse_landed_name("第01集_开篇"), (1, "开篇"))

    def test_single_digit_page(self):
        self.assertEqual(sn.parse_landed_name("第3集_x"), (3, "x"))

    def test_raw_excluded(self):
        self.assertEqual(sn.parse_landed_name("第01集_开篇_raw.md"), (None, None))

    def test_body_excluded(self):
        self.assertEqual(sn.parse_landed_name("第01集_开篇.body.md"), (None, None))

    def test_overview_excluded(self):
        self.assertEqual(sn.parse_landed_name("00_系列总览.md"), (None, None))

    def test_random_name_excluded(self):
        self.assertEqual(sn.parse_landed_name("随手记.txt"), (None, None))

    def test_empty_and_none(self):
        self.assertEqual(sn.parse_landed_name(""), (None, None))
        self.assertEqual(sn.parse_landed_name(None), (None, None))

    def test_single_quote_preserved_in_part(self):
        self.assertEqual(
            sn.parse_landed_name("第02集_吸金的便士秤'Penny-Weight'"),
            (2, "吸金的便士秤'Penny-Weight'"))


# ── Patch C 纯函数 2：find_page_conflict ─────────────────────────────────────


class TestFindPageConflict(unittest.TestCase):
    """同页码 sanitize 后不同标题 → 冲突（Penny-Weight 病灶回归）。"""

    CONFLICT = "第02集_吸金的便士秤'Penny-Weight'"

    def test_conflict_hit(self):
        self.assertEqual(
            sn.find_page_conflict([self.CONFLICT], 2, "吸金的便士秤"), self.CONFLICT)

    def test_same_page_same_part_no_conflict(self):
        self.assertEqual(sn.find_page_conflict(["第02集_吸金的便士秤"], 2, "吸金的便士秤"), "")

    def test_other_page_no_conflict(self):
        self.assertEqual(sn.find_page_conflict(["第03集_吸金的便士秤"], 2, "吸金的便士秤"), "")

    def test_empty_existing(self):
        self.assertEqual(sn.find_page_conflict([], 1, "开篇"), "")

    def test_unparsable_entries_skipped(self):
        self.assertEqual(
            sn.find_page_conflict(["00_系列总览", "随手记.txt", "第01集_x_raw.md"], 1, "x"), "")

    def test_new_part_sanitized_before_compare(self):
        self.assertEqual(
            sn.find_page_conflict(["第02集_旧标题"], 2, "旧标题?"), "第02集_旧标题")

    def test_sanitize_equivalent_no_conflict(self):
        self.assertEqual(sn.find_page_conflict(["第02集_旧 标题"], 2, "旧  标题"), "")

    def test_empty_part_normalized(self):
        self.assertEqual(sn.find_page_conflict(["第02集_未命名"], 2, ""), "")

    def test_multiple_conflicts_returns_one(self):
        names = [self.CONFLICT, "第02集_另一标题"]
        self.assertIn(sn.find_page_conflict(names, 2, "吸金的便士秤"), names)


# ── Patch C 纯函数 3：scan_landed_names ──────────────────────────────────────


class TestScanLandedNames(unittest.TestCase):

    def setUp(self):
        self.tmp = self._make_tmp()

    _make_tmp = _TmpMixin._make_tmp

    def _mk(self, *names):
        d = os.path.join(self.tmp, f"dir{len(os.listdir(self.tmp))}")
        os.makedirs(d)
        for n in names:
            with open(os.path.join(d, n), "w", encoding="utf-8") as f:
                f.write("x")
        return d

    def test_mixed_files_filtered(self):
        d = self._mk("第01集_a.md", "第01集_a_raw.md", "第01集_a.body.md",
                     "00_系列总览.md", "随手记.txt", "第02集_b.md")
        self.assertEqual(sn.scan_landed_names([d]), ["第01集_a", "第02集_b"])

    def test_missing_dir_ignored(self):
        self.assertEqual(sn.scan_landed_names([os.path.join(self.tmp, "nope")]), [])

    def test_multiple_dirs_merged_dedup(self):
        d1 = self._mk("第01集_a.md")
        d2 = self._mk("第01集_a.md", "第02集_b.md")
        self.assertEqual(sn.scan_landed_names([d1, d2]), ["第01集_a", "第02集_b"])

    def test_empty_dir(self):
        self.assertEqual(sn.scan_landed_names([self._mk()]), [])

    def test_empty_and_none_dirs(self):
        self.assertEqual(sn.scan_landed_names([]), [])
        self.assertEqual(sn.scan_landed_names([None, ""]), [])


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


# ── Patch C 接入：系列直连路径集成 ───────────────────────────────────────────


class TestSeriesGateIntegration(unittest.TestCase):
    """_handle_bilibili_series：集数冲突跳过 + 成功保存/冲突跳过后 mark_done 闭环。"""

    FOLDER = "【我的总结】/作者/奇衡DK-CAPITAL"

    def setUp(self):
        self.tmp = self._make_tmp()
        self.notes = os.path.join(self.tmp, "notes")
        self.vault = os.path.join(self.tmp, "vault")
        self.state_path = os.path.join(self.tmp, "series_state.json")
        os.makedirs(self.notes)
        os.makedirs(self.vault)
        self.series_dir = os.path.join(self.notes, "千刀千法")
        os.makedirs(self.series_dir)
        with open(os.path.join(self.series_dir, "第02集_吸金的便士秤'Penny-Weight'.md"),
                  "w", encoding="utf-8") as f:
            f.write("旧稿")

    _make_tmp = _TmpMixin._make_tmp

    def _run(self):
        patches = [
            mock.patch.dict(os.environ, {"OBSIDIAN_VAULT_PATH": self.vault}),
            mock.patch.object(articles_main, "NOTES_DIR", self.notes),
            mock.patch.object(series_state, "STATE_PATH", self.state_path),
            # 桩内容须过 _save_series_note 机械门禁（general 硬下限=900 字），
            # 1600 字落在区间 (1500, 3000) 内，零 issue 零 warning
            mock.patch.object(videos_main, "_summarize_segments", return_value="总结文本" * 400),
            mock.patch.object(videos_main, "_generate_series_overview", return_value=None),
        ]
        with mock.patch.object(articles_main, "OutputManager", _StubOutputManager):
            for p in patches:
                p.start()
                self.addCleanup(p.stop)
            series = {
                "series_title": "千刀千法",
                "kind": "合集",
                "author": "奇衡DK-CAPITAL",
                "entries": [
                    {"page": 1, "part": "开篇", "segments": [], "title": "开篇"},
                    {"page": 2, "part": "吸金的便士秤", "segments": [], "title": "吸金的便士秤"},
                ],
            }
            return videos_main._handle_bilibili_series(
                "https://www.bilibili.com/video/BV1xx",
                {"folder": self.FOLDER}, series=series)

    def test_conflict_skipped_and_done_marked(self):
        r = self._run()
        self.assertTrue(r["success"])
        by_page = {x["page"]: x for x in r["results"]}
        self.assertEqual(by_page[2].get("conflict"), "第02集_吸金的便士秤'Penny-Weight'")
        self.assertFalse(os.path.isfile(os.path.join(self.series_dir, "第02集_吸金的便士秤.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.series_dir, "第01集_开篇.md")))
        self.assertTrue(series_state.is_done("千刀千法", "第01集_开篇"))
        self.assertTrue(series_state.is_done("千刀千法", "第02集_吸金的便士秤"))

    def test_second_run_reports_no_updates(self):
        self._run()
        r2 = self._run()
        self.assertEqual(r2["results"], [])
        self.assertIn("无更新", r2["message"])


if __name__ == "__main__":
    unittest.main()
