# -*- coding: utf-8 -*-
"""监控管线接入四步新流程：不重复登记一次性交接、接单提示指向统一入口。

2026-10-29 补漏：监控文章路径此前未告知 `_entry_handoff=False`，长源会在一次性队列和
monitors 常驻队列里各登记一份；运行结束打印的接单说明也仍让模型直接调
`save_summary_only` 并手改队列，绕过 `filter_pending.py` / `distill_prepare.py` /
`save_pending.py`。
"""
from monitors import run as run_mod


class TestArticleHandoff:
    def _run(self, monkeypatch, stat_key="article"):
        from articles import main as am
        captured = {}

        def fake_skill_main(data):
            captured.update(data)
            return {"need_continue_summary": True, "prompt": "PROMPT",
                    "raw_file": "RAW", "note_type": "key_points"}

        monkeypatch.setattr(am, "skill_main", fake_skill_main)
        monkeypatch.setattr(run_mod, "_queue_pending_summary", lambda it, res: None)
        stats = {"ad_skip": 0, "short_skip": 0, "article": 0, "dynamic_full": 0,
                 "dynamic_light": 0, "error": 0}
        item = {"source": "wechat", "url": "https://mp.weixin.qq.com/s/rehearsal",
                "title": "标题", "mp_name": "公众号", "publish_time": 0,
                "category": "投资交易"}
        run_mod._summarize_article(item, "标题", "正文内容足够长，用于演练。", False,
                                   stats, stat_key=stat_key)
        return captured, stats

    def test_article_item_suppresses_one_off_handoff(self, monkeypatch):
        captured, stats = self._run(monkeypatch)
        assert captured.get("_entry_handoff") is False
        assert stats["article"] == 1

    def test_dynamic_full_counts_its_own_stat(self, monkeypatch):
        captured, stats = self._run(monkeypatch, stat_key="dynamic_full")
        assert captured.get("_entry_handoff") is False
        assert stats["dynamic_full"] == 1
        assert stats["article"] == 0


class TestPendingAgentHint:
    def test_monitors_hint_points_to_unified_entrypoints(self):
        text = run_mod.pending_agent_hint("monitors", "monitors/pending_summaries.json", 3)
        assert "NEED_AGENT_SUMMARY" in text and "3 条" in text
        assert "scripts/filter_pending.py" in text
        assert "scripts/distill_prepare.py" in text
        assert 'scripts/save_pending.py --queue monitors' in text
        assert "save_summary_only(" not in text

    def test_scys_hint_uses_scys_queue(self):
        text = run_mod.pending_agent_hint("scys", "notes/_scraped/scys/pending_summaries.json", 2)
        assert "NEED_AGENT_SCYS_SUMMARY" in text and "2 篇" in text
        assert 'scripts/save_pending.py --queue scys' in text
        assert "从队列移除该条" not in text

    def test_unknown_kind_falls_back_to_monitors(self):
        """未识别队列名不得抛异常：提示仍指向统一入口。"""
        text = run_mod.pending_agent_hint("no_such_queue", "x.json", 1)
        assert "--queue monitors" in text
