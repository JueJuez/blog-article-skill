"""两阶段直接落盘：默认派单、待提炼状态、流程刷新与只读覆盖诊断。"""
import json
import sys
from pathlib import Path

from conftest import v6ize
from prompts import distill as DT
from prompts.provenance import dispatch_fields, text_hash
from shared import distill_dispatch as DD, distill_pack as DP
from scripts import filter_pending as FP, check_note_coverage as CHECK


def _inputs(tmp_path, monkeypatch):
    src = ("完整论证与条件应随原文保留，这里提供非数字密集的长源。\n" * 600)
    raw = tmp_path / "raw.md"
    raw.write_text(src, encoding="utf-8")
    store = tmp_path / "packs"
    store.mkdir()
    monkeypatch.setattr(DP, "PACKS_DIR", str(store))
    monkeypatch.setenv("NOTE_DISTILL_DISPATCH_LOG", str(tmp_path / "dispatch.jsonl"))
    monkeypatch.delenv("NOTE_DISTILL_DISPATCH", raising=False)
    entry = {"url": "eval://distill-write", "title": "长源", "raw_file": str(raw),
             **dispatch_fields("长源", src, "structured", "manual")}
    parsed = v6ize({"mainline": {"question": "主线", "progression": [], "conclusions": []},
                   "items": [{"id": "i1", "type": "argument", "stance": "source",
                              "label": "关键判断及必要条件", "source_spans": [[1, 2]]}],
                   "relations": []})
    pack = DP.build_pack(title=entry["title"], url=entry["url"], source_text=src,
                         distill_prompt_sha256=DT.prompt_hash(entry["title"], src),
                         parsed=parsed)
    return entry, src, pack, store


def test_distill_is_enabled_by_default_with_explicit_opt_out(monkeypatch):
    monkeypatch.delenv("NOTE_DISTILL_DISPATCH", raising=False)
    assert DD.distill_dispatch_enabled()
    monkeypatch.setenv("NOTE_DISTILL_DISPATCH", "0")
    assert not DD.distill_dispatch_enabled()


def test_missing_pack_is_waiting_for_distill_not_single_ready(tmp_path, monkeypatch):
    entry, src, pack, store = _inputs(tmp_path, monkeypatch)
    before = entry["prompt"]
    assert FP.distill_prepare(entry) == "needs_distill"
    assert entry["prompt"] == before
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps([entry], ensure_ascii=False), encoding="utf-8")
    report = FP._run_queue(str(queue), lambda rows: (rows, []))
    assert report["distill"]["needs_distill"] == 1
    assert report["next_action"] == "distill_then_write"


def test_prepared_pack_has_two_stage_policy_without_model_review(tmp_path, monkeypatch):
    entry, src, pack, store = _inputs(tmp_path, monkeypatch)
    DP.save_pack(pack, str(store))
    DD.prepare_entry(entry, store_dir=str(store))
    context = entry["generation_context"]
    assert context["distill_workflow"] == "distill_write"
    assert context["distill_postprocess"] == "mechanical_only"
    assert context["prompt_sha256"] == text_hash(entry["prompt"])
    assert DD.WRITE_FLOW_MARKER in entry["prompt"]
    assert context["review"]["status"] == "not_performed"


def test_old_workflow_block_is_refreshed_without_new_distillation(tmp_path, monkeypatch):
    entry, src, pack, store = _inputs(tmp_path, monkeypatch)
    DP.save_pack(pack, str(store))
    DD.prepare_entry(entry, store_dir=str(store))
    previous = entry["prompt"].replace(DD.WRITE_FLOW_MARKER, "")
    entry["prompt"] = previous
    entry["generation_context"]["prompt_sha256"] = text_hash(previous)
    old_distill_hash = entry["generation_context"]["distill_prompt_sha256"]
    result = DD.prepare_entry(entry, store_dir=str(store))
    assert result["status"] == "prepared"
    assert entry["prompt"].count(DD.WRITE_FLOW_MARKER) == 1
    assert entry["generation_context"]["distill_prompt_sha256"] == old_distill_hash
    assert entry["generation_context"]["prompt_sha256"] != text_hash(previous)


def test_coverage_suspects_only_report_by_default(tmp_path, monkeypatch, capsys):
    entry, src, pack, store = _inputs(tmp_path, monkeypatch)
    entry["generation_context"]["distill_input_mode"] = "pack_only"
    draft = tmp_path / "draft.md"
    draft.write_text("本稿采用了不同的说法。", encoding="utf-8")
    monkeypatch.setattr(CHECK, "_load_entry", lambda queue, key: entry)
    monkeypatch.setattr(DD, "load_valid_pack_for_source", lambda *a, **kw: (pack, "ok"))
    def unexpected_patch(*args, **kwargs):
        raise AssertionError("默认覆盖诊断不得触发补稿派工")
    monkeypatch.setattr(CHECK, "_emit_patch_task", unexpected_patch)
    monkeypatch.setattr(sys, "argv", ["check_note_coverage.py", "--queue", "monitors",
                                     "--key", entry["url"], "--file", str(draft)])
    assert CHECK.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["issues"]
    assert report["report_only"] is True
    assert report["next_action"] == "save_with_existing_gates"
