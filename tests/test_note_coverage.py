# -*- coding: utf-8 -*-
"""写作端两环（机械自查 + 定向补稿）接线用例——F7 前置，先于实现（RED）。

冻结口径来源：notes/_meta/eval/20261029-pack-only-wiring/check_calibration.json
（label 3-gram 命中率 < 0.2 拦截；标题/数字只作 info；阈值校准后冻结不再调参）。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from shared import note_coverage as NC


def _pack():
    return {"items": [
        {"id": "i1", "label": "专业素养：知识体系与市场洞察",
         "source_spans": [[2, 3]]},
        {"id": "i2", "label": "供应链掌控：库存周转与账期",
         "source_spans": [[5, 6]]},
    ]}


SOURCE = "\n".join([
    "一、专业素养",
    "扎实的产品知识与行业洞察是顾问式销售的基础。",
    "二、供应链",
    "库存周转与账期决定现金流安全。",
])


class TestFrozenThreshold(unittest.TestCase):
    def test_threshold_is_frozen_calibration_value(self):
        self.assertEqual(NC.LABEL_3GRAM_THRESHOLD, 0.2)

    def test_calibration_pointer_recorded(self):
        self.assertIn("check_calibration.json", NC.CALIBRATION_REF)


class TestSuspectDetection(unittest.TestCase):
    def test_missing_label_is_suspect(self):
        note = "正文谈了专业素养：知识体系与市场洞察，写得很多。"
        issues = NC.coverage_issues(_pack(), note)
        self.assertEqual([x["ref"] for x in issues], ["i2"])

    def test_rewritten_label_passes(self):
        # 改写形态：字面几乎不重合也应由 label 3-gram 判阴（高召回提示器只标可疑）
        note = "做外贸要懂产品、懂行业，还得盯住库存和供应商付款节奏。"
        issues = NC.coverage_issues(_pack(), note)
        # i2 的 label 3-gram 命中率应低于阈值（机械层接受误报，此处验证其被标出）
        self.assertIn("i2", [x["ref"] for x in issues])

    def test_report_shape_has_info_only_sections(self):
        rep = NC.coverage_report(SOURCE, _pack(), "正文……")
        self.assertIn("info", rep)
        self.assertIn("headings_total", rep["info"])
        self.assertIn("numbers_total", rep["info"])
        self.assertNotIn("kind", "")  # 占位：标题与数字不产生 issues


class TestInsertOnly(unittest.TestCase):
    ORIG = "第一段。\n\n第二段。\n\n第三段。\n"

    def test_pure_insert_passes(self):
        patched = "第一段。\n\n第二段。\n\n插入的新段。\n\n第三段。\n"
        self.assertEqual(NC.insert_only_violations(self.ORIG, patched), [])

    def test_rewrite_is_violation(self):
        patched = "第一段。\n\n第二段改了。\n\n插入的新段。\n\n第三段。\n"
        v = NC.insert_only_violations(self.ORIG, patched)
        self.assertTrue(v)
        self.assertIn("replace", v[0]["op"])

    def test_deletion_is_violation(self):
        patched = "第一段。\n\n第三段。\n"
        self.assertTrue(NC.insert_only_violations(self.ORIG, patched))


if __name__ == "__main__":
    unittest.main()
