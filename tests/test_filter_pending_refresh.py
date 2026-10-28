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

from prompts.provenance import dispatch_fields
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
        e = {"note_type": "structured", "raw_file": raw, "prompt": "旧版 prompt",
             "generation_context": {"type_origin": "manual"}}
        assert refresh_prompt(e) == "refreshed"
        assert e["prompt"] == _expected_prompt("structured", 4000)
        assert "推广" in e["prompt"] and "chart" in e["prompt"]

    def test_current_prompt_kept(self, tmp_path):
        raw = _write_raw(tmp_path, "正文" * 2000)
        e = {**dispatch_fields("", "正文" * 2000, "structured", "manual"), "raw_file": raw}
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
        assert refresh_prompt(e) == "skipped"  # 仅chars不能证明源版本或类型来源
        assert e["prompt"] == "旧"

    def test_scys_no_chars_skipped(self):
        e = {"note_type": "case", "prompt": "旧"}
        assert refresh_prompt(e) == "skipped"


class TestRunQueueIntegration:
    def test_queue_file_rewritten_with_refreshed_prompts(self, tmp_path):
        raw = _write_raw(tmp_path, "源文本" * 1000)  # 3000 字
        qpath = tmp_path / "pending.json"
        entries = [
            {"url": "https://a", "note_type": "structured", "raw_file": raw,
             "generation_context": {"type_origin": "manual"}, "prompt": "旧版"},
            {"url": "https://b", "raw_file": raw,
             **dispatch_fields("", "源文本" * 1000, "structured", "manual")},
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
        entries = [{"url": "https://a", "raw_file": raw,
                    **dispatch_fields("", "源文本" * 1000, "structured", "manual")}]
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


class TestDistillFallbackPersisted:
    """S9 §8.5 第 1 条：提炼回退若剥了旧材料包块，必须写回队列文件。

    生产接线（`filter_pending.distill_prepare`）默认返回一个笼统的 `fallback`，
    `_run_queue` 只把 prepared/stale 计入写回条件 → 剥了包却不落盘，下一轮继续消费
    失效材料。这里钉死两件事：返回值区分 `stripped`，且 stripped 触发写回。
    注意日志环境变量必须重定向到 tmp，否则测试会往生产 `notes/_meta` 里写。
    """

    def _long_source(self, chars: int = 12000) -> str:
        line = "这一段口语论述用来凑足字数本身不含任何读数也不包含数字口径。\n"
        out, n = [], 0
        while n < chars:
            out.append(line)
            n += len("".join(line.split()))
        return "".join(out)

    def _pack(self, src: str, url: str, title: str):
        from prompts import distill as DT
        from shared import distill_pack as DP
        return DP.build_pack(title=title, url=url, source_text=src,
                             distill_prompt_sha256=DT.prompt_hash(title, src),
                             parsed={"mainline": {"question": "q", "progression": ["p"],
                                                 "conclusions": ["c"]},
                                     "items": [{"id": "i1", "type": "step",
                                                "stance": "paraphrase",
                                                "content": "先做背调",
                                                "source_lines": [1, 1]}],
                                     "relations": []})

    def test_stripped_pack_is_written_back(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOTE_DISTILL_DISPATCH", "1")
        monkeypatch.setenv("NOTE_DISTILL_DISPATCH_LOG", str(tmp_path / "dispatch.jsonl"))
        from shared import distill_dispatch as DD

        src = self._long_source()
        raw = tmp_path / "raw.md"
        raw.write_text(src, encoding="utf-8")
        entry = {"url": "https://s9-a", "title": "长源", "raw_file": str(raw),
                 "prompt": "BASE PROMPT\n", "generation_context": {}}
        block = DD.render_pack_block(self._pack(src, "https://s9-a", "长源"))
        entry["prompt"] = entry["prompt"] + "\n\n" + block
        entry["generation_context"]["distill_pack_key"] = "stale-key"
        qpath = tmp_path / "pending.json"
        qpath.write_text(json.dumps([entry], ensure_ascii=False), encoding="utf-8")

        raw.write_text("短源。\n" * 50, encoding="utf-8")       # 源变短 → 回退并剥包
        stats = _run_queue(str(qpath), lambda es: (es, []))
        assert stats["distill"]["stripped"] == 1
        saved = json.load(open(str(qpath), encoding="utf-8"))[0]
        assert DD.MATERIAL_PACK_BEGIN not in saved["prompt"]
        assert "distill_pack_key" not in saved["generation_context"]

    def test_plain_fallback_does_not_force_rewrite(self, tmp_path, monkeypatch):
        """没剥包的回退（顺例：压根没有 pack）保持原语义，不制造无谓写盘。"""
        monkeypatch.setenv("NOTE_DISTILL_DISPATCH", "1")
        monkeypatch.setenv("NOTE_DISTILL_DISPATCH_LOG", str(tmp_path / "dispatch2.jsonl"))
        raw = tmp_path / "raw.md"
        raw.write_text("短源。\n" * 50, encoding="utf-8")
        entry = {"url": "https://s9-b", "title": "短源", "raw_file": str(raw),
                 "prompt": "BASE PROMPT\n", "generation_context": {}}
        qpath = tmp_path / "pending.json"
        qpath.write_text(json.dumps([entry], ensure_ascii=False), encoding="utf-8")
        mtime_before = os.path.getmtime(str(qpath))
        stats = _run_queue(str(qpath), lambda es: (es, []))
        assert stats["distill"]["fallback"] == 1
        assert stats["distill"]["stripped"] == 0
        assert os.path.getmtime(str(qpath)) == mtime_before
