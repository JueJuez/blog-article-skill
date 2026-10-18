"""固定评估集（T3）护栏：`docs/plans/PLAN-20261018-knowledge-layer.md` §6 A1。

守四件事（每一件都对应一个真实风险，不是凑数）：

1. **抽样可复现**：评估集一旦漂移，前后两次改动就不可比 → 同种子必须抽到同一批。
2. **分层有分辨率**：长源桶（>10k，召回衰减段）不能因为全库占比小就被抽成 0~1 条。
3. **条文关系式表单**：只有 `MUST_KEEP` 标记之间的清单行参与判分；区块外的行不能被算进去。
4. **未填不充数**：少于门槛条数的表单单独列 pending，不能带着 100% 混进统计。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import pytest  # noqa: E402

from shared import eval_set as ES  # noqa: E402


def _rows(n_short=10, n_long=3, offset=0):
    rows = []
    for i in range(n_short):
        rows.append({"title": f"s{i}", "source_path": f"/src/s{i}", "note_path": f"/n/s{i}",
                     "source_words": 1000 + i, "note_words": 500, "bucket": "0-2k",
                     "note_type": "structured" if i % 2 else "general"})
    for i in range(n_long):
        rows.append({"title": f"L{i}", "source_path": f"/src/L{i}", "note_path": f"/n/L{i}",
                     "source_words": 25000 + i + offset, "note_words": 8000, "bucket": ">20k",
                     "note_type": "interview"})
    return rows


class TestBucketing:
    def test_bucket_boundaries(self):
        assert ES.bucket_of(0) == "0-2k"
        assert ES.bucket_of(1999) == "0-2k"
        assert ES.bucket_of(2000) == "2-5k"
        assert ES.bucket_of(4999) == "2-5k"
        assert ES.bucket_of(5000) == "5-10k"
        assert ES.bucket_of(9999) == "5-10k"
        assert ES.bucket_of(10000) == "10-20k"
        assert ES.bucket_of(20000) == ">20k"
        assert ES.bucket_of(10 ** 7) == ">20k"

    def test_invalid_length_not_bucketed(self):
        assert ES.bucket_of(-1) == ""


class TestStratifiedSampling:
    def test_same_seed_same_sample(self):
        rows = _rows()
        a, plan_a = ES.stratify_sample(rows, per_bucket=3, seed=42)
        b, plan_b = ES.stratify_sample(rows, per_bucket=3, seed=42)
        assert [r["title"] for r in a] == [r["title"] for r in b]
        assert plan_a == plan_b

    def test_different_seed_differs(self):
        rows = _rows(n_short=40, n_long=10)
        a, _ = ES.stratify_sample(rows, per_bucket=10, seed=1)
        b, _ = ES.stratify_sample(rows, per_bucket=10, seed=2)
        assert [r["title"] for r in a] != [r["title"] for r in b]

    def test_long_bucket_keeps_resolution(self):
        """长源桶必须抽到 >= per_bucket 条（全库占比小不代表可忽略）。"""
        rows = _rows(n_short=600, n_long=20)
        picked, plan = ES.stratify_sample(rows, per_bucket=6, seed=7)
        assert plan[">20k"] == 6
        assert sum(1 for r in picked if r["bucket"] == ">20k") == 6

    def test_type_round_robin_spreads_types(self):
        rows = _rows(n_short=20, n_long=0)
        picked, _ = ES.stratify_sample(rows, per_bucket=6, seed=3)
        assert len({r["note_type"] for r in picked}) == 2

    def test_eval_id_assigned_sequentially(self):
        picked, _ = ES.stratify_sample(_rows(), per_bucket=3, seed=5)
        ids = [r["eval_id"] for r in picked]
        assert ids == [f"A{i:02d}" for i in range(1, len(picked) + 1)]

    def test_scarce_bucket_not_drained_and_shortage_reported(self):
        """稀缺桶不得被「富桶补抽」抽干；缺额如实上报而不是靠借样本凑数。"""
        rows = _rows(n_short=100, n_long=2)          # >20k 只有 2 条
        picked, plan = ES.stratify_sample(rows, per_bucket=6, seed=9)
        assert plan[">20k"] == 2
        assert sum(1 for r in picked if r["bucket"] == ">20k") == 2
        shortage = ES.allocate(rows, per_bucket=6)[1]
        assert shortage[">20k"] == 4


class TestFormRoundTrip:
    ROW = {
        "eval_id": "A01", "title": "标题", "bucket": "5-10k",
        "source_words": 6000, "note_words": 3000, "ratio": 0.5,
        "note_type": "structured", "folder": "F",
        "source_path": "/s.md", "note_path": "/n.md",
        "source_sha256": "ab" * 32, "note_sha256": "cd" * 32,
        "anchors": {"total": 4, "matched": 3, "missed": ["数:42"], "recall": 0.75},
        "form": {"signals": []}, "structure": {"missing": []},
        "verbatim": 0.01, "evidence_blockers": [],
    }

    def _write(self, path, body):
        path.write_text(ES.render_form(self.ROW).split(ES._MUST_BEGIN)[0]
                        + ES._MUST_BEGIN + "\n" + body + "\n"
                        + ES._MUST_END + "\n", encoding="utf-8")

    def test_parse_counts_only_checked_items(self, tmp_path):
        f = tmp_path / "A01.md"
        self._write(f, "- [x] 保留的一条\n- [ ] 丢掉的一条\n- [ ] 又丢一条")
        r = ES.parse_form(f.read_text(encoding="utf-8"))
        assert r["total"] == 3 and r["kept"] == 1
        assert r["retention"] == pytest.approx(1 / 3)

    def test_items_outside_marker_are_ignored(self, tmp_path):
        f = tmp_path / "A01.md"
        f.write_text(ES.render_form(self.ROW).replace(ES._MUST_BEGIN + "\n- [ ] \n- [ ] \n- [ ] \n",
                                                      ES._MUST_BEGIN + "\n- [x] 真条目\n")
                     + "- [x] 区块外的候选不能被算分\n", encoding="utf-8")
        r = ES.parse_form(f.read_text(encoding="utf-8"))
        assert r["total"] == 1 and r["kept"] == 1

    def test_blank_checkbox_not_counted(self, tmp_path):
        f = tmp_path / "A01.md"
        self._write(f, "- [x] 有内容\n- [ ]   \n- [ ] ")
        r = ES.parse_form(f.read_text(encoding="utf-8"))
        assert r["total"] == 1, "空白占位行不得被计入清单条数"

    def test_machine_hints_present_in_rendered_form(self):
        form = ES.render_form(self.ROW)
        assert "数:42" in form
        assert ES._MUST_BEGIN in form and ES._MUST_END in form


class TestOverwriteGuard:
    """重建评估集不得静默覆盖人工清单（`build_eval_set.py` 护栏；抽样可复现 ≠ 填写可重建）。"""

    def _write_form(self, path, items):
        path.write_text("# A01\n\n" + ES._MUST_BEGIN + "\n" + items + "\n"
                        + ES._MUST_END + "\n", encoding="utf-8")

    def test_fresh_template_not_touched(self, tmp_path):
        d = tmp_path / "forms"
        d.mkdir()
        self._write_form(d / "A01.md", "- [ ] \n- [ ] \n- [ ] ")
        assert ES.touched_forms(str(d)) == []

    def test_partial_form_is_touched(self, tmp_path):
        """只写了 1 条（未达统计门槛）也是人工劳动，必须保护。"""
        d = tmp_path / "forms"
        d.mkdir()
        self._write_form(d / "A01.md", "- [ ] 只写了一条也算")
        assert ES.touched_forms(str(d)) == ["A01.md"]

    def test_checked_items_detected(self, tmp_path):
        d = tmp_path / "forms"
        d.mkdir()
        self._write_form(d / "A02.md", "- [x] 保留\n- [ ] 丢掉")
        assert ES.touched_forms(str(d)) == ["A02.md"]

    def test_missing_dir_returns_empty(self, tmp_path):
        assert ES.touched_forms(str(tmp_path / "nope")) == []


class TestScoring:
    def _form(self, path, eid, items):
        body = "\n".join(f"- [{'x' if kept else ' '}] {t}" for t, kept in items)
        path.write_text(f"# {eid}\n\n" + ES._MUST_BEGIN + "\n" + body + "\n"
                        + ES._MUST_END + "\n", encoding="utf-8")

    @pytest.fixture()
    def forms(self, tmp_path):
        d = tmp_path / "forms"
        d.mkdir()
        self._form(d / "A01.md", "A01", [("a", True), ("b", True), ("c", False), ("d", True)])
        self._form(d / "A02.md", "A02", [("a", True), ("b", True)])
        self._form(d / "A03.md", "A03", [("a", True), ("b", True), ("c", True)])
        rows = {
            "A01": {"eval_id": "A01", "bucket": "0-2k", "note_type": "general",
                    "source_words": 1000, "note_words": 500, "anchors": {"recall": 0.5}},
            "A02": {"eval_id": "A02", "bucket": "0-2k", "note_type": "general",
                    "source_words": 1000, "note_words": 500, "anchors": {"recall": None}},
            "A03": {"eval_id": "A03", "bucket": ">20k", "note_type": "case",
                    "source_words": 30000, "note_words": 9000, "anchors": {"recall": 0.9}},
        }
        return d, rows

    def test_short_form_is_pending_not_scored(self, forms):
        d, rows = forms
        res = ES.score_forms(str(d), rows)
        # A02 只有 2 条 < MIN_ITEMS_PER_FORM(3) → 不进统计
        assert [r["eval_id"] for r in res["pending"]] == ["A02"]
        assert {r["eval_id"] for r in res["items"]} == {"A01", "A03"}

    def test_aggregate_retention(self, forms):
        d, rows = forms
        agg = ES.score_forms(str(d), rows)["aggregate"]
        assert agg["filled_forms"] == 2
        assert agg["total_items"] == 7
        assert agg["kept_items"] == 6
        assert agg["mean_retention"] == pytest.approx((0.75 + 1.0) / 2, abs=1e-3)
        assert agg["forms_below_target"] == 1          # A01 0.75 < 0.90

    def test_missing_items_reported(self, forms):
        d, rows = forms
        items = {r["eval_id"]: r for r in ES.score_forms(str(d), rows)["items"]}
        assert items["A01"]["missed_texts"] == ["c"]
        assert items["A03"]["missed_texts"] == []

    def test_empty_dir_no_crash(self, tmp_path):
        res = ES.score_forms(str(tmp_path / "nope"), {})
        assert res["aggregate"]["filled_forms"] == 0
        assert res["items"] == []
