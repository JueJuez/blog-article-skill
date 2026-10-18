# -*- coding: utf-8 -*-
"""T8 受控替换：A/B 判定纯函数 + resum_save_batch 接线回归。

钉三件事：
1. **判定保守**：只有「确定性缺陷更少」才替换；平手/更差保留旧稿；force 无条件替换。
2. **两种结局都有留痕**：候选稿在替换判定前先暂存；决策回写暂存 meta + 台账 JSONL。
3. **接线端到端**：保留旧稿时旧稿不动、不保存；改善时照常走保存链路。
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SOURCE = "占比 30%、占比 45%、投入 100万元、回报 200万元，这是四个核心数字口径。"
OLD_BAD = "旧内容没有任何数字。"
OLD_CLEAN = "旧内容是一段正常文字，本来就没有数字要求。"

NEW_CLEAN = ("口径如下：30%、45%、100万元、200万元按原样保留。"
             + "这是一段用于回归测试的正文，长度必须超过机械门禁的内容缺失下限。" * 12
             + "\n\n【核心主题词】回归测试 | 受控替换 | 落盘\n")


def _m(n):
    """造一份 metrics（只关心缺陷数）。"""
    return {"content_blockers": ["缺陷%d" % i for i in range(n)], "evidence_blockers": [], "defect_count": n}


class TestDecide:
    def test_no_old_replaces(self):
        from shared import controlled_replace as CR
        d = CR.decide(None, _m(3))
        assert d["replace"] is True and d["reason"] == "no_old"

    def test_improved_replaces(self):
        from shared import controlled_replace as CR
        d = CR.decide(_m(2), _m(1))
        assert d["replace"] is True and d["reason"] == "improved"

    def test_regressed_keeps_old(self):
        from shared import controlled_replace as CR
        d = CR.decide(_m(1), _m(2))
        assert d["replace"] is False and d["reason"] == "regressed"

    def test_tie_keeps_old(self):
        from shared import controlled_replace as CR
        for a, b in ((0, 0), (2, 2)):
            d = CR.decide(_m(a), _m(b))
            assert d["replace"] is False and d["reason"] == "no_gain", (a, b)

    def test_force_replaces_regardless(self):
        from shared import controlled_replace as CR
        d = CR.decide(_m(0), _m(5), force=True)
        assert d["replace"] is True and d["reason"] == "force"


class TestAssessText:
    def test_clean_text_zero_defects(self):
        from shared import controlled_replace as CR
        assert CR.assess_text("干净的正文，没有数字。", "", "")["defect_count"] == 0

    def test_missing_anchors_flagged(self):
        from shared import controlled_replace as CR
        r = CR.assess_text(OLD_BAD, SOURCE, "")
        assert r["defect_count"] >= 1
        assert any("锚点召回" in x for x in r["content_blockers"])

    def test_faithful_text_clean(self):
        from shared import controlled_replace as CR
        r = CR.assess_text("口径如下：30%、45%、100万元、200万元。", SOURCE, "")
        assert r["defect_count"] == 0

    def test_metrics_from_gate_shape_matches(self):
        """门禁结果与 assess_text 同形状，可直接进 decide（接线依赖这一点）。"""
        from shared import controlled_replace as CR
        from prompts.verifier import verify_note_mechanical
        gate = verify_note_mechanical(NEW_CLEAN, "", source_text=SOURCE)
        m = CR.metrics_from_gate(gate)
        assert set(m) == {"content_blockers", "evidence_blockers", "defect_count"}
        assert m["defect_count"] == len(m["content_blockers"]) + len(m["evidence_blockers"])


class TestLedger:
    def test_log_and_read(self, tmp_path, monkeypatch):
        from shared import controlled_replace as CR
        monkeypatch.setattr(CR, "LEDGER_BASE", str(tmp_path / "decisions.jsonl"))
        path = CR.log_decision({"action": "keep", "reason": "no_gain", "title": "t"})
        assert path and os.path.exists(path)
        rows = CR.read_ledger(str(tmp_path / "decisions.jsonl"))
        assert len(rows) == 1 and rows[0]["action"] == "keep"

    def test_log_never_raises(self, tmp_path, monkeypatch):
        from shared import controlled_replace as CR
        blocker = tmp_path / "not_a_dir"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setattr(CR, "LEDGER_BASE", str(blocker / "sub" / "decisions.jsonl"))
        assert CR.log_decision({"action": "x"}) == ""


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """隔离暂存区 / 台账 / 审查队列；假保存函数记录调用。"""
    from shared import controlled_replace as CR
    from shared import draft_stage as DS
    monkeypatch.setattr(DS, "STAGE_BASE", str(tmp_path / "staged"))
    monkeypatch.setattr(CR, "LEDGER_BASE", str(tmp_path / "decisions.jsonl"))
    import prompts.review_rubric as RR
    monkeypatch.setattr(RR, "queue_for_review", lambda *a, **k: None)
    import articles.main as AM
    calls = {"save": []}
    monkeypatch.setattr(AM, "save_summarized_article",
                        lambda content, **kw: (calls["save"].append(kw) or ("formed", "fname.md")))
    return tmp_path, calls


def _run_batch(tmp_path, monkeypatch, old_text, new_text, source_path="", extra_flags=None):
    from scripts import resum_save_batch as R

    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)
    note_path = tmp_path / "vault" / "20260101_受控替换.md"
    note_path.parent.mkdir(exist_ok=True)
    note_path.write_text(old_text, encoding="utf-8")
    (out_dir / "batch_t_00.md").write_text(new_text, encoding="utf-8")

    item = {"title": "受控替换", "url": "https://example.com/x", "author": "作者",
            "note_type": "", "note_path": str(note_path), "source_path": source_path}
    batch_path = tmp_path / "batch_t.json"
    batch_path.write_text(json.dumps([item], ensure_ascii=False), encoding="utf-8")

    argv = ["resum_save_batch.py", str(batch_path), str(out_dir)] + list(extra_flags or [])
    monkeypatch.setattr(sys, "argv", argv)
    rc = R.main()
    return rc, note_path


class TestWiring:
    def test_keeps_old_when_no_gain(self, tmp_path, monkeypatch, sandbox):
        """旧稿干净、新稿也干净 → 平手保留旧稿；候选暂存 + 台账都有。"""
        rc, note_path = _run_batch(tmp_path, monkeypatch, OLD_CLEAN, NEW_CLEAN)
        _, calls = sandbox
        assert rc == 0, "保留旧稿是正常结局，不是失败"
        assert calls["save"] == [], "平手时不得调用保存"
        assert note_path.read_text(encoding="utf-8") == OLD_CLEAN, "旧稿必须原样不动"

        from shared import controlled_replace as CR
        from shared import draft_stage as DS
        rows = CR.read_ledger(str(tmp_path / "decisions.jsonl"))
        assert rows and rows[0]["action"] == "keep" and rows[0]["reason"] == "no_gain"
        staged = DS.list_staged()
        assert len(staged) == 1, "候选稿必须已暂存（明确更优前先留档）"
        meta = DS.read_meta(staged[0])
        assert meta["reason"] == "redo_candidate"
        assert meta["outcome"] == "kept_old"
        assert meta["replace_decision"]["reason"] == "no_gain"

    def test_replaces_when_improved(self, tmp_path, monkeypatch, sandbox):
        """旧稿有确定性缺陷（锚点全丢）、新稿干净 → 替换并落台账。"""
        src = tmp_path / "src.md"
        src.write_text(SOURCE, encoding="utf-8")
        rc, _ = _run_batch(tmp_path, monkeypatch, OLD_BAD, NEW_CLEAN, source_path=str(src))
        _, calls = sandbox
        assert rc == 0
        assert len(calls["save"]) == 1, "确有改善时必须走保存链路"
        from shared import controlled_replace as CR
        rows = CR.read_ledger(str(tmp_path / "decisions.jsonl"))
        assert rows and rows[0]["action"] == "replace" and rows[0]["reason"] == "improved"
        assert rows[0]["old_defects"] >= 1 and rows[0]["new_defects"] == 0

    def test_force_replaces_clean_old(self, tmp_path, monkeypatch, sandbox):
        """--force：逃生舱，跳过比较无条件替换（如模板升级的定向重做）。"""
        rc, _ = _run_batch(tmp_path, monkeypatch, OLD_CLEAN, NEW_CLEAN, extra_flags=["--force"])
        _, calls = sandbox
        assert rc == 0
        assert len(calls["save"]) == 1
        from shared import controlled_replace as CR
        rows = CR.read_ledger(str(tmp_path / "decisions.jsonl"))
        assert rows and rows[0]["reason"] == "force"
