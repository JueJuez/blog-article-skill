"""filter_pending --refresh-prompts（默认开）守护测试（2026-10-06）。

背景：长跑补齐进程启动时加载的模板可能落后于仓库当前模板（UP 补齐 16:56
启动错过 18:46 质量纪律提交），入队预计算的 prompt 在消费前须按当前模板
机械重算。钉死三件事：刷新口径与入队写点一致、不可得时跳过不猜、
_run_queue 把刷新结果写回队列文件。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.filter_pending import _run_queue, refresh_prompt  # noqa: E402


def _expected_prompt(note_type: str, chars: int) -> str:
    from articles.prompt import get_note_prompt
    from prompts.templates import QUALITY_GATE_SELFCHECK
    return get_note_prompt(note_type, chars) + QUALITY_GATE_SELFCHECK


def _write_raw(tmp_path, text: str) -> str:
    rf = tmp_path / "raw.md"
    rf.write_text(text, encoding="utf-8")
    return str(rf)


class TestRefreshPrompt:
    def test_old_prompt_refreshed_with_discipline(self, tmp_path):
        raw = _write_raw(tmp_path, "正文" * 2000)  # 4000 字
        e = {"note_type": "structured", "raw_file": raw, "prompt": "旧版 prompt"}
        assert refresh_prompt(e) == "refreshed"
        assert e["prompt"] == _expected_prompt("structured", 4000)
        assert "推广" in e["prompt"] and "chart" in e["prompt"]

    def test_current_prompt_kept(self, tmp_path):
        raw = _write_raw(tmp_path, "正文" * 2000)
        e = {"note_type": "structured", "raw_file": raw,
             "prompt": _expected_prompt("structured", 4000)}
        assert refresh_prompt(e) == "kept"

    def test_missing_note_type_skipped(self):
        e = {"raw_file": "", "prompt": "旧"}
        assert refresh_prompt(e) == "skipped"
        assert e["prompt"] == "旧"

    def test_missing_raw_file_skipped(self, tmp_path):
        e = {"note_type": "key_points", "raw_file": str(tmp_path / "nope.md"),
             "prompt": "旧"}
        assert refresh_prompt(e) == "skipped"
        assert e["prompt"] == "旧"

    def test_scys_style_chars_only(self):
        e = {"note_type": "case", "chars": 5000, "prompt": "旧"}
        assert refresh_prompt(e) == "refreshed"
        assert e["prompt"] == _expected_prompt("case", 5000)

    def test_scys_no_chars_skipped(self):
        e = {"note_type": "case", "prompt": "旧"}
        assert refresh_prompt(e) == "skipped"


class TestRunQueueIntegration:
    def test_queue_file_rewritten_with_refreshed_prompts(self, tmp_path):
        raw = _write_raw(tmp_path, "源文本" * 1000)  # 3000 字
        qpath = tmp_path / "pending.json"
        entries = [
            {"url": "https://a", "note_type": "structured", "raw_file": raw,
             "prompt": "旧版"},
            {"url": "https://b", "note_type": "structured", "raw_file": raw,
             "prompt": _expected_prompt("structured", 3000)},
        ]
        qpath.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        stats = _run_queue(str(qpath), lambda es: (es, []))
        assert stats["kept"] == 2 and stats["dropped"] == 0
        assert stats["prompt_refreshed"] == 1 and stats["prompt_skipped"] == 0
        saved = json.load(open(str(qpath), encoding="utf-8"))
        assert saved[0]["prompt"] == _expected_prompt("structured", 3000)
        assert saved[1]["prompt"] == _expected_prompt("structured", 3000)

    def test_no_changes_no_rewrite(self, tmp_path):
        raw = _write_raw(tmp_path, "源文本" * 1000)  # 3000 字
        qpath = tmp_path / "pending.json"
        entries = [{"url": "https://a", "note_type": "structured",
                    "raw_file": raw, "prompt": _expected_prompt("structured", 3000)}]
        qpath.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        mtime_before = os.path.getmtime(str(qpath))
        stats = _run_queue(str(qpath), lambda es: (es, []))
        assert stats["prompt_refreshed"] == 0
        assert os.path.getmtime(str(qpath)) == mtime_before

    def test_dropped_entries_still_written(self, tmp_path):
        qpath = tmp_path / "pending.json"
        entries = [{"url": "https://drop-me", "prompt": "旧"}]
        qpath.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        stats = _run_queue(str(qpath), lambda es: ([], es))
        assert stats["dropped"] == 1
        assert json.load(open(str(qpath), encoding="utf-8")) == []
