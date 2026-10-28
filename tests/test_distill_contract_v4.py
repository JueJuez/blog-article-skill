"""N1 基础保护随 N6 迁移到原文素材；v4 的历史规则见 Git。"""
# -*- coding: utf-8 -*-
"""N1 新粒度与交接协议回归测试（PLAN-20261028-long-source-summary §4-N1）。

本轮不是把细条目改个名字，而是换一套**可机械核验的交接契约**：

1. **段级论证单元**：条目以完整观点/论证为单位，依据、案例、步骤、条件、例外与数字
   归属放在同一单元里；不为每个数字单列，也不把一个论证拆成多条。旧契约 v3 的
   「数字逐行成条」正是本轮要改掉的粒度（S9-B 回包 398 条/195 关系的主要来源）。
2. **数字同段不混口径**：一个单元里的多个读数走 `readings` 数组，逐项保留
   对象/单位/期间/基准；两个读数没有任何区分字段就是「混口径」，机械拦下。
3. **必要跨段关系**：段内关系直接写进单元正文；跨单元才看得清的指代/条件/冲突写进
   `deferred`，由全篇统稿处理——局部任务不得替全篇下结论。
4. **实际处理范围**：pack 记录 `coverage.processed_ranges`（1-based 行区间）。
   `complete=True` 就必须真的覆盖全文，有空洞即违约；`complete=False` 如实声明部分读取。
5. **版本绑定**：`contract_version` 与 `schema_version` 都进 pack 与包块 meta；
   旧契约/旧 schema 的 pack 不得被当成本轮有效交接。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from prompts import distill as DT
from prompts.provenance import text_hash
from shared import distill_pack as DP
from shared import distill_dispatch as DD

_TALK = ("这一段口语论述用于占位本身不含数字只是把前因后果和背景交代清楚便于理解上下文"
         "也不会被当成任何统计口径")


def _src(chars: int = 12000) -> str:
    out, n = [], 0
    while n < chars:
        out.append(_TALK + "\n")
        n += len("".join((_TALK + "\n").split()))
    return "".join(out)


def _argument_item(**kw) -> dict:
    base = {
        "id": "i1",
        "type": "argument",
        "stance": "paraphrase",
        "content": ("作者用首单报价与次年涨价说明定价随交付成熟度上调；条件是客户自备货源。"),
        "source_lines": [1, 3],
        "readings": [
            {"value": "8000 元/月", "object": "首单", "unit": "元/月",
             "period": "2019 年", "basis": "月费", "certainty": "确定"},
            {"value": "12000 元/月", "object": "复购单", "unit": "元/月",
             "period": "2021 年", "basis": "月费", "certainty": "确定"},
        ],
    }
    base.update(kw)
    return base


def _parsed(source: str, items=None, deferred=None) -> dict:
    return {
        "mainline": {"question": "怎么定价", "progression": ["首单", "涨价"],
                     "conclusions": ["报价随交付成熟度上调"]},
        "items": items or [_argument_item()],
        "relations": [],
        "deferred": deferred or [],
    }


def _pack(source: str, **kw) -> dict:
    return DP.build_pack(title="长源", url="eval://n1/T1", source_text=source,
                         distill_prompt_sha256=DT.prompt_hash("长源", source),
                         parsed=_parsed(source), **kw)


# ---------------------------------------------------------------- 1. 契约 v4 文案

class TestContractV5Text:
    def test_version_and_marker_in_sync(self):
        assert DT.CONTRACT_VERSION == 5
        assert "v5" in DT.DISTILL_MARKER

    def test_granularity_is_argument_unit_not_per_number(self):
        rules = DT.rules_text()
        assert "完整论证单元" in rules or "完整观点" in rules
        # 旧粒度规则必须退场，否则模型会继续按数字拆条
        assert "数字逐行成条" not in rules
        assert "不为每个数字单列" in rules or "不强制每个数字" in rules

    def test_numbers_keep_caliber_in_one_unit(self):
        rules = DT.rules_text()
        assert "readings" in rules
        for field in ("对象", "单位", "期间", "基准"):
            assert field in rules
        assert "不混口径" in rules or "不得混口径" in rules

    def test_cross_unit_handoff_is_deferred(self):
        rules = DT.rules_text()
        assert "deferred" in rules
        assert "不得替全篇下结论" in rules or "不替全篇下结论" in rules

    def test_example_is_self_consistent(self):
        assert DT.example_issues() == [], DT.example_issues()
        ex = DT.example_schema()
        assert ex.get("deferred") == [] or isinstance(ex.get("deferred"), list)
        assert all(it.get("source_spans") and not it.get("content") for it in ex["items"])

    def test_chunk_task_states_scope_and_no_whole_doc_conclusion(self):
        src = _src()
        chunk = {"index": 1, "total": 2, "start_line": 1, "end_line": 40}
        p = DT.render_distill_prompt("T", src, chunk_label="第 1/2 块（源第 1–40 行）",
                                     start_line=1)
        assert "不得替全篇下结论" in p
        assert "deferred" in p


# ---------------------------------------------------------------- 2. pack schema v2

class TestPackSchemaV3:
    def test_schema_version_bumped(self):
        assert DP.SCHEMA_VERSION == 3

    def test_build_records_contract_and_processed_range(self):
        src = _src()
        pack = _pack(src)
        assert pack["schema_version"] == 3
        assert pack["contract_version"] == DT.CONTRACT_VERSION
        assert pack["coverage"]["processed_ranges"] == [[1, len(src.split("\n"))]]
        assert pack["coverage"]["complete"] is True
        assert pack["deferred"] == []

    def test_build_copies_source_and_preserves_deferred(self):
        src = _src()
        parsed = _parsed(src, deferred=["第 2 块才知道的指代：'他' 指作者本人"])
        pack = DP.build_pack(title="长源", url="eval://n1/norm", source_text=src,
                             distill_prompt_sha256="h", parsed=parsed)
        assert not pack["items"][0].get("readings")
        assert pack["items"][0]["content"] == "\n".join(src.split("\n")[:3])
        assert pack["deferred"] == ["第 2 块才知道的指代：'他' 指作者本人"]

    def test_partial_scope_is_declared_honestly(self):
        src = _src(20000)
        pack = DP.build_pack(title="长源", url="eval://n1/partial", source_text=src,
                             distill_prompt_sha256="h", parsed=_parsed(src),
                             processed_ranges=[[1, 100]], complete=False)
        assert pack["coverage"]["complete"] is False
        assert DP.validate_pack(pack, src)["ok"] is True


# ---------------------------------------------------------------- 3. 版本绑定

class TestVersionBinding:
    def test_contract_version_mismatch_rejected(self):
        src = _src()
        pack = _pack(src)
        pack["contract_version"] = 3
        rep = DP.validate_pack(pack, src)
        assert rep["ok"] is False
        assert any("契约版本" in m for m in rep["issues"])

    def test_missing_contract_version_rejected(self):
        src = _src()
        pack = _pack(src)
        pack.pop("contract_version")
        rep = DP.validate_pack(pack, src)
        assert any("contract_version" in m for m in rep["issues"])

    def test_schema_version_mismatch_rejected(self):
        src = _src()
        pack = _pack(src)
        pack["schema_version"] = 1
        rep = DP.validate_pack(pack, src)
        assert any("schema_version" in m for m in rep["issues"])

    def test_dispatch_load_rejects_old_contract_before_consuming(self, tmp_path):
        src = _src()
        pack = _pack(src)
        pack["contract_version"] = 3
        DP.save_pack(pack, str(tmp_path))
        got, reason = DD.load_valid_pack_for_source("eval://n1/T1", src, title="长源",
                                                    store_dir=str(tmp_path))
        assert got is None
        assert reason == "contract_version_mismatch"

    def test_block_is_current_detects_contract_in_block_meta(self):
        src = _src()
        pack = _pack(src)
        block = DD.render_pack_block(pack, style="lines")
        assert DD.block_is_current(block, src, title="长源") == (True, "ok")
        old = block.replace(f'"contract_version":{DT.CONTRACT_VERSION}',
                            '"contract_version":3')
        assert old != block
        ok, why = DD.block_is_current(old, src, title="长源")
        assert ok is False and why == "contract_upgraded"


# ---------------------------------------------------------------- 4. 二次读数一律拒收

class TestReadingsRejected:
    """v5 起素材正文由同版源提取，模型生成的 readings 不再是合法载体。"""

    def test_two_calibers_without_discriminator_rejected(self):
        src = _src()
        bad = _pack(src)
        bad["items"][0]["readings"] = [
            {"value": "8000 元/月", "object": "", "unit": "", "period": "", "basis": ""},
            {"value": "12000 元/月", "object": "", "unit": "", "period": "", "basis": ""},
        ]
        rep = DP.validate_pack(bad, src)
        assert rep["ok"] is False
        assert any("readings" in m or "读数" in m for m in rep["issues"])

    def test_reading_without_value_rejected(self):
        src = _src()
        bad = _pack(src)
        bad["items"][0]["readings"] = [{"value": "", "period": "2019 年"}]
        rep = DP.validate_pack(bad, src)
        assert any("readings" in m or "读数" in m for m in rep["issues"])

    def test_duplicate_readings_reported(self):
        src = _src()
        bad = _pack(src)
        same = {"value": "8000 元/月", "object": "首单"}
        bad["items"][0]["readings"] = [same, dict(same)]
        rep = DP.validate_pack(bad, src)
        assert any("readings" in m or "读数" in m for m in rep["issues"])

    def test_generated_reading_not_accepted_as_original(self):
        src = _src()
        ok = _pack(src)
        ok["items"][0]["readings"] = [{"value": "8000 元/月"}]
        assert DP.validate_pack(ok, src)["ok"] is False


# ---------------------------------------------------------------- 5. 实际处理范围

class TestProcessedRange:
    def test_claiming_complete_with_hole_rejected(self):
        src = _src(20000)
        pack = _pack(src)
        pack["coverage"]["processed_ranges"] = [[1, 100], [200, len(src.split("\n"))]]
        pack["coverage"]["complete"] = True
        rep = DP.validate_pack(pack, src)
        assert rep["ok"] is False
        assert any("空洞" in m for m in rep["issues"])

    def test_range_out_of_bounds_rejected(self):
        src = _src()
        pack = _pack(src)
        pack["coverage"]["processed_ranges"] = [[1, len(src.split("\n")) + 5]]
        rep = DP.validate_pack(pack, src)
        assert any("越界" in m for m in rep["issues"])

    def test_missing_range_rejected(self):
        src = _src()
        pack = _pack(src)
        pack["coverage"].pop("processed_ranges")
        rep = DP.validate_pack(pack, src)
        assert any("处理范围" in m for m in rep["issues"])

    def test_overlapping_ranges_cover_without_duplicate_lines(self):
        src = _src(20000)
        n = len(src.split("\n"))
        pack = _pack(src)
        pack["coverage"]["processed_ranges"] = [[1, n // 2 + 5], [n // 2 - 5, n]]
        assert DP.validate_pack(pack, src)["ok"] is True


# ---------------------------------------------------------------- 6. deferred 交接

class TestDeferred:
    def test_non_list_deferred_rejected(self):
        src = _src()
        bad = _pack(src)
        bad["deferred"] = "跨块指代待定"
        rep = DP.validate_pack(bad, src)
        assert any("deferred" in m for m in rep["issues"])

    def test_merge_concatenates_deferred_and_records_ranges(self):
        src = _src(90000)
        chunks = DD.plan_chunks(src)
        parts = []
        for c in chunks:
            parsed = _parsed(src, deferred=[f"第{c['index']}块待统稿事项"])
            parts.append({"chunk": c, "parsed": parsed, "prompt_sha256": "h"})
        pack = DP.merge_chunk_packs(title="长源", url="eval://n1/merge", source_text=src,
                                    distill_prompt_sha256=DT.prompt_hash("长源", src),
                                    parts=parts)
        assert pack["coverage"]["complete"] is True
        assert pack["coverage"]["processed_ranges"][0][0] == 1
        assert pack["coverage"]["processed_ranges"][-1][1] == len(src.split("\n"))
        assert len(pack["deferred"]) == len(chunks)
        assert DP.validate_pack(pack, src)["ok"] is True

    def test_merge_preserves_source_spans(self):
        src = _src(90000)
        chunks = DD.plan_chunks(src)
        parts = [{"chunk": c, "parsed": _parsed(src), "prompt_sha256": "h"} for c in chunks]
        pack = DP.merge_chunk_packs(title="长源", url="eval://n1/merge2", source_text=src,
                                    distill_prompt_sha256="h", parts=parts)
        assert all(it["source_spans"] and not it.get("readings") for it in pack["items"])


# ---------------------------------------------------------------- 7. 行式序列化不丢新字段

class TestLinesStyleKeepsNewFields:
    def test_roundtrip_preserves_readings_and_deferred(self):
        src = _src()
        pack = _pack(src)
        pack["deferred"] = ["待统稿：第 3 块的同一指标另说"]
        assert DP.lines_roundtrip_issues(pack) == []

    def test_serialized_lines_contains_source_and_deferred(self):
        src = _src()
        pack = _pack(src)
        pack["deferred"] = ["跨块条件待统稿"]
        text = DP.serialize_pack(pack, style="lines")
        assert DP.parse_pack_lines(text)["items"][0]["content"] == pack["items"][0]["content"]
        assert "跨块条件待统稿" in text


# ---------------------------------------------------------------- 8. 成本可观测

class TestCostTelemetry:
    def test_measure_reports_new_granularity_counters(self):
        src = _src()
        cost = DD.measure_assembly("BASE\n", _pack(src), style="json")
        assert cost["schema_version"] == DP.SCHEMA_VERSION
        assert cost["contract_version"] == DT.CONTRACT_VERSION
        assert cost["items"] == 1
        assert cost["readings"] == 0
        assert cost["deferred"] == 0
        assert cost["source_lines_total"] == len(src.split("\n"))
        assert cost["processed_ranges"] == [[1, len(src.split("\n"))]]
        assert cost["token"] == "unknown" and cost["latency"] == "unknown"

    def test_emit_and_ingest_bind_contract(self, tmp_path):
        src = _src()
        sf = tmp_path / "src.md"
        sf.write_text(src, encoding="utf-8")
        qp = tmp_path / "queue.json"
        qp.write_text(json.dumps([{"url": "eval://n1/bind", "title": "长源", "prompt": "B\n",
                                  "raw_file": str(sf)}], ensure_ascii=False),
                      encoding="utf-8")
        out = tmp_path / "tasks"
        man = DD.emit_distill_tasks(str(qp), str(out))
        assert man["tasks"] and man["tasks"][0]["contract_version"] == DT.CONTRACT_VERSION

        # 回包按任务书写（含 readings / deferred / 有效行号）
        t = man["tasks"][0]
        stem = t["task_file"].replace("_distill.txt", "")
        json.dump(_parsed(src), open(out / f"{stem}.json", "w", encoding="utf-8"),
                  ensure_ascii=False)
        res = DD.ingest_distill_results(str(out), store_dir=str(tmp_path / "packs"))
        assert res["saved"] and not res["failed"], res

    def test_ingest_rejects_task_from_old_contract(self, tmp_path):
        src = _src()
        sf = tmp_path / "src.md"
        sf.write_text(src, encoding="utf-8")
        qp = tmp_path / "queue.json"
        qp.write_text(json.dumps([{"url": "eval://n1/stale", "title": "长源", "prompt": "B\n",
                                  "raw_file": str(sf)}], ensure_ascii=False),
                      encoding="utf-8")
        out = tmp_path / "tasks_old"
        man = DD.emit_distill_tasks(str(qp), str(out))
        man["tasks"][0]["contract_version"] = 3
        json.dump(man, open(out / "manifest.json", "w", encoding="utf-8"), ensure_ascii=False)
        t = man["tasks"][0]
        stem = t["task_file"].replace("_distill.txt", "")
        json.dump(_parsed(src), open(out / f"{stem}.json", "w", encoding="utf-8"),
                  ensure_ascii=False)
        res = DD.ingest_distill_results(str(out), store_dir=str(tmp_path / "packs"))
        assert not res["saved"]
        assert any("契约" in m for it in res["failed"] for m in it["issues"])
