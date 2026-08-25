"""三层机械去重前置测试（DECISION-20260825）：入队拦截 / 派单前过滤 / 落盘闸门。

保护需求：多 Agent 接力（非并发）消化待总结队列时，
已总结条目不应再消耗 AI 总结 token，也不应重复写入飞书。
"""
import json
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(BASE_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(BASE_DIR / "scripts"))

from articles import dedup
from articles import main as articles_main
from monitors import run as run_mod


@pytest.fixture
def dedup_index(tmp_path, monkeypatch):
    """把 dedup 索引指到临时目录，测试间互不污染真实 .cache/dedup.json。"""
    monkeypatch.setattr(dedup, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(dedup, "_INDEX_FILE", str(tmp_path / "dedup.json"))
    return dedup


class TestSaveSummaryOnlyGate:
    """第③层落盘闸门：URL 已在 dedup 索引 → 跳过写入且按成功出队。"""

    @pytest.fixture(autouse=True)
    def _no_real_save(self, monkeypatch):
        self.calls = []
        monkeypatch.setattr(
            articles_main, "save_summarized_article",
            lambda *a, **k: (self.calls.append(1), ("fmt", "f.md"))[1])

    def test_skips_when_url_already_summarized(self, dedup_index):
        dedup_index.mark_summarized(url="https://a.com/x", filename="old.md")
        res = articles_main.save_summary_only({
            "summarized_content": "总结", "original_url": "https://a.com/x"})
        assert res.get("success") is True
        assert res.get("skipped") is True
        assert self.calls == []

    def test_marks_index_after_success(self, dedup_index):
        res = articles_main.save_summary_only({
            "summarized_content": "总结", "original_url": "https://b.com/y",
            "original_title": "T"})
        assert res.get("success") is True
        assert dedup_index.is_summarized(url="https://b.com/y") != {}

    def test_force_bypasses_gate(self, dedup_index):
        dedup_index.mark_summarized(url="https://c.com/z", filename="old.md")
        res = articles_main.save_summary_only({
            "summarized_content": "总结", "original_url": "https://c.com/z",
            "force": True})
        assert res.get("skipped") is not True
        assert self.calls == [1]

    def test_no_url_falls_through_to_save(self, dedup_index):
        res = articles_main.save_summary_only({"summarized_content": "总结"})
        assert res.get("success") is True
        assert self.calls == [1]


class TestEnqueueGate:
    """第①层入队拦截：已总结 URL 不进 pending_summaries 队列（省 AI token）。"""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        self.queue_path = tmp_path / "pending_summaries.json"
        self.queue_path.write_text("[]", encoding="utf-8")
        monkeypatch.setattr(run_mod, "PENDING_SUMMARY_PATH", str(self.queue_path))
        monkeypatch.setattr(run_mod, "_item_folder", lambda it: "")
        monkeypatch.setattr(run_mod, "derive_title_from_body",
                            lambda rf, t, max_len=36: t)

    def test_summarized_url_not_enqueued(self, dedup_index):
        dedup_index.mark_summarized(url="https://a.com/x")
        run_mod._queue_pending_summary(
            {"url": "https://a.com/x", "title": "T", "publish_time": 0},
            {"need_continue_summary": True, "raw_file": "r.md",
             "original_title": "T"})
        assert json.loads(self.queue_path.read_text(encoding="utf-8")) == []

    def test_new_url_enqueued(self, dedup_index):
        run_mod._queue_pending_summary(
            {"url": "https://b.com/y", "title": "T", "publish_time": 0},
            {"need_continue_summary": True, "raw_file": "r.md",
             "original_title": "T"})
        q = json.loads(self.queue_path.read_text(encoding="utf-8"))
        assert len(q) == 1
        assert q[0]["url"] == "https://b.com/y"


class TestFilterPending:
    """第②层派单前过滤：filter_pending 清洗两队列，编排方只对剩余条目派子 Agent。"""

    def test_monitors_drops_dedup_hit_keeps_rest(self, dedup_index):
        import filter_pending as fp
        dedup_index.mark_summarized(url="https://done.com/1")
        entries = [
            {"url": "https://done.com/1", "title": "旧"},
            {"url": "https://todo.com/2", "title": "新"},
        ]
        keep, dropped = fp.filter_monitors(entries)
        assert [e["url"] for e in keep] == ["https://todo.com/2"]
        assert len(dropped) == 1

    def test_scys_drops_summarized_flag_and_dedup_hit(self, dedup_index):
        import filter_pending as fp
        dedup_index.mark_summarized(url="https://scys.com/done")
        entries = [
            {"url": "https://scys.com/done", "summarized": False},
            {"url": "https://scys.com/flag", "summarized": True},
            {"url": "https://scys.com/new", "summarized": False},
        ]
        keep, dropped = fp.filter_scys(entries)
        assert [e["url"] for e in keep] == ["https://scys.com/new"]
        assert len(dropped) == 2

    def test_empty_queues_pass_through(self):
        import filter_pending as fp
        keep, dropped = fp.filter_monitors([])
        assert keep == [] and dropped == []

    def test_main_persists_and_reports(self, dedup_index, tmp_path,
                                       monkeypatch, capsys):
        import filter_pending as fp
        mp = tmp_path / "m.json"
        mp.write_text(json.dumps(
            [{"url": "https://x.com/1", "title": "a"}], ensure_ascii=False),
            encoding="utf-8")
        sp = tmp_path / "s.json"
        sp.write_text(json.dumps(
            [{"url": "https://y.com/2", "summarized": True}],
            ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(fp, "PENDING_PATH", str(mp))
        monkeypatch.setattr(fp, "SCYS_PENDING_PATH", str(sp))
        fp.main()
        assert json.loads(mp.read_text(encoding="utf-8")) == [
            {"url": "https://x.com/1", "title": "a"}]
        assert json.loads(sp.read_text(encoding="utf-8")) == []
        out = capsys.readouterr().out
        assert "kept" in out and "dropped" in out
