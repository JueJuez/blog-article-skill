"""单篇/单视频入口的两阶段交接：≥10000 字长源登记一次性队列。"""
import json
import os

from conftest import v6ize
from prompts import distill as DT
from prompts.provenance import dispatch_fields, text_hash
from shared import distill_dispatch as DD, distill_pack as DP

LONG_SOURCE = "完整论证与条件应随原文保留，这里提供非数字密集的长源。\n" * 600


def _entry(tmp_path, src=LONG_SOURCE, url="https://example.com/long", title="长源"):
    raw = tmp_path / "raw.md"
    raw.write_text(src, encoding="utf-8")
    return {"url": url, "title": title, "raw_file": str(raw),
            **dispatch_fields(title, src, "structured", "manual")}, src


def _save_pack(entry, src):
    parsed = v6ize({"mainline": {"question": "主线", "progression": [], "conclusions": []},
                    "items": [{"id": "i1", "type": "argument", "stance": "source",
                               "label": "关键判断及必要条件", "source_spans": [[1, 2]]}],
                    "relations": []})
    pack = DP.build_pack(title=entry["title"], url=entry["url"], source_text=src,
                         distill_prompt_sha256=DT.prompt_hash(entry["title"], src),
                         parsed=parsed)
    DP.save_pack(pack)
    return pack


def _queue_items(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_short_source_returns_no_handoff(tmp_path):
    entry, _ = _entry(tmp_path, src="短源内容。\n" * 50)
    assert DD.single_entry_handoff(entry) == {}


def test_below_10000_chars_returns_no_handoff(tmp_path):
    entry, _ = _entry(tmp_path, src="论" * 9999)
    assert DD.single_entry_handoff(entry) == {}


def test_exactly_10000_chars_enters_handoff(tmp_path):
    entry, _ = _entry(tmp_path, src="论" * 10000)
    rep = DD.single_entry_handoff(entry)
    assert rep["distill_route"] == "distill"
    assert rep["distill_status"] == "needs_distill"


def test_long_source_without_pack_registers_queue_and_waits_for_distill(tmp_path):
    entry, _ = _entry(tmp_path)
    rep = DD.single_entry_handoff(entry)
    assert rep["distill_route"] == "distill"
    assert rep["distill_status"] == "needs_distill"
    assert rep["distill_next_action"] == "distill_then_write"
    queue = rep["distill_queue"]
    assert os.path.isfile(queue)
    items = _queue_items(queue)
    assert items[0]["url"] == entry["url"]
    assert items[0]["prompt"] == entry["prompt"]
    assert items[0]["raw_file"] == entry["raw_file"]
    assert items[0]["generation_context"]["source_sha256"] == \
        entry["generation_context"]["source_sha256"]
    assert "--emit" in rep["distill_commands"]["emit"]
    assert "--ingest" in rep["distill_commands"]["ingest"]
    assert rep["distill_save"]["key"] == entry["url"]


def test_long_source_with_valid_pack_returns_pack_only_prompt(tmp_path):
    entry, src = _entry(tmp_path)
    _save_pack(entry, src)
    rep = DD.single_entry_handoff(entry)
    assert rep["distill_status"] in ("prepared", "already_prepared")
    assert rep["distill_next_action"] == "write"
    assert DD.WRITE_FLOW_MARKER in rep["prompt"]
    assert rep["prompt"].count(DD.MATERIAL_PACK_BEGIN) == 1
    assert rep["generation_context"]["distill_input_mode"] == "pack_only"
    assert rep["generation_context"]["prompt_sha256"] == text_hash(rep["prompt"])
    assert _queue_items(rep["distill_queue"])[0]["prompt"] == rep["prompt"]


def test_ephemeral_long_source_returns_no_handoff(tmp_path):
    """时效类长源（观点/估值/复盘）只取结论 → 不登记两阶段队列。"""
    entry, _ = _entry(tmp_path, title="【深度】中国核电、中国广核价值分析")
    assert DD.single_entry_handoff(entry) == {}


def test_tutorial_long_source_still_enters_handoff(tmp_path):
    """教程类长源仍走两阶段：质量优先。"""
    entry, _ = _entry(tmp_path, title="手把手教你搭建自动化工作流")
    rep = DD.single_entry_handoff(entry)
    assert rep["distill_route"] == "distill"
    assert rep["distill_next_action"] in ("write", "distill_then_write")


def test_missing_raw_file_returns_no_handoff(tmp_path):
    entry, _ = _entry(tmp_path)
    entry["raw_file"] = str(tmp_path / "missing.md")
    assert DD.single_entry_handoff(entry) == {}


def test_empty_url_returns_no_handoff(tmp_path):
    entry, _ = _entry(tmp_path, url="")
    assert DD.single_entry_handoff(entry) == {}


def test_switch_off_disables_handoff(tmp_path, monkeypatch):
    entry, _ = _entry(tmp_path)
    monkeypatch.setenv("NOTE_DISTILL_DISPATCH", "0")
    assert DD.single_entry_handoff(entry) == {}


def test_queue_write_failure_does_not_raise(tmp_path, monkeypatch):
    entry, _ = _entry(tmp_path)

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(DD, "_write_entry_queue", _boom)
    assert DD.single_entry_handoff(entry) == {}
