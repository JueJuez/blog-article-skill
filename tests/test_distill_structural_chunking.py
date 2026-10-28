# -*- coding: utf-8 -*-
"""N2 结构感知切分回归测试（PLAN-20261028-long-source-summary §4-N2）。

切分要回答的问题不是「每 40k 字切一刀」，而是三件事：

1. **无空洞**：各块**核心区间**首尾相接、并集恰好覆盖 1..总行数；重叠只进上下文，
   不进核心（核心重复会让同一段被提炼两次、合并时再去重掩盖真实成本）。
2. **核心与上下文可区分**：`start_line–end_line` 是下发范围（含前一块尾部重叠），
   `core_start_line–core_end_line` 是本块负责提炼的范围；派单文案必须把两者分开写，
   并要求 `source_lines` 落在核心区间内。
3. **完整单元优先，超长显式续接**：标题/段落、表格、代码块在容量内不被切开；
   单个结构单元超过容量时切开，但必须标 `continued` / `continues` 并在任务里说明，
   不许静默拦腰切。无结构文本（字幕/口播）按句子边界与容量兜底。

切分是纯机械的：不新增任何 LLM 分段调用。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from prompts import distill as DT
from shared import distill_pack as DP
from shared import distill_dispatch as DD
from shared import source_material as SM

_TALK = ("这一段口语论述用来占位本身不含数字只是把前因后果和背景交代清楚便于理解上下文"
         "不会进入任何统计口径")


def _lines_of(chars: int, end: str = "。") -> list:
    out, n = [], 0
    while n < chars:
        out.append(f"{_TALK}{end}")
        n += len("".join(out[-1].split()))
    return out


def _section(k: int, chars: int = 9000) -> str:
    return "\n".join([f"## 第{k}节 标题"] + _lines_of(chars))


def _structured_source(sections: int = 6, chars: int = 9000) -> str:
    return "\n\n".join(_section(k, chars) for k in range(1, sections + 1))


def _table(rows: int = 60, cols: int = 4) -> str:
    head = "| " + " | ".join(f"列{c}" for c in range(1, cols + 1)) + " |"
    sep = "|" + "|".join("---" for _ in range(cols)) + "|"
    body = ["| " + " | ".join(f"r{r}c{c}内容" for c in range(1, cols + 1)) + " |"
            for r in range(1, rows + 1)]
    return "\n".join([head, sep] + body)


def _code(chars: int = 5000) -> str:
    body, n = [], 0
    while n < chars:
        body.append(f"print('代码行 {len(body) + 1}')  # 说明文字占位")
        n += len("".join(body[-1].split()))
    return "\n".join(["```python"] + body + ["```"])


def _asr_source(chars: int = 90000) -> str:
    """字幕/口播：无标题、无空行、每行以句号结尾。"""
    return "\n".join(_lines_of(chars))


def _plain_source(chars: int = 90000) -> str:
    """无结构也无标点：只能按容量兜底。"""
    return "\n".join(_lines_of(chars, end=""))


def _section_end_lines(src: str) -> set:
    lines = src.split("\n")
    ends = {i for i, ln in enumerate(lines, 1) if ln.startswith("## ")}
    return {e - 1 for e in ends if e > 1} | {len(lines)}


# ---------------------------------------------------------------- 1. 核心区间

class TestCoreRanges:
    def test_single_chunk_records_core_and_legacy_fields(self):
        src = _section(1, 3000)
        c = DD.plan_chunks(src)[0]
        n = len(src.split("\n"))
        assert (c["start_line"], c["end_line"]) == (1, n)
        assert (c["core_start_line"], c["core_end_line"]) == (1, n)
        assert c["context_lines"] == 0
        for legacy in ("index", "total", "chars"):
            assert legacy in c

    def test_core_ranges_partition_without_holes(self):
        src = _structured_source()
        chunks = DD.plan_chunks(src)
        n = len(src.split("\n"))
        assert len(chunks) >= 2
        assert chunks[0]["core_start_line"] == 1
        assert chunks[-1]["core_end_line"] == n
        for a, b in zip(chunks, chunks[1:]):
            assert b["core_start_line"] == a["core_end_line"] + 1, (a, b)
        # 核心区间不重叠：重叠只应出现在下发范围
        covered = sum(c["core_end_line"] - c["core_start_line"] + 1 for c in chunks)
        assert covered == n

    def test_delivered_range_includes_only_backward_context(self):
        chunks = DD.plan_chunks(_structured_source())
        for c in chunks[1:]:
            assert c["context_lines"] >= 1
            assert c["start_line"] == c["core_start_line"] - c["context_lines"]
            assert c["end_line"] == c["core_end_line"]

    def test_blank_line_heavy_doc_has_no_holes(self):
        src = "\n\n".join("\n".join(_lines_of(4000)) for _ in range(12))
        chunks = DD.plan_chunks(src)
        n = len(src.split("\n"))
        assert chunks[0]["core_start_line"] == 1
        assert chunks[-1]["core_end_line"] == n
        for a, b in zip(chunks, chunks[1:]):
            assert b["core_start_line"] == a["core_end_line"] + 1


# ---------------------------------------------------------------- 2. 结构边界

class TestStructuralBoundaries:
    def test_cut_prefers_section_ends(self):
        src = _structured_source()
        ends = _section_end_lines(src)
        for c in DD.plan_chunks(src)[:-1]:
            assert c["core_end_line"] in ends, (c, sorted(ends))
            assert c["boundary"] in ("heading", "paragraph", "table", "code", "sentence")

    def test_table_stays_inside_one_chunk(self):
        main = "\n\n".join([_section(1), _table(80), _section(2), _section(3)])
        table = SM.scan_structure(main)["tables"][0]
        for c in DD.plan_chunks(main):
            inside_start = c["core_start_line"] > table["line_start"] and \
                c["core_start_line"] <= table["line_end"]
            inside_end = c["core_end_line"] >= table["line_start"] and \
                c["core_end_line"] < table["line_end"]
            assert not inside_start and not inside_end, (c, table)

    def test_code_block_stays_inside_one_chunk(self):
        main = "\n\n".join([_section(1), _code(6000), _section(2), _section(3)])
        code = SM.scan_structure(main)["code_blocks"][0]
        for c in DD.plan_chunks(main):
            assert not (c["core_start_line"] > code["line_start"]
                        and c["core_start_line"] <= code["line_end"])
            assert not (c["core_end_line"] >= code["line_start"]
                        and c["core_end_line"] < code["line_end"])


# ---------------------------------------------------------------- 3. 超长单元续接

class TestContinuation:
    def _huge_code_source(self) -> str:
        return "\n\n".join([_section(1, 4000), _code(DD.CHUNK_MAX_CHARS + 20000),
                            _section(2, 4000)])

    def test_oversized_code_marks_continuation(self):
        src = self._huge_code_source()
        chunks = DD.plan_chunks(src)
        split_at = [i for i, c in enumerate(chunks) if c["continues"]]
        resumed_at = [i for i, c in enumerate(chunks) if c["continued"]]
        assert split_at and resumed_at, chunks
        assert split_at[0] + 1 == resumed_at[0]
        # 续接块的接续目标一致（同一结构单元）
        cont = chunks[resumed_at[0]]["continuation"]
        assert cont and cont["unit"] == "code" and cont["part"] >= 2

    def test_oversized_code_prompt_says_continuation(self):
        src = self._huge_code_source()
        chunks = DD.plan_chunks(src)
        first = next(c for c in chunks if c["continues"])
        nxt = next(c for c in chunks if c["continued"])
        p1 = DD.render_chunk_prompt("T", src, first)
        p2 = DD.render_chunk_prompt("T", src, nxt)
        assert "尚未结束" in p1 or "续接" in p1
        assert "接续" in p2 or "不要重复" in p2

    def test_continuation_chunks_cover_core_without_holes(self):
        src = self._huge_code_source()
        chunks = DD.plan_chunks(src)
        for a, b in zip(chunks, chunks[1:]):
            assert b["core_start_line"] == a["core_end_line"] + 1


# ---------------------------------------------------------------- 4. 无结构兜底

class TestFallback:
    def test_asr_sentences_cut_at_sentence_boundaries(self):
        src = _asr_source()
        chunks = DD.plan_chunks(src)
        assert len(chunks) >= 2
        for c in chunks[:-1]:
            assert c["boundary"] in ("sentence", "paragraph")

    def test_no_punctuation_falls_back_to_capacity(self):
        src = _plain_source()
        chunks = DD.plan_chunks(src)
        n = len(src.split("\n"))
        assert len(chunks) >= 2
        assert chunks[0]["core_start_line"] == 1
        assert chunks[-1]["core_end_line"] == n
        for a, b in zip(chunks, chunks[1:]):
            assert b["core_start_line"] == a["core_end_line"] + 1

    def test_chunk_size_respects_capacity(self):
        src = _asr_source()
        longest = max(len("".join(l.split())) for l in src.split("\n"))
        for c in DD.plan_chunks(src):
            assert c["chars"] <= DD.CHUNK_MAX_CHARS + longest

    def test_single_oversized_line_stays_one_chunk(self):
        """超长单行不可再切：允许单块超容量，但不能死循环、不能造空洞。"""
        src = _TALK * 1200 + "。"
        chunks = DD.plan_chunks(src)
        assert len(chunks) == 1
        c = chunks[0]
        assert (c["core_start_line"], c["core_end_line"]) == (1, 1)
        assert c["chars"] > DD.CHUNK_MAX_CHARS

    def test_last_chunk_absorbs_tail(self):
        src = _structured_source()
        n = len(src.split("\n"))
        assert DD.plan_chunks(src)[-1]["core_end_line"] == n


# ---------------------------------------------------------------- 5. 派单文案

class TestChunkPrompt:
    def test_prompt_separates_core_from_context(self):
        src = _structured_source()
        chunks = DD.plan_chunks(src)
        c = chunks[1]
        p = DD.render_chunk_prompt("T", src, c)
        assert f"核心第 {c['core_start_line']}–{c['core_end_line']} 行" in p
        assert "重叠上下文" in p
        assert "不要重复提炼" in p
        assert "source_spans" in p and "核心区间" in p
        assert "不得替全篇下结论" in p

    def test_context_lines_are_delivered_but_marked(self):
        src = _structured_source()
        c = DD.plan_chunks(src)[1]
        p = DD.render_chunk_prompt("T", src, c)
        # 上下文内容确实下发（给模型消解指代），但被明确标为只读上下文
        assert f"{c['start_line']}| " in p
        assert f"{c['core_start_line']}| " in p
        assert c["start_line"] < c["core_start_line"]

    def test_single_chunk_uses_whole_source_prompt(self):
        src = _section(1, 3000)
        c = DD.plan_chunks(src)[0]
        p = DD.render_chunk_prompt("T", src, c)
        assert DT.numbered_source(src) in p


# ---------------------------------------------------------------- 6. 下游消费

class TestDownstreamUsesCore:
    def test_merge_processed_ranges_use_core_not_context(self):
        src = _structured_source()
        chunks = DD.plan_chunks(src)
        parts = [{"chunk": c, "parsed": {
            "mainline": {"question": "q", "progression": [], "conclusions": []},
            "items": [{"id": "i1", "type": "argument", "stance": "paraphrase",
                       "content": f"第{c['index']}块的核心论证",
                       "source_lines": [c["core_start_line"], c["core_start_line"]]}],
            "relations": []}, "prompt_sha256": "h"} for c in chunks]
        pack = DP.merge_chunk_packs(title="T", url="eval://n2/merge", source_text=src,
                                    distill_prompt_sha256=DT.prompt_hash("T", src),
                                    parts=parts)
        n = len(src.split("\n"))
        assert pack["coverage"]["processed_ranges"] == [[1, n]]
        assert pack["coverage"]["complete"] is True

    def test_chunk_scope_issues_detects_out_of_core_item(self):
        src = _structured_source()
        c = DD.plan_chunks(src)[1]
        bad = {"mainline": {"question": "q"}, "relations": [],
               "items": [{"id": "i1", "type": "paragraph", "stance": "paraphrase",
                          "content": "越界条目",
                          "source_lines": [c["core_start_line"] - 5,
                                           c["core_start_line"] - 4]}]}
        issues = DD.chunk_scope_issues(c, bad)
        assert issues and "核心区间" in issues[0]
        good = {"mainline": {"question": "q"}, "relations": [],
                "items": [{"id": "i1", "type": "argument", "stance": "paraphrase",
                           "content": "范围内", "source_lines": [c["core_start_line"]] * 2}]}
        assert DD.chunk_scope_issues(c, good) == []

    def test_ingest_rejects_out_of_core_block(self, tmp_path):
        src = _structured_source()
        sf = tmp_path / "src.md"
        sf.write_text(src, encoding="utf-8")
        qp = tmp_path / "queue.json"
        qp.write_text(json.dumps([{"url": "eval://n2/ingest", "title": "T", "prompt": "B\n",
                                   "raw_file": str(sf)}], ensure_ascii=False),
                      encoding="utf-8")
        out = tmp_path / "tasks"
        man = DD.emit_distill_tasks(str(qp), str(out))
        assert len(man["tasks"]) >= 2
        for t in man["tasks"]:
            c = t["chunk"]
            parsed = {"mainline": {"question": "q", "progression": [], "conclusions": []},
                      "relations": [],
                      "items": [{"id": "i1", "type": "argument", "stance": "paraphrase",
                                 "content": f"块{t['chunk']['index']}",
                                 "source_lines": [c["core_start_line"], c["core_start_line"]]}]}
            if t is man["tasks"][1]:   # 第二块故意写到第一块核心区间
                parsed["items"][0]["source_lines"] = [1, 1]
            stem = t["task_file"].replace("_distill.txt", "")
            json.dump(parsed, open(out / f"{stem}.json", "w", encoding="utf-8"),
                      ensure_ascii=False)
        res = DD.ingest_distill_results(str(out), store_dir=str(tmp_path / "packs"))
        assert not res["saved"]
        assert any("核心区间" in m for it in res["failed"] for m in it["issues"])


# ---------------------------------------------------------------- 7. 单一真源

class TestStructureSingleSource:
    def test_scan_structure_matches_index(self):
        src = _structured_source(2) + "\n\n" + _table(5) + "\n\n" + _code(500)
        st = SM.scan_structure(src)
        idx = SM.build_index(src, kind="article")
        assert st["headings"] == idx["headings"]
        assert st["tables"] == idx["tables"]
        assert st["code_blocks"] == idx["code_blocks"]
        assert st["paragraphs"]["bounds"] == idx["paragraphs"]["bounds"]

    def test_plan_chunks_uses_shared_structure(self):
        """切块与材料索引必须同一口径：表格/代码块边界一致，不能各扫一套。"""
        src = _structured_source(2) + "\n\n" + _table(5) + "\n\n" + _code(500)
        st = SM.scan_structure(src)
        chunks = DD.plan_chunks(src)
        for c in chunks:
            for t in st["tables"]:
                assert not (c["core_end_line"] >= t["line_start"]
                            and c["core_end_line"] < t["line_end"])
