"""入队条目 prompt 预计算回归测试（monitors 降级队列 + scys 批量队列）。

保护的需求（2026-09-05）：三大待总结队列（monitors / scys / UP）的入队条目
统一带预计算 prompt（get_note_prompt(note_type) + QUALITY_GATE_SELFCHECK），
子 Agent 读队列即可总结，无需自调任何 CLI（AGENTS.md 能力 2 契约）。
"""
import json

import pytest

from monitors import run as run_mod
from prompts.classify import classify_note_type
from prompts.templates import QUALITY_GATE_SELFCHECK, get_note_prompt

import scys_batch_fetch as sbf


# ---------- monitors/_queue_pending_summary ----------

class TestMonitorsQueuePrompt:
    @pytest.fixture
    def queue_env(self, monkeypatch, tmp_path):
        qpath = tmp_path / "pending_summaries.json"
        monkeypatch.setenv("MON_PENDING_SUMMARY_PATH", str(qpath))
        return qpath

    @staticmethod
    def _it() -> dict:
        return {"url": "https://mp.weixin.qq.com/s/abc", "title": "7月19日哥飞的朋友们",
                "mp_name": "哥飞", "publish_time": 1700000000}

    def test_entry_carries_precomputed_prompt_when_note_type_present(self, queue_env):
        res = {"need_continue_summary": True, "note_type": "case",
               "original_title": "7月19日哥飞的朋友们", "raw_file": "", "tags": ["SEO"]}
        run_mod._queue_pending_summary(self._it(), res)
        entry = json.load(open(queue_env, encoding="utf-8"))[0]
        assert entry["note_type"] == "case"
        assert entry["prompt"] == get_note_prompt("case") + QUALITY_GATE_SELFCHECK

    def test_entry_classifies_when_note_type_missing(self, queue_env, tmp_path):
        raw = tmp_path / "raw.md"
        raw.write_text("这是一篇深度复盘案例：从0到1做出十万粉账号的全过程。", encoding="utf-8")
        res = {"need_continue_summary": True, "raw_file": str(raw)}
        run_mod._queue_pending_summary(self._it(), res)
        entry = json.load(open(queue_env, encoding="utf-8"))[0]
        assert entry["note_type"] == classify_note_type("7月19日哥飞的朋友们",
                                                        raw.read_text(encoding="utf-8")[:4000])
        assert entry["prompt"] == get_note_prompt(entry["note_type"]) + QUALITY_GATE_SELFCHECK

    def test_entry_prompt_present_even_without_raw_file(self, queue_env):
        res = {"need_continue_summary": True, "raw_file": ""}
        run_mod._queue_pending_summary(self._it(), res)
        entry = json.load(open(queue_env, encoding="utf-8"))[0]
        assert entry["note_type"]
        assert len(entry["prompt"]) > 200  # 预计算 prompt 已带上（含质量自检段）

    def test_dedup_skip_still_blocks_enqueue(self, queue_env, monkeypatch):
        from articles import dedup
        monkeypatch.setattr(dedup, "is_summarized", lambda **kw: {"filename": "x.md"})
        res = {"need_continue_summary": True, "note_type": "case", "raw_file": ""}
        run_mod._queue_pending_summary(self._it(), res)
        assert not queue_env.exists()


# ---------- scys_batch_fetch.build_pending_entry ----------

class TestScysPendingEntry:
    def test_entry_has_note_type_and_precomputed_prompt(self, tmp_path):
        raw = tmp_path / "12345.md"
        raw.write_text("这是一篇深度复盘案例：从0到1做出十万粉账号的全过程。", encoding="utf-8")
        r = {"topicId": 12345, "url": "https://scys.com/articleDetail/xq_topic/12345",
             "title": "十万粉复盘", "chars": 25, "output": str(raw),
             "external_docs": [], "related": []}
        it = {"topicId": "12345", "isDigested": True, "readingCount": 100}
        e = sbf.build_pending_entry(r, "出海", it)
        assert e["topicId"] == "12345"
        assert e["project"] == "出海"
        assert e["list_meta"]["isDigested"] is True
        assert e["note_type"] == classify_note_type("十万粉复盘",
                                                    raw.read_text(encoding="utf-8")[:4000])
        assert e["prompt"] == get_note_prompt(e["note_type"]) + QUALITY_GATE_SELFCHECK

    def test_missing_output_file_still_yields_prompt(self, tmp_path):
        r = {"topicId": 1, "url": "u", "title": "普通分享帖", "chars": 0,
             "output": str(tmp_path / "nope.md"), "external_docs": [], "related": []}
        e = sbf.build_pending_entry(r, "自媒体", {"topicId": "1"})
        assert e["note_type"] == classify_note_type("普通分享帖", "")
        assert e["prompt"] == get_note_prompt(e["note_type"]) + QUALITY_GATE_SELFCHECK

    def test_empty_body_file_yields_prompt(self, tmp_path):
        raw = tmp_path / "2.md"
        raw.write_text("", encoding="utf-8")
        r = {"topicId": 2, "url": "u", "title": "观点随笔", "chars": 0,
             "output": str(raw), "external_docs": [], "related": []}
        e = sbf.build_pending_entry(r, "AI产品开发", {"topicId": "2"})
        assert e["prompt"] == get_note_prompt(e["note_type"]) + QUALITY_GATE_SELFCHECK
