# -*- coding: utf-8 -*-
"""N3 全篇统稿与装配消费回归测试（PLAN-20261028-long-source-summary §4-N3）。

N3 的出口不是「多写一段 prompt」，而是四件可机械核验的事：

1. **全块完整回收才允许形成可统稿材料**：派单清单块序必须 1..total（清单被 `--limit`
   截断时回包数照样凑得上，但源没处理完）；合并后处理范围不覆盖全文不落盘；
   消费侧 `load_valid_pack_for_source` 对 `complete=False` / 块序不全的 pack 一律拒绝。
2. **统稿核对跨块条件/指代/冲突**：多块或有 deferred 的 pack，装配块必须带
   `prompts.synthesis` 的统稿契约（deferred 逐条、块边界、块级主线不等于全篇结论）。
3. **同版原文可按行段读取；读不到就保留缺口**：装配块写回源文件路径/行数，
   `source_lines` 语义明确；路径缺失时明说回源不可用，不得拿材料包二手概括补足。
4. **装配契约有版本**：N3 之前装配的块没有 `HANDOFF_MARKER` → 判 `handoff_upgraded`
   重装（只重装 prompt，不重跑提炼）。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from prompts import distill as DT
from prompts import synthesis as SY
from prompts.provenance import text_hash
from shared import distill_pack as DP
from shared import distill_dispatch as DD
from conftest import v6ize

_TALK = ("这一段口语论述用来占位本身不含数字只是把前因后果和背景交代清楚便于理解上下文"
         "不会进入任何统计口径")


def _src(chars: int = 90000) -> str:
    out, n = [], 0
    while n < chars:
        out.append(_TALK + "。\n")
        n += len("".join((_TALK + "。").split()))
    return "".join(out)


def _parsed(core_start: int, tag: str = "论证") -> dict:
    return v6ize({"mainline": {"question": "q", "progression": [], "conclusions": []},
                  "relations": [],
                  "items": [{"id": "i1", "type": "argument", "stance": "paraphrase",
                             "content": f"第{tag}块的核心论证",
                             "source_lines": [core_start, core_start]}],
                  "deferred": []})


def _merged_pack(src: str, deferred_each: bool = False) -> dict:
    chunks = DD.plan_chunks(src)
    parts = []
    for c in chunks:
        parsed = _parsed(c["core_start_line"], tag=str(c["index"]))
        if deferred_each:
            parsed["deferred"] = [f"第{c['index']}块：这里的「他」指谁要跨块确认"]
        parts.append({"chunk": c, "parsed": parsed, "prompt_sha256": "h"})
    return DP.merge_chunk_packs(title="T", url="eval://n3/merge", source_text=src,
                                distill_prompt_sha256=DT.prompt_hash("T", src), parts=parts)


def _single_pack(src: str) -> dict:
    return DP.build_pack(title="T", url="eval://n3/single", source_text=src,
                         distill_prompt_sha256=DT.prompt_hash("T", src),
                         parsed=_parsed(1))


def _write_queue(tmp_path, entries) -> str:
    qp = tmp_path / "queue.json"
    qp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(qp)


def _long_entry(tmp_path, url: str = "eval://n3/q") -> tuple:
    src = _src(DD.LONG_SOURCE_MIN_CHARS + 2000)
    sf = tmp_path / "src.md"
    sf.write_text(src, encoding="utf-8")
    from prompts.provenance import dispatch_fields
    fields = dispatch_fields("T", "", note_type="general", origin="manual")
    entry = {"url": url, "title": "T", "note_type": fields["note_type"],
             "prompt": fields["prompt"], "generation_context": fields["generation_context"],
             "raw_file": str(sf)}
    return entry, src


# ---------------------------------------------------------------- 1. 全块完整回收

class TestFullRecovery:
    def _emit(self, tmp_path, src: str, url: str = "eval://n3/full"):
        sf = tmp_path / "src.md"
        sf.write_text(src, encoding="utf-8")
        qp = _write_queue(tmp_path, [{"url": url, "title": "T", "prompt": "B\n",
                                      "raw_file": str(sf)}])
        out = tmp_path / "tasks"
        man = DD.emit_distill_tasks(qp, str(out))
        assert len(man["tasks"]) >= 2, "本用例需要多块源"
        return out, man

    def _write_results(self, out, tasks, only=None):
        for t in tasks:
            if only is not None and t["chunk"]["index"] not in only:
                continue
            stem = t["task_file"].replace("_distill.txt", "")
            json.dump(_parsed(t["chunk"]["core_start_line"], tag=str(t["chunk"]["index"])),
                      open(out / f"{stem}.json", "w", encoding="utf-8"), ensure_ascii=False)

    def test_truncated_manifest_rejected(self, tmp_path):
        """清单被 --limit 截断：回包份数与清单份数相等，但源没处理完 → 不能落盘。"""
        out, man = self._emit(tmp_path, _src())
        self._write_results(out, man["tasks"])
        truncated = dict(man)
        truncated["tasks"] = man["tasks"][:1]
        json.dump(truncated, open(out / "manifest.json", "w", encoding="utf-8"),
                  ensure_ascii=False)
        res = DD.ingest_distill_results(str(out), store_dir=str(tmp_path / "packs"))
        assert not res["saved"]
        assert any("派单清单" in m for it in res["failed"] for m in it["issues"])

    def test_hole_between_chunks_rejected(self, tmp_path):
        """份数齐了但块区间之间有洞 → 合并后不覆盖全文，整篇不落盘。"""
        src = _src(90000)
        sf = tmp_path / "src.md"
        sf.write_text(src, encoding="utf-8")
        n = len(src.split("\n"))
        url = "eval://n3/hole"
        key = DP.url_key_of(url, src)
        out = tmp_path / "tasks"
        out.mkdir()
        spec = [(1, 10), (21, n)]          # 11–20 行被漏掉
        tasks = []
        for idx, (a, b) in enumerate(spec, 1):
            tasks.append({"url": url, "url_key": key, "title": "T",
                          "source_path": str(sf), "source_sha256": text_hash(src),
                          "contract_version": DT.CONTRACT_VERSION,
                          "schema_version": DP.SCHEMA_VERSION,
                          "distill_prompt_sha256": "h", "source_chars": len(src),
                          "task_file": f"{key}_c{idx}of2_distill.txt",
                          "chunk": {"index": idx, "total": 2,
                                    "start_line": a, "end_line": b,
                                    "core_start_line": a, "core_end_line": b,
                                    "chars": 0, "context_lines": 0,
                                    "continued": False, "continues": False}})
            json.dump(_parsed(a, tag=str(idx)),
                      open(out / f"{key}_c{idx}of2.json", "w", encoding="utf-8"),
                      ensure_ascii=False)
        json.dump({"tasks": tasks, "skipped": []},
                  open(out / "manifest.json", "w", encoding="utf-8"), ensure_ascii=False)
        res = DD.ingest_distill_results(str(out), store_dir=str(tmp_path / "packs"))
        assert not res["saved"]
        assert any("未完整覆盖全文" in m for it in res["failed"] for m in it["issues"])

    def test_complete_recovery_is_saved(self, tmp_path):
        out, man = self._emit(tmp_path, _src())
        self._write_results(out, man["tasks"])
        res = DD.ingest_distill_results(str(out), store_dir=str(tmp_path / "packs"))
        assert res["saved"] and not res["failed"], res
        assert res["saved"][0]["chunks"] == len(man["tasks"])

    def test_missing_one_result_rejected(self, tmp_path):
        out, man = self._emit(tmp_path, _src())
        self._write_results(out, man["tasks"], only={1})
        res = DD.ingest_distill_results(str(out), store_dir=str(tmp_path / "packs"))
        assert not res["saved"]
        assert res["missing"]


class TestConsumptionGate:
    def test_incomplete_pack_not_loaded(self, tmp_path):
        src = _src(DD.LONG_SOURCE_MIN_CHARS + 2000)
        p = DP.build_pack(title="T", url="eval://n3/partial", source_text=src,
                          distill_prompt_sha256=DT.prompt_hash("T", src),
                          parsed=_parsed(1), processed_ranges=[[1, 100]], complete=False)
        DP.save_pack(p, str(tmp_path))
        got, reason = DD.load_valid_pack_for_source("eval://n3/partial", src, title="T",
                                                    store_dir=str(tmp_path))
        assert got is None
        assert reason.startswith("incomplete_coverage")

    def test_prepare_strips_block_when_pack_incomplete(self, tmp_path):
        """有旧包块但同版 pack 缺块 → 剥块回单轮，不消费不完整材料。"""
        entry, src = _long_entry(tmp_path, "eval://n3/partial2")
        p = DP.build_pack(title="T", url="eval://n3/partial2", source_text=src,
                          distill_prompt_sha256=DT.prompt_hash("T", src),
                          parsed=_parsed(1), processed_ranges=[[1, 100]], complete=False)
        DP.save_pack(p, str(tmp_path))
        entry["prompt"] = DD.assemble_write_prompt(entry["prompt"], p)
        rep = DD.prepare_entry(entry, store_dir=str(tmp_path), source_text=src)
        assert rep["status"] == "stale_stripped"
        assert DD.MATERIAL_PACK_BEGIN not in entry["prompt"]
        assert "distill_pack_key" not in entry["generation_context"]


# ---------------------------------------------------------------- 2. 合并完整性

class TestMergeIntegrity:
    def test_same_content_different_readings_both_kept(self):
        src = _src(20000)
        n = len(src.split("\n"))
        half = n // 2
        content = "同一句结论，但两块的读数口径不同。"
        raw = src.split("\n")
        raw[0] = "2023 年有 11 个点"
        raw[half] = "2024 年读数 4.78"
        src = "\n".join(raw)
        chunks = [
            {"index": 1, "total": 2, "start_line": 1, "end_line": half,
             "core_start_line": 1, "core_end_line": half},
            {"index": 2, "total": 2, "start_line": half + 1, "end_line": n,
             "core_start_line": half + 1, "core_end_line": n},
        ]
        parts = [
            {"chunk": chunks[0], "prompt_sha256": "h", "parsed": v6ize({
                "mainline": {"question": "q"}, "relations": [],
                "items": [{"id": "i1", "type": "argument", "stance": "paraphrase",
                           "content": content, "source_lines": [1, 1],
                           "readings": [{"value": "11 个点", "period": "2023 年"}]}]})},
            {"chunk": chunks[1], "prompt_sha256": "h", "parsed": v6ize({
                "mainline": {"question": "q"}, "relations": [],
                "items": [{"id": "i1", "type": "argument", "stance": "paraphrase",
                           "content": content, "source_lines": [half + 1, half + 1],
                           "readings": [{"value": "4.78", "period": "2024 年"}]}]})},
        ]
        pack = DP.merge_chunk_packs(title="T", url="eval://n3/readings", source_text=src,
                                    distill_prompt_sha256="h", parts=parts)
        assert len(pack["items"]) == 2, "同 content 不同读数不得被去重丢掉"
        assert "11 个点" in pack["items"][0]["content"]
        assert "4.78" in pack["items"][1]["content"]
        assert all(not it.get("readings") for it in pack["items"])

    def test_chunk_order_and_declared_total_recorded(self):
        src = _src()
        pack = _merged_pack(src)
        cov = pack["coverage"]
        assert cov["chunk_count"] == len(DD.plan_chunks(src))
        assert cov["chunk_indices"] == list(range(1, cov["chunk_count"] + 1))
        assert cov["chunk_total_declared"] == [cov["chunk_count"]]
        assert DP.coverage_issues(pack) == []


# ---------------------------------------------------------------- 3. 统稿契约

class TestSynthesisContract:
    def test_multi_chunk_block_carries_synthesis_section(self):
        src = _src()
        pack = _merged_pack(src, deferred_each=True)
        ref = {"path": "notes/_raw/x.md", "lines": len(src.split("\n")),
               "sha256": text_hash(src)}
        block = DD.render_pack_block(pack, style="json", source_ref=ref)
        assert SY.HANDOFF_MARKER in block
        assert "## 全篇统稿" in block
        assert "不对材料包做二次压缩" in block
        assert "跨块对账" in block
        # deferred 逐条列出（不是只说「见 deferred」）
        assert "这里的「他」指谁" in block
        # 块边界给出核心区间
        assert "第1块 核心 1–" in block
        # 回源引用
        assert "notes/_raw/x.md" in block and "1-based" in block

    def test_single_chunk_has_handoff_but_no_synthesis(self):
        pack = _single_pack(_src(12000))
        block = DD.render_pack_block(pack, style="json")
        assert SY.HANDOFF_MARKER in block
        assert "## 全篇统稿" not in block
        assert "回源核对不可用" in block      # 未传 source_ref

    def test_lines_style_carries_synthesis(self):
        pack = _merged_pack(_src(), deferred_each=True)
        block = DD.render_pack_block(pack, style="lines")
        assert SY.HANDOFF_MARKER in block
        assert "全篇统稿" in block
        assert "@f " in block       # deferred 在行式正文里也没丢

    def test_block_without_handoff_marker_is_stale(self):
        src = _src(12000)
        pack = _single_pack(src)
        block = DD.render_pack_block(pack, style="json")
        old = block.replace(SY.HANDOFF_MARKER, "").strip()
        assert old != block
        assert DD.block_is_current(old, src, title="T") == (False, "handoff_upgraded")

    def test_prepare_reassembles_old_handoff_block(self, tmp_path):
        entry, src = _long_entry(tmp_path, "eval://n3/upgrade")
        pack = DP.build_pack(title="T", url="eval://n3/upgrade", source_text=src,
                             distill_prompt_sha256=DT.prompt_hash("T", src),
                             parsed=_parsed(1))
        DP.save_pack(pack, str(tmp_path))
        old_block = DD.render_pack_block(pack, style="json").replace(SY.HANDOFF_MARKER, "")
        entry["prompt"] = entry["prompt"].rstrip("\n") + "\n\n" + old_block
        rep = DD.prepare_entry(entry, store_dir=str(tmp_path), source_text=src)
        assert rep["status"] == "prepared"
        assert entry["prompt"].count(DD.MATERIAL_PACK_BEGIN) == 1
        assert SY.HANDOFF_MARKER in entry["prompt"]

    def test_measure_reports_synthesis(self):
        src = _src()
        pack = _merged_pack(src, deferred_each=True)
        cost = DD.measure_assembly("BASE\n", pack)
        assert cost["needs_synthesis"] is True
        assert cost["synthesis_boundaries"] == pack["coverage"]["chunk_count"]
        assert cost["chunk_count"] == pack["coverage"]["chunk_count"]


# ---------------------------------------------------------------- 4. 回源与 provenance

class TestSourceRef:
    def test_missing_path_declares_gap_instead_of_substitute(self):
        pack = _single_pack(_src(12000))
        block = DD.render_pack_block(pack, style="json", source_ref={"path": ""})
        assert "回源核对不可用" in block
        assert "如实保留缺口" in block
        assert "不得拿材料包里的二手概括补足" in block

    def test_prepare_records_source_path_lines_and_chunks(self, tmp_path):
        entry, src = _long_entry(tmp_path, "eval://n3/prov")
        pack = DP.build_pack(title="T", url="eval://n3/prov", source_text=src,
                             distill_prompt_sha256=DT.prompt_hash("T", src),
                             parsed=_parsed(1))
        DP.save_pack(pack, str(tmp_path))
        rep = DD.prepare_entry(entry, store_dir=str(tmp_path), source_text=src)
        assert rep["status"] == "prepared"
        gc = entry["generation_context"]
        assert gc["distill_source_path"] == entry["raw_file"]
        assert gc["distill_source_lines"] == len(src.split("\n"))
        assert gc["distill_chunks"] == 1
        assert gc["distill_synthesis"] is False
        assert entry["raw_file"] in entry["prompt"]

    def test_plan_reports_pack_not_ok_for_incomplete(self, tmp_path):
        entry, src = _long_entry(tmp_path, "eval://n3/plan")
        p = DP.build_pack(title="T", url="eval://n3/plan", source_text=src,
                          distill_prompt_sha256=DT.prompt_hash("T", src),
                          parsed=_parsed(1), processed_ranges=[[1, 50]], complete=False)
        DP.save_pack(p, str(tmp_path))
        qp = _write_queue(tmp_path, [entry])
        plan = DD.plan_queue(qp, store_dir=str(tmp_path))
        row = plan["rows"][0]
        assert row["pack_ok"] is False
        assert row["pack"].startswith("incomplete_coverage")
        assert row["chunks"] >= 1
