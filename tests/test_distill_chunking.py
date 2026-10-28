# -*- coding: utf-8 -*-
""">40k 超长源分块提炼回归测试（S9 第 8 条③，2026-10-27）。

为什么要有这块：S9 真实抽样里 B 篇 52.8k 字一次提炼产出 330 条目 / 105.6KB pack、
写作 prompt 79,496 字符——链路扛住了，但「单次任务能承载多大」没有边界结论（REPORT §5.1）。
分块提炼的三条硬要求：

1. **不漏行**：相邻块的并集必须覆盖整篇每一行（overlap 只会过量，不留空洞）；
2. **坐标统一**：每块看到的行号是**全局行号**（`NNN| ` 前缀下发），
   合并时不需要任何换算——换算就是给漂移留位置（S9-B 行号漂 5 行已被拦过一次）；
3. **不全则以整篇为单位失败**：缺一块或任一块校验不过 → 整篇不落盘。
   半份 pack 会让写作端误以为全覆盖，等于把遗漏藏起来。
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

_TALK = ("这一段口语论述用于占位本身不含数字只是把前因后果和背景交代清楚便于理解上下文")


def _big_source(chars: int = 60000) -> str:
    out, n, k = [], 0, 0
    while n < chars:
        k += 1
        line = f"第{k}段：{_TALK}"
        out.append(line)
        n += len("".join(line.split()))
    return "\n".join(out)


def _small_source(chars: int = 12000) -> str:
    return _big_source(chars)


# ---------------------------------------------------------------- 切块规划

class TestPlanChunks:
    def test_short_source_single_chunk_covers_all(self):
        src = _small_source()
        chunks = DD.plan_chunks(src)
        assert len(chunks) == 1
        c = chunks[0]
        assert c["index"] == 1 and c["total"] == 1
        assert (c["start_line"], c["end_line"]) == (1, len(src.split("\n")))
        assert (c["core_start_line"], c["core_end_line"]) == (1, len(src.split("\n")))
        assert c["chars"] == len("".join(src.split()))

    def test_long_source_splits_into_multiple(self):
        chunks = DD.plan_chunks(_big_source())
        assert len(chunks) >= 2
        assert [c["index"] for c in chunks] == list(range(1, len(chunks) + 1))
        assert all(c["total"] == len(chunks) for c in chunks)

    def test_covers_every_line_no_holes(self):
        src = _big_source()
        chunks = DD.plan_chunks(src)
        total = len(src.split("\n"))
        assert chunks[0]["start_line"] == 1
        assert chunks[-1]["end_line"] == total
        for a, b in zip(chunks, chunks[1:]):
            # 下一块的起点不得越过上一块终点（否则就是空洞）
            assert b["start_line"] <= a["end_line"] + 1, (a, b)

    def test_chunk_size_below_threshold(self):
        src = _big_source()
        for c in DD.plan_chunks(src):
            # 允许溢出：单行超过阈值时无法再切
            longest = max(len("".join(l.split())) for l in src.split("\n"))
            assert c["chars"] <= DD.CHUNK_MAX_CHARS + longest

    def test_overlap_provides_context(self):
        chunks = DD.plan_chunks(_big_source())
        assert len(chunks) >= 2
        a, b = chunks[0], chunks[1]
        overlap = a["end_line"] - b["start_line"] + 1
        assert 1 <= overlap <= DD.CHUNK_OVERLAP_LINES + 1

    def test_empty_source_no_chunks(self):
        assert DD.plan_chunks("") == []


# ---------------------------------------------------------------- 分块任务渲染

class TestChunkRender:
    def test_single_chunk_prompt_is_whole_source(self):
        src = _small_source()
        chunk = DD.plan_chunks(src)[0]
        p = DD.render_chunk_prompt("标题", src, chunk)
        assert DT.numbered_source(src) in p
        assert "块" not in p.split("<<<SOURCE_BEGIN>>>")[0]  # 不需声明分块

    def test_multi_chunk_prompt_carries_global_line_numbers(self):
        src = _big_source()
        chunks = DD.plan_chunks(src)
        p = DD.render_chunk_prompt("标题", src, chunks[1])
        assert f"第 2/{len(chunks)} 块" in p
        assert f"源第 {chunks[1]['start_line']}–{chunks[1]['end_line']} 行" in p
        # 全局行号：片段第一行前缀必须是块的 start_line
        assert f"{chunks[1]['start_line']}| " in p
        # 片段之外的行不得出现（证明任务里真的是节选而不是整篇）
        assert f"{chunks[1]['start_line'] - 1}| " not in p

    def test_chunk_text_is_the_segment_only(self):
        src = _big_source()
        c = DD.plan_chunks(src)[1]
        frag = DD.chunk_source_text(src, c["start_line"], c["end_line"])
        assert frag == "\n".join(src.split("\n")[c["start_line"] - 1:c["end_line"]])

    def test_chunk_filename_convention(self):
        assert DD.chunk_filename("k123", {"index": 1, "total": 1}) == "k123_distill.txt"
        assert DD.chunk_filename("k123", {"index": 2, "total": 3}) == "k123_c2of3_distill.txt"


# ---------------------------------------------------------------- 合并

def _parsed(idx: int, start_line: int, extra_item_id: str = "") -> dict:
    # 条目必须落在**核心区间**内：分块后下发范围含重叠上下文，条目写在那里会被 scope 门拦下
    items = [{"id": "i1", "type": "number", "stance": "paraphrase",
              "content": f"第{idx}块的读数12元附近",
              "source_lines": [start_line, start_line]}]
    if extra_item_id:
        items.append({"id": extra_item_id, "type": "viewpoint", "stance": "paraphrase",
                      "content": f"第{idx}块的观点",
                      "source_lines": [start_line + 1, start_line + 1]})
    return {
        "mainline": {"question": f"Q{idx}", "progression": [f"推进{idx}"],
                     "conclusions": [f"结论{idx}"]},
        "items": items,
        "relations": [{"from": "i1", "to": extra_item_id or "i1", "kind": "supports",
                       "note": "证据"}],
    }


class TestMergeChunkPacks:
    def _merge(self, src, n_chunks=2):
        chunks = DD.plan_chunks(src) if n_chunks else DD.plan_chunks(src)
        parts = [{"chunk": c, "parsed": _parsed(c["index"], c["core_start_line"], "i2"),
                  "prompt_sha256": "h%d" % c["index"]} for c in chunks]
        return DP.merge_chunk_packs(title="T", url="eval://s9/chunk", source_text=src,
                                    distill_prompt_sha256=DT.prompt_hash("T", src),
                                    parts=parts), chunks

    def test_ids_prefixed_and_unique(self):
        pack, _ = self._merge(_big_source())
        ids = [it["id"] for it in pack["items"]]
        assert len(ids) == len(set(ids))
        assert all(i.startswith(("c1_", "c2_", "c3_")) for i in ids)

    def test_source_lines_are_global_and_valid(self):
        src = _big_source()
        pack, _ = self._merge(src)
        rep = DP.validate_pack(pack, src)
        assert rep["ok"], rep["issues"]  # 行号无需换算即可通过整篇校验

    def test_relations_remapped_not_dangling(self):
        src = _big_source()
        pack, _ = self._merge(src)
        ids = {it["id"] for it in pack["items"]}
        assert pack["relations"]
        for r in pack["relations"]:
            assert r["from"] in ids and r["to"] in ids

    def test_overlap_duplicates_dropped_and_declared(self):
        src = _big_source()
        chunks = DD.plan_chunks(src)
        # 两块提炼出完全相同的条目（重叠区常见）→ 应去重并如实声明
        same = {"id": "i1", "type": "number", "stance": "paraphrase",
                "content": "重叠区的同一个读数", "source_lines": [chunks[0]["end_line"]] * 2}
        parts = [{"chunk": c, "parsed": {"mainline": {"question": "q", "progression": [],
                                                      "conclusions": []},
                                         "items": [dict(same)], "relations": []},
                  "prompt_sha256": "h"} for c in chunks]
        pack = DP.merge_chunk_packs(title="T", url="eval://s9/dup", source_text=src,
                                    distill_prompt_sha256="x", parts=parts)
        assert len(pack["items"]) == 1
        assert "去重" in pack["notes"]

    def test_merge_is_declared_not_silent(self):
        pack, chunks = self._merge(_big_source())
        assert pack["coverage"]["chunk_count"] == len(chunks)
        assert "分块提炼" in pack["notes"]
        assert "跨块关系未生成" in pack["notes"]
        # 版本绑定仍用整篇 hash（不是块 hash）
        assert pack["distill_prompt_sha256"] == DT.prompt_hash("T", _big_source())
        assert pack["chunk_prompt_sha256s"] == ["h1", "h2"]

    def test_mainline_concatenated_in_order(self):
        pack, chunks = self._merge(_big_source())
        assert pack["mainline"]["question"] == "Q1"
        assert pack["mainline"]["progression"] == [f"推进{c['index']}" for c in chunks]


# ---------------------------------------------------------------- 端到端（emit → 回包 → ingest）

def _write_manifest(d: str, src_path: str, src: str, key: str = "abc123") -> list:
    chunks = DD.plan_chunks(src)
    from prompts.provenance import text_hash
    tasks = []
    for c in chunks:
        name = DD.chunk_filename(key, c)
        open(os.path.join(d, name), "w", encoding="utf-8").write(
            DD.render_chunk_prompt("T", src, c))
        tasks.append({"url": "eval://s9/e2e", "url_key": key, "title": "T",
                      "source_path": src_path, "source_sha256": text_hash(src),
                      "contract_version": DT.CONTRACT_VERSION,
                      "schema_version": DP.SCHEMA_VERSION,
                      "distill_prompt_sha256": "h%d" % c["index"],
                      "source_chars": len("".join(src.split())),
                      "task_file": name, "chunk": c})
    json.dump({"contract_version": DT.CONTRACT_VERSION, "tasks": tasks, "skipped": []},
              open(os.path.join(d, "manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return chunks


class TestIngestChunks:
    def test_all_chunks_present_merges_one_pack(self, tmp_path):
        src = _big_source()
        src_path = tmp_path / "source.md"
        src_path.write_text(src, encoding="utf-8")
        d = tmp_path / "in"
        d.mkdir()
        chunks = _write_manifest(str(d), str(src_path), src)
        for c in chunks:
            stem = DD.chunk_filename("abc123", c).replace("_distill.txt", "")
            json.dump(_parsed(c["index"], c["core_start_line"], "i2"),
                      open(d / f"{stem}.json", "w", encoding="utf-8"), ensure_ascii=False)
        res = DD.ingest_distill_results(str(d), store_dir=str(tmp_path / "packs"))
        assert len(res["saved"]) == 1 and not res["failed"]
        saved = json.load(open(res["saved"][0]["pack"], encoding="utf-8"))
        assert saved["items"] and len({i["id"] for i in saved["items"]}) == len(saved["items"])
        assert saved["coverage"]["chunk_count"] == len(chunks)

    def test_missing_one_chunk_refuses_whole_pack(self, tmp_path):
        src = _big_source()
        src_path = tmp_path / "source.md"
        src_path.write_text(src, encoding="utf-8")
        d = tmp_path / "in"
        d.mkdir()
        chunks = _write_manifest(str(d), str(src_path), src)
        for c in chunks[:-1]:  # 故意少给一块
            stem = DD.chunk_filename("abc123", c).replace("_distill.txt", "")
            json.dump(_parsed(c["index"], c["core_start_line"], "i2"),
                      open(d / f"{stem}.json", "w", encoding="utf-8"), ensure_ascii=False)
        res = DD.ingest_distill_results(str(d), store_dir=str(tmp_path / "packs"))
        # 缺块 → 双向留痕：missing 记缺哪个任务文件，failed 记因此整篇未落盘
        assert not res["saved"]
        assert res["missing"] and res["missing"][0]["task_file"].endswith("_distill.txt")
        assert any("缺" in m for it in res["failed"] for m in it["issues"])

    def test_bad_line_number_in_one_block_refuses_pack(self, tmp_path):
        src = _big_source()
        src_path = tmp_path / "source.md"
        src_path.write_text(src, encoding="utf-8")
        d = tmp_path / "in"
        d.mkdir()
        n_lines = len(src.split("\n"))
        chunks = _write_manifest(str(d), str(src_path), src)
        for i, c in enumerate(chunks):
            parsed = _parsed(c["index"], c["core_start_line"], "i2")
            if i == len(chunks) - 1:
                parsed["items"][0]["source_lines"] = [n_lines - 1, n_lines + 50]  # 越界
            stem = DD.chunk_filename("abc123", c).replace("_distill.txt", "")
            json.dump(parsed, open(d / f"{stem}.json", "w", encoding="utf-8"),
                      ensure_ascii=False)
        res = DD.ingest_distill_results(str(d), store_dir=str(tmp_path / "packs"))
        assert not res["saved"]
        # N2 起先由「核心区间」门拦（越界行必然越出核心），validate_pack 的越界检查仍兜底
        assert any(("越界" in m or "核心区间" in m)
                   for it in res["failed"] for m in it["issues"])
