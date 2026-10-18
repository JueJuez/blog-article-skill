"""T7 ② 落盘后异步审核：入队（免费）/ 消费（花钱，手动）/ hash 过期保护。

设计要点（PLAN-20261018 §6 A3）：
- 审核**不参与**「能不能落盘」，只在落盘之后回答问题；
- 每条记录必须绑 note_sha256，稿变了 → 标 stale 丢弃结论（§10 第 4 项）；
- 入队失败只能是 no-op，绝不影响落盘。
"""
import json
import os

import pytest

from shared import async_review as AR


@pytest.fixture
def queue(tmp_path, monkeypatch):
    q = str(tmp_path / "q.jsonl")
    monkeypatch.setattr(AR, "QUEUE", q)
    monkeypatch.setattr(AR, "RESULT_DIR", str(tmp_path / "reviews"))
    return q


@pytest.fixture
def note(tmp_path):
    p = tmp_path / "笔记.md"
    p.write_text("# 标题\n\n正文一段。\n", encoding="utf-8")
    return str(p)


def _write_ok(note_path, **kw):
    return AR.enqueue(note_path, content=open(note_path, encoding="utf-8").read(),
                      source_text="源正文", **kw)


# ---------------------------------------------------------------- 入队

def test_enqueue_records_snapshot_and_hashes(queue, note):
    res = _write_ok(note, note_type="case", title="标题", reasons=["超参考值"])
    assert res["queued"] is True
    rec = AR.load(queue)[0]
    assert rec["status"] == "pending"
    assert rec["note_sha256"] == AR.file_digest(note)
    assert rec["source_sha256"] == AR.digest("源正文")
    # 源文本落快照：后台审的是「当时那份源」，不依赖 _raw_ 还在不在
    assert rec.get("source_snapshot") is True
    assert open(rec["source_path"], encoding="utf-8").read() == "源正文"
    assert rec["reasons"] == ["超参考值"]


def test_enqueue_dedups_pending(queue, note):
    assert _write_ok(note)["queued"] is True
    second = _write_ok(note)
    assert second["queued"] is False
    assert second["reason"] == "already_pending"
    assert len(AR.load(queue)) == 1


def test_enqueue_requires_note_hash(queue, tmp_path):
    """拿不到笔记内容就不入队：宁可漏审，也不审一份内容未知的稿。"""
    res = AR.enqueue(str(tmp_path / "不存在.md"))
    assert res["queued"] is False
    assert res["reason"] == "no_content"


def test_maybe_enqueue_env_gate(queue, note, monkeypatch):
    monkeypatch.setenv("NOTE_ASYNC_REVIEW", "0")
    assert AR.maybe_enqueue(note)["reason"] == "disabled"

    monkeypatch.setenv("NOTE_ASYNC_REVIEW", "1")

    class _No:
        def random(self):
            return 0.99

    assert AR.maybe_enqueue(note, rng=_No())["reason"] == "not_sampled"
    assert AR.maybe_enqueue(note, reasons=["超参考值"], rng=_No())["queued"] is True


def test_maybe_enqueue_all_mode(queue, note, monkeypatch):
    monkeypatch.setenv("NOTE_ASYNC_REVIEW", "all")
    assert AR.maybe_enqueue(note)["queued"] is True


# ---------------------------------------------------------------- 状态更新

def test_update_is_atomic_and_keeps_records(queue, note):
    rid = _write_ok(note)["id"]
    _write_ok(os.path.join(os.path.dirname(note), "另一篇.md")) if False else None
    assert AR.update(rid, status="failed", result={"score": 40}) is True
    rec = AR.load(queue)[0]
    assert rec["status"] == "failed"
    assert rec["result"]["score"] == 40
    assert not os.path.exists(queue + ".tmp")
    assert AR.update("不存在", status="passed") is False
    with pytest.raises(ValueError):
        AR.update(rid, status="乱写")


def test_stats_counts(queue, note):
    rid = _write_ok(note)["id"]
    AR.update(rid, status="passed")
    s = AR.stats(queue)
    assert s["passed"] == 1 and s["pending"] == 0 and s["total"] == 1


# ---------------------------------------------------------------- 消费端

@pytest.fixture
def runner(tmp_path, monkeypatch, queue):
    from scripts import run_async_review as R
    monkeypatch.setattr(R, "IN_DIR", str(tmp_path / "in"))
    monkeypatch.setattr(R, "OUT_DIR", str(tmp_path / "out"))
    return R


def test_emit_marks_stale_when_note_changed(runner, queue, note):
    _write_ok(note)
    with open(note, "a", encoding="utf-8") as fh:
        fh.write("事后又改了一段。\n")
    rc = runner.cmd_emit(1, AR.pending(queue=queue))
    assert rc == 0
    assert AR.load(queue)[0]["status"] == "stale"


def test_emit_marks_skipped_when_source_missing(runner, queue, tmp_path):
    note = tmp_path / "n.md"
    note.write_text("# 标题\n\n正文\n", encoding="utf-8")
    AR.enqueue(str(note), content=note.read_text(encoding="utf-8"))  # 无 source
    runner.cmd_emit(1, AR.pending(queue=queue))
    assert AR.load(queue)[0]["status"] == "skipped"


def test_emit_writes_dispatch_with_note_and_source(runner, queue, note):
    _write_ok(note)
    runner.cmd_emit(1, AR.pending(queue=queue))
    rec = AR.load(queue)[0]
    body = open(os.path.join(runner.IN_DIR, f"{rec['id']}.md"), encoding="utf-8").read()
    assert rec["note_sha256"] in body
    assert "正文一段。" in body and "源正文" in body
    assert rec["status"] == "pending"


def test_ingest_accepts_and_marks(runner, queue, note):
    rec_id = _write_ok(note)["id"]
    os.makedirs(runner.OUT_DIR, exist_ok=True)
    payload = {"score": 90, "pass": True, "status": "complete", "coverage": "full",
               "reviewer_role": "independent_review_call", "issues": [],
               "findings": [], "claims": []}
    with open(os.path.join(runner.OUT_DIR, f"{rec_id}.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    runner.cmd_ingest(AR.pending(queue=queue))
    rec = AR.load(queue)[0]
    assert rec["status"] == "passed"
    assert rec["result"]["score"] == 90


def test_ingest_rejects_invalid_payload(runner, queue, note):
    rec_id = _write_ok(note)["id"]
    os.makedirs(runner.OUT_DIR, exist_ok=True)
    with open(os.path.join(runner.OUT_DIR, f"{rec_id}.json"), "w", encoding="utf-8") as fh:
        fh.write("随便写点不是 JSON 的东西")
    runner.cmd_ingest(AR.pending(queue=queue))
    assert AR.load(queue)[0]["status"] == "error"


def test_ingest_drops_conclusion_if_note_changed(runner, queue, note):
    """后台审的这段时间里稿被重做过 → 结论作废，不许冒充当前稿。"""
    rec_id = _write_ok(note)["id"]
    os.makedirs(runner.OUT_DIR, exist_ok=True)
    payload = {"score": 95, "pass": True, "status": "complete", "coverage": "full",
               "reviewer_role": "independent_review_call", "issues": [], "findings": [], "claims": []}
    with open(os.path.join(runner.OUT_DIR, f"{rec_id}.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    with open(note, "a", encoding="utf-8") as fh:
        fh.write("改过。\n")
    runner.cmd_ingest(AR.pending(queue=queue))
    assert AR.load(queue)[0]["status"] == "stale"
