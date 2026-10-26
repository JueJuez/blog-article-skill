"""S1（PLAN-20261026-low-cost-quality）：有界复审编排与最小统计。

这组用例保护的是「复审真的发生，而且账是清楚的」这一组约束：

1. **有界**：一批最多 N 轮独立审稿，必须给修订后重审留名额，至少留一个随机样本名额；
   没被选中的条目要写明原因，不能静默丢掉。
2. **不重复花钱**：同一稿同一版本已有有效结论（passed/failed）就不再派单、不再覆盖。
3. **不冒充通过**：缺源、改稿、解析失败、partial coverage、自检角色、未回收 —— 都不得登记为通过。
4. **统计分母分身份**：真错 / 误报 / 未确认 分列，未知消耗写 unknown 而不是 0。
5. **单一真源**：裁定写进既有 grounding 账本，不另起队列或第二套质量分。
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
def runner(tmp_path, monkeypatch, queue):
    from scripts import run_async_review as R
    monkeypatch.setattr(R, "IN_DIR", str(tmp_path / "in"))
    monkeypatch.setattr(R, "OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(R, "BATCH_DIR", str(tmp_path / "batches"))
    monkeypatch.setattr(R, "LEDGER", str(tmp_path / "ledger.jsonl"))
    return R


def _mk(tmp_path, name, reasons, *, source=True, note_text=None):
    """造一条真实可读的「稿 + 源」，入队后返回 (id, note_path)。"""
    note = tmp_path / f"{name}.md"
    note.write_text(note_text or f"# {name}\n\n正文一段。\n", encoding="utf-8")
    kw = {}
    if source:
        kw["source_text"] = f"源正文 {name}"
    res = AR.enqueue(str(note), reasons=reasons, title=name, note_path_rel=name, **kw)
    assert res["queued"] is True, res
    return res["id"], str(note)


def _payload(**kw):
    base = {"score": 90, "pass": True, "status": "complete", "coverage": "full",
            "reviewer_role": "independent_review_call", "issues": [], "findings": [],
            "claims": []}
    base.update(kw)
    return base


def _put_payload(R, rid, payload):
    os.makedirs(R.OUT_DIR, exist_ok=True)
    with open(os.path.join(R.OUT_DIR, f"{rid}.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)


# ---------------------------------------------------------------- 选单（有界 / 去重 / 可审计）

def test_plan_reserves_rerun_slot_and_one_random_sample(runner, queue, tmp_path):
    """5 轮预算里必须留 1 轮给可能的修订重审，并且至少 1 条普通随机样本。"""
    for i in range(8):
        _mk(tmp_path, f"n{i}", [f"证据待复核[tag{i}] 第{i + 2}行：具体疑点"])
    plan = runner.plan_batch(AR.pending(queue=queue), rounds=5, reserved_rerun=1,
                             random_slots=1, seed=7)
    assert len(plan["selected"]) == 3 + 1          # 风险档 3 + 随机档 1
    assert plan["budget"] == {"rounds": 5, "risk_slots": 3, "random_slots": 1,
                              "reserved_rerun": 1}
    assert sum(1 for s in plan["selected"] if s["sample"] == "random") == 1


def test_plan_prefers_specific_suspicion_over_generic_tag(runner, queue, tmp_path):
    """作者元数据缺失是系统性噪音，不能挤掉带具体行号的疑点。"""
    _mk(tmp_path, "generic", ["证据待复核[author_unverified] 第1行：源未提供明确作者元数据"])
    _mk(tmp_path, "specific", ["证据待复核[numeric_context] 第126行：保留数值邻文"])
    plan = runner.plan_batch(AR.pending(queue=queue), rounds=2, reserved_rerun=0,
                             random_slots=0, seed=1)
    assert len(plan["selected"]) == 2
    assert plan["selected"][0]["note_rel"] == "specific"


def test_plan_dedups_same_note_and_caps_same_tag(runner, queue, tmp_path):
    """同一篇稿只派一次；同一类风险最多占 per_tag_cap 个名额。"""
    note = tmp_path / "same.md"
    note.write_text("# v1\n\n正文\n", encoding="utf-8")
    AR.enqueue(str(note), source_text="源", reasons=["证据待复核[numeric_context] 第3行：x"],
               title="same")
    note.write_text("# v2\n\n正文改了\n", encoding="utf-8")
    AR.enqueue(str(note), source_text="源", reasons=["证据待复核[numeric_context] 第5行：y"],
               title="same")
    for i in range(3):
        _mk(tmp_path, f"c{i}", ["证据待复核[numeric_context] 第3行：重复类"])

    plan = runner.plan_batch(AR.pending(queue=queue), rounds=5, reserved_rerun=0,
                             random_slots=0, per_tag_cap=2, seed=3)
    notes = [s["note_rel"] for s in plan["selected"]]
    assert notes.count("same") == 1
    assert sum(1 for s in plan["selected"] if "numeric_context" in s["tags"]) == 2
    capped = [s for s in plan["not_selected"] if str(s["why"]).startswith("tag_cap")]
    assert capped, plan["not_selected"]


def test_plan_records_reason_for_every_unselected(runner, queue, tmp_path):
    for i in range(6):
        _mk(tmp_path, f"r{i}", [f"证据待复核[t{i}] 第{i + 2}行：疑点"])
    plan = runner.plan_batch(AR.pending(queue=queue), rounds=2, reserved_rerun=0,
                             random_slots=0, seed=1)
    assert len(plan["selected"]) == 2
    total = len(plan["selected"]) + len(plan["not_selected"])
    assert total == 6, "选中 + 未选中必须覆盖全部候选，不能静默丢条目"
    assert all(s["why"] for s in plan["not_selected"])


def test_plan_is_deterministic_with_seed_and_read_only(runner, queue, tmp_path):
    for i in range(6):
        _mk(tmp_path, f"d{i}", [f"证据待复核[t{i}] 第{i + 2}行：疑点"])
    recs = AR.pending(queue=queue)
    first = runner.plan_batch(recs, rounds=4, reserved_rerun=1, random_slots=1, seed=42)
    second = runner.plan_batch(recs, rounds=4, reserved_rerun=1, random_slots=1, seed=42)
    assert [s["id"] for s in first["selected"]] == [s["id"] for s in second["selected"]]
    # 选单只是读：不清积压、不改状态
    assert all(r["status"] == "pending" for r in AR.load(queue))


def test_plan_excludes_missing_source_and_covered_versions(runner, queue, tmp_path):
    _mk(tmp_path, "nosrc", ["source_thin"], source=False)
    rid, _ = _mk(tmp_path, "done", ["证据待复核[numeric_context] 第3行：x"])
    AR.update(rid, status="passed", result={"score": 91})
    rec = [r for r in AR.load(queue) if r["id"] == rid][0]
    # 同一版本再入队一条（hash 相同），不得再花钱审一遍
    AR.enqueue(rec["note_path"], content=open(rec["note_path"], encoding="utf-8").read(),
               source_text="源", reasons=["证据待复核[numeric_context] 第3行：x"])

    plan = runner.plan_batch(AR.pending(queue=queue), rounds=5, reserved_rerun=0,
                             random_slots=0, seed=1, all_records=AR.load(queue))
    whys = {s["note_rel"]: s["why"] for s in plan["not_selected"]}
    assert whys.get("nosrc") == "source_missing"
    assert whys.get("done") == "valid_conclusion_exists"
    assert plan["selected"] == []


# ---------------------------------------------------------------- 派单 / 回收（不重复、不冒充通过）

def test_emit_batch_only_dispatches_selected_and_writes_handoff(runner, queue, tmp_path):
    for i in range(5):
        _mk(tmp_path, f"e{i}", [f"证据待复核[t{i}] 第{i + 2}行：疑点"])
    plan = runner.plan_batch(AR.pending(queue=queue), rounds=3, reserved_rerun=1,
                             random_slots=0, seed=1)
    runner.cmd_emit(0, AR.pending(queue=queue), batch=plan)
    files = sorted(f for f in os.listdir(runner.IN_DIR) if f.endswith(".md"))
    dispatch = [f for f in files if not f.startswith("HANDOFF")]
    assert len(dispatch) == len(plan["selected"]) == 2
    handoff = [f for f in files if f.startswith("HANDOFF")]
    assert handoff, "必须给出编排交接单：发包不算已审"
    saved = runner.load_batch(plan["batch_id"])
    assert saved["state"] == "emitted" and len(saved["emitted"]) == 2
    assert saved["emitted"][0]["est_calls"] >= 1


def test_emit_skips_version_that_already_has_valid_conclusion(runner, queue, tmp_path):
    rid, note = _mk(tmp_path, "keep", ["证据待复核[t] 第3行：疑点"])
    plan = runner.plan_batch(AR.pending(queue=queue), rounds=2, reserved_rerun=0,
                             random_slots=0, seed=1)
    AR.update(rid, status="passed", result={"score": 93})
    runner.cmd_emit(0, AR.pending(queue=queue), batch=plan)
    assert not os.path.exists(os.path.join(runner.IN_DIR, f"{rid}.md"))
    saved = runner.load_batch(plan["batch_id"])
    assert any(s["why"] in ("already_concluded", "valid_conclusion_exists")
               for s in saved["skipped_at_emit"])
    assert AR.load(queue)[0]["status"] == "passed"


def test_ingest_partial_coverage_is_error_not_pass(runner, queue, tmp_path):
    rid, _ = _mk(tmp_path, "partial", ["证据待复核[t] 第3行：疑点"])
    _put_payload(runner, rid, _payload(coverage="partial", status="complete"))
    counters = runner.cmd_ingest(AR.pending(queue=queue))
    rec = AR.load(queue)[0]
    assert rec["status"] == "error"
    assert "coverage_partial" in json.dumps(rec["result"], ensure_ascii=False)
    assert counters["passed"] == 0


def test_ingest_self_check_role_is_error_not_pass(runner, queue, tmp_path):
    rid, _ = _mk(tmp_path, "selfcheck", ["证据待复核[t] 第3行：疑点"])
    _put_payload(runner, rid, _payload(reviewer_role="self_check"))
    runner.cmd_ingest(AR.pending(queue=queue))
    assert AR.load(queue)[0]["status"] == "error"


def test_ingest_never_overwrites_valid_conclusion(runner, queue, tmp_path):
    rid, _ = _mk(tmp_path, "locked", ["证据待复核[t] 第3行：疑点"])
    AR.update(rid, status="passed", result={"score": 95})
    _put_payload(runner, rid, _payload(score=10, **{"pass": False}))
    counters = runner.cmd_ingest(AR.load(queue))
    assert AR.load(queue)[0]["status"] == "passed"
    assert counters["already_concluded"] == [rid]


def test_ingest_missing_payload_stays_pending_and_is_reported(runner, queue, tmp_path):
    ids = [_mk(tmp_path, f"m{i}", [f"证据待复核[t{i}] 第3行：疑点"])[0] for i in range(2)]
    plan = runner.plan_batch(AR.pending(queue=queue), rounds=2, reserved_rerun=0,
                             random_slots=0, seed=1)
    runner.cmd_emit(0, AR.pending(queue=queue), batch=plan)
    _put_payload(runner, plan["selected"][0]["id"], _payload())
    counters = runner.cmd_ingest(AR.pending(queue=queue), batch=plan)
    assert len(counters["unrecovered"]) == 1
    assert {r["status"] for r in AR.load(queue)} == {"pending", "passed"}
    report = runner.write_report(os.path.join(tmp_path, "rep.md"), AR.stats(queue),
                                 AR.load(queue), batch=runner.load_batch(plan["batch_id"]),
                                 counters=counters)
    text = open(report, encoding="utf-8").read()
    assert "未回收" in text and "样本只代表被选批次" in text


def test_retry_error_reparses_without_spending_new_round(runner, queue, tmp_path):
    rid, _ = _mk(tmp_path, "retry", ["证据待复核[t] 第3行：疑点"])
    _put_payload(runner, rid, _payload(coverage="partial"))
    runner.cmd_ingest(AR.pending(queue=queue))
    assert AR.load(queue)[0]["status"] == "error"
    _put_payload(runner, rid, _payload())
    counters = runner.cmd_ingest(AR.load(queue), retry_errors=True)
    assert AR.load(queue)[0]["status"] == "passed"
    assert counters["retried"] == [rid]
    assert counters["rounds_used"] == 0, "重解析已有回包不产生新的审稿轮次"


# ---------------------------------------------------------------- 统计与裁定（分母分身份 / 单一账本）

def test_report_separates_true_error_false_positive_unconfirmed(runner, queue, tmp_path):
    rid, _ = _mk(tmp_path, "led", ["证据待复核[t] 第3行：疑点"])
    plan = runner.plan_batch(AR.pending(queue=queue), rounds=2, reserved_rerun=0,
                             random_slots=0, seed=1)
    runner.save_batch(plan)
    runner.append_verdict(plan["batch_id"], rid, verdict="真错", check="numeric_context",
                          finding="分母写错", action="revised", revisions=1, minutes=6,
                          note_sha256_before="a" * 64, note_sha256_after="b" * 64)
    runner.append_verdict(plan["batch_id"], rid, verdict="false_positive", check="author_unverified",
                          finding="单作者源", action="none", revisions=0, minutes=2)
    runner.append_verdict(plan["batch_id"], rid, verdict="unconfirmed", check="table_attribution",
                          finding="待核", action="pending", revisions=0)
    # 已确认真错但没修的，必须出现在「未解决项」里，不能被报告漏掉
    runner.append_verdict(plan["batch_id"], rid, verdict="true_error", check="omission",
                          finding="整段漏掉", action="pending", revisions=0)

    text = open(runner.write_report(os.path.join(tmp_path, "rep.md"), AR.stats(queue),
                                    AR.load(queue), batch=runner.load_batch(plan["batch_id"])),
                encoding="utf-8").read()
    assert "真错" in text and "误报" in text and "未确认" in text
    assert "不计入误报" in text
    # 误报率分母只算「已裁定 = 真错 + 误报」= 1+2=3，未确认不进分母
    assert "1 / 3" in text, text[text.find("误报率"):][:140]
    assert "已确认真错但本批未修订：1 项" in text
    assert "未回源核对（仍待确认）1 项" in text
    assert "unknown" in text and "not_exposed" in text
    assert "人工分钟" in text


def test_verdict_writes_only_to_existing_ledger(runner, tmp_path):
    runner.append_verdict("b1", "r1", verdict="true_error", check="numeric_context",
                          finding="x", action="none", revisions=0, minutes=None,
                          note_sha256_before="a" * 64, note_sha256_after="")
    rows = [json.loads(line) for line in open(runner.LEDGER, encoding="utf-8") if line.strip()]
    assert len(rows) == 1
    row = rows[0]
    for key in ("date", "batch", "id", "check", "finding", "verdict", "action",
                "revisions", "note_sha256_before", "note_sha256_after", "reviewer", "minutes"):
        assert key in row, key
    assert row["verdict"] == "true_error"
    # 未知人工分钟保留 unknown，不写 0 冒充
    assert row["minutes"] in (None, "unknown")


def test_verdict_rejects_unknown_value(runner):
    with pytest.raises(ValueError):
        runner.append_verdict("b1", "r1", verdict="看着还行", check="x", finding="",
                              action="none", revisions=0)


def test_batch_file_is_auditable(runner, queue, tmp_path):
    for i in range(3):
        _mk(tmp_path, f"a{i}", [f"证据待复核[t{i}] 第3行：疑点"])
    plan = runner.plan_batch(AR.pending(queue=queue), rounds=3, reserved_rerun=1,
                             random_slots=1, seed=5)
    path = runner.save_batch(plan)
    saved = json.load(open(path, encoding="utf-8"))
    assert saved["batch_id"] == plan["batch_id"]
    assert saved["budget"]["rounds"] == 3
    assert saved["created_at"]
    assert "not_selected" in saved and "selected" in saved
