# -*- coding: utf-8 -*-
"""S9 有限接线回归测试（PLAN-20261026-low-cost-quality §8.4 / §9.4，2026-10-27）。

协议要点（§8.4 S9 入口第 1–4 条）：
1. 路由范围：只对**长源**（`source_chars_of` ≥ 10000）复杂论证/多案例走两阶段；
   数值密集源（数字密度 ≥ 0.08，S8 五源校准：D2=0.174 vs 其余 ≤0.027）与
   短源（≤5k）维持现役单轮——误判方向必须落在「保守回单轮」。
2. 材料包块：与 S8 distill 臂逐字节同款标记与格式；生产 prompt 主体逐字节不变
   （assemble = base 前缀 + 包块）；`DISTILL_MARKER` 模板隔离边界不受影响。
3. 版本绑定（§9.6）：源 hash 或提炼契约 hash 失配的旧 pack 不得当有效交接——
   load 不通过就降级单轮，不消费。
4. 断点续跑 / 去重：同版 pack 已存在且有效 → 直接复用，不再重复提炼；
   prepare 幂等（重复运行不二次拼接包块）。
5. 派单留痕：route / fallback_reason / prompt hash 全部进 JSONL 日志；
   fallback 时队列条目保持原样（不阻塞批次，降级=单轮）。
6. provenance：装配后 prompt 与 generation_context.prompt_sha256 一致，
   validate_dispatch 仍通过（extras 为附加键，不改既有字段语义）。

注（2026-10-27 实现时收敛）：prepare 用例原用 3000 字源，与同文件路由断言（≥10000
才走两阶段）自相矛盾；按计划 §8.4「只对长源」改用超阈值源，路由口径不被测试放宽。
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
from prompts.provenance import text_hash


# ---------------------------------------------------------------- 路由

def _long_source(chars: int = 12000, density_text: str = "正文没有数字。") -> str:
    """构造指定字数（source_chars_of 口径≈去空白）的低密度长源。"""
    body = (density_text * 10) + "\n"
    out = []
    n = 0
    while n < chars:
        out.append(body)
        n += len("".join(body.split()))
    return "\n".join(out)


def _numeric_source(chars: int = 12000) -> str:
    """构造数字密集长源（模拟 S8 D2 型：表格/数字行密集）。"""
    line = "2023年Q1营收4540万元，同比增长17.3%，毛利率31.2%，净利率8.7%。\n"
    out = []
    n = 0
    while n < chars:
        out.append(line)
        n += len("".join(line.split()))
    return "\n".join(out)


class TestRoute:
    def test_short_source_stays_single(self):
        d = DD.route_for_source("短源。\n" * 50)
        assert d["route"] == "single"
        assert "short" in d["reason"]

    def test_long_low_density_routes_distill(self):
        d = DD.route_for_source(_long_source())
        assert d["route"] == "distill"
        assert d["signals"]["chars"] >= DD.LONG_SOURCE_MIN_CHARS

    def test_long_numeric_dense_stays_single(self):
        d = DD.route_for_source(_numeric_source())
        assert d["route"] == "single"
        assert "numeric" in d["reason"]

    def test_boundary_exactly_at_threshold_routes_distill(self):
        # 恰好 10000 去空白字符（≥ 判长源）
        src = _long_source(DD.LONG_SOURCE_MIN_CHARS)
        assert DD.route_for_source(src)["route"] == "distill"

    def test_signals_are_reported_not_fabricated(self):
        d = DD.route_for_source(_numeric_source())
        assert d["signals"]["digit_density"] >= DD.DENSE_DIGIT_DENSITY
        assert isinstance(d["signals"]["chars"], int)

    def test_dense_threshold_direction_conservative(self):
        # 密度恰在阈值上：排除（回单轮是安全方向）
        src = _long_source(12000, "数字12345占位。\n")
        d = DD.route_for_source(src)
        assert d["signals"]["digit_density"] > 0  # sanity
        # 高于阈值 → single
        assert DD.route_for_source(_numeric_source())["route"] == "single"


# ---------------------------------------------------------------- 材料包块

def _mk_pack(source: str) -> dict:
    parsed = {
        "mainline": {"question": "怎么入行", "progression": ["先学", "再练"],
                     "conclusions": ["可行"]},
        "items": [{"id": "i1", "type": "step", "stance": "paraphrase",
                   "content": "先做背调再开发信", "source_lines": [1, 2]}],
        "relations": [],
    }
    return DP.build_pack(title="测试", url="eval://s9/T1", source_text=source,
                         distill_prompt_sha256=DT.prompt_hash("测试", source),
                         parsed=parsed)


class TestPackBlock:
    def test_base_prefix_byte_identical(self):
        base = "生产 prompt 主体\n自检段。\n"
        out = DD.assemble_write_prompt(base, _mk_pack("源"))
        assert out.startswith(base)

    def test_markers_and_layout_match_s8(self):
        out = DD.assemble_write_prompt("BASE", _mk_pack("源"))
        assert DD.MATERIAL_PACK_BEGIN in out
        assert DD.MATERIAL_PACK_END in out
        assert "材料包不是大纲" in out
        assert "材料包不是标准答案" in out
        assert out.index(DD.MATERIAL_PACK_BEGIN) > out.index("BASE")
        # JSON 与包说明都在 begin/end 之间
        seg = out.split(DD.MATERIAL_PACK_BEGIN, 1)[1].split(DD.MATERIAL_PACK_END, 1)[0]
        assert '"kind": "distill_pack"' in seg

    def test_idempotent_assemble(self):
        base = "BASE\n"
        p = _mk_pack("源")
        once = DD.assemble_write_prompt(base, p)
        # 对已装配结果再装配不应二次拼接（prepare 幂等的底层保障）
        base2, block = DD.split_write_prompt(once)
        assert block is not None
        again = DD.assemble_write_prompt(base2, p)
        assert again == once

    def test_template_isolation_marker_not_in_block(self):
        out = DD.assemble_write_prompt("BASE", _mk_pack("源"))
        assert DT.DISTILL_MARKER not in out  # 提炼契约不随 pack 进入写作 prompt


# ---------------------------------------------------------------- 版本绑定

class TestLoadValidPack:
    def test_valid_pack_reused(self, tmp_path):
        src = _long_source(3000)
        p = _mk_pack(src)
        DP.save_pack(p, str(tmp_path))
        got, reason = DD.load_valid_pack_for_source("eval://s9/T1", src,
                                                    title="测试", store_dir=str(tmp_path))
        assert reason == "ok" and got is not None

    def test_source_changed_pack_rejected(self, tmp_path):
        src = _long_source(3000)
        DP.save_pack(_mk_pack(src), str(tmp_path))
        got, reason = DD.load_valid_pack_for_source(
            "eval://s9/T1", src + "\n新增一段。\n", title="测试", store_dir=str(tmp_path))
        assert got is None and "source_sha" in reason

    def test_distill_contract_changed_rejected(self, tmp_path):
        src = _long_source(3000)
        DP.save_pack(_mk_pack(src), str(tmp_path))
        # 标题变了 → 提炼 prompt hash 变 → 旧 pack 过期（同源不同契约/任务不消费）
        got, reason = DD.load_valid_pack_for_source(
            "eval://s9/T1", src, title="另一个标题", store_dir=str(tmp_path))
        assert got is None and "distill" in reason

    def test_missing_pack(self, tmp_path):
        got, reason = DD.load_valid_pack_for_source(
            "eval://s9/none", "源文本", title="t", store_dir=str(tmp_path))
        assert got is None and reason == "no_pack"


# ---------------------------------------------------------------- prepare（脚本层）

def _queue_entry(title="测试", url="eval://s9/T1", note_type="general"):
    from prompts.provenance import dispatch_fields
    fields = dispatch_fields(title, "", note_type=note_type, origin="manual")
    return {"title": title, "url": url, "note_type": fields["note_type"],
            "prompt": fields["prompt"], "generation_context": fields["generation_context"],
            "raw_file": ""}


class TestPrepare:
    def _write_queue(self, tmp_path, entries):
        qp = tmp_path / "queue.json"
        qp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(qp)

    def test_prepare_assembles_and_updates_context(self, tmp_path):
        src = _long_source(DD.LONG_SOURCE_MIN_CHARS + 1000)
        sf = tmp_path / "src.md"
        sf.write_text(src, encoding="utf-8")
        DP.save_pack(_mk_pack(src), str(tmp_path))
        entry = _queue_entry()
        entry["raw_file"] = str(sf)
        qp = self._write_queue(tmp_path, [entry])

        rep = DD.prepare_queue_entry(qp, "eval://s9/T1", store_dir=str(tmp_path))
        assert rep["route"] == "distill"
        e = json.loads(json.dumps(rep["entry"]))
        assert DD.MATERIAL_PACK_BEGIN in e["prompt"]
        assert e["generation_context"]["prompt_sha256"] == text_hash(e["prompt"])
        assert e["generation_context"]["distill_pack_key"]
        # 队列文件已写回
        saved = json.load(open(qp, encoding="utf-8"))[0]
        assert saved["prompt"] == e["prompt"]
        # validate_dispatch 仍通过
        from prompts.provenance import validate_dispatch
        validate_dispatch(saved, source_text=src)

    def test_prepare_short_source_untouched(self, tmp_path):
        entry = _queue_entry()
        entry["raw_file"] = ""
        qp = self._write_queue(tmp_path, [entry])
        with pytest.raises(DD.PrepareIneligible):
            DD.prepare_queue_entry(qp, "eval://s9/T1", store_dir=str(tmp_path),
                                   source_text_override="短源。")
        # 条目未被改动
        assert DD.MATERIAL_PACK_BEGIN not in json.load(open(qp, encoding="utf-8"))[0]["prompt"]

    def test_prepare_fallback_on_invalid_pack(self, tmp_path):
        src = _long_source(DD.LONG_SOURCE_MIN_CHARS + 1000)
        sf = tmp_path / "src.md"
        sf.write_text(src, encoding="utf-8")
        p = _mk_pack(src)
        p["source_sha256"] = "0" * 64  # 伪造 → validate 失配
        DP.save_pack(p, str(tmp_path))
        entry = _queue_entry()
        entry["raw_file"] = str(sf)
        qp = self._write_queue(tmp_path, [entry])
        with pytest.raises(DD.PrepareIneligible):
            DD.prepare_queue_entry(qp, "eval://s9/T1", store_dir=str(tmp_path))
        assert DD.MATERIAL_PACK_BEGIN not in json.load(open(qp, encoding="utf-8"))[0]["prompt"]

    def test_prepare_idempotent(self, tmp_path):
        src = _long_source(DD.LONG_SOURCE_MIN_CHARS + 1000)
        sf = tmp_path / "src.md"
        sf.write_text(src, encoding="utf-8")
        DP.save_pack(_mk_pack(src), str(tmp_path))
        entry = _queue_entry()
        entry["raw_file"] = str(sf)
        qp = self._write_queue(tmp_path, [entry])
        r1 = DD.prepare_queue_entry(qp, "eval://s9/T1", store_dir=str(tmp_path))
        r2 = DD.prepare_queue_entry(qp, "eval://s9/T1", store_dir=str(tmp_path))
        assert r2["status"] == "already_prepared"
        saved = json.load(open(qp, encoding="utf-8"))[0]
        assert saved["prompt"] == r1["entry"]["prompt"]  # 未二次拼接

    def test_prepare_logs_decision(self, tmp_path):
        src = _long_source(DD.LONG_SOURCE_MIN_CHARS + 1000)
        sf = tmp_path / "src.md"
        sf.write_text(src, encoding="utf-8")
        DP.save_pack(_mk_pack(src), str(tmp_path))
        entry = _queue_entry()
        entry["raw_file"] = str(sf)
        qp = self._write_queue(tmp_path, [entry])
        log_path = str(tmp_path / "log.jsonl")
        DD.prepare_queue_entry(qp, "eval://s9/T1", store_dir=str(tmp_path),
                               log_path=log_path)
        rec = json.loads(open(log_path, encoding="utf-8").readlines()[-1])
        assert rec["route"] == "distill"
        assert rec["source_sha256"] == text_hash(src)
        assert rec["write_prompt_sha256"]

    def test_stale_block_stripped_when_source_changed(self, tmp_path):
        """换源后旧包块不得当「已完成」：剥掉回单轮，provenance 同步。"""
        src = _long_source(DD.LONG_SOURCE_MIN_CHARS + 1000)
        sf = tmp_path / "src.md"
        sf.write_text(src, encoding="utf-8")
        DP.save_pack(_mk_pack(src), str(tmp_path))
        entry = _queue_entry()
        entry["raw_file"] = str(sf)
        qp = self._write_queue(tmp_path, [entry])
        DD.prepare_queue_entry(qp, "eval://s9/T1", store_dir=str(tmp_path))
        # 源被改（同 url），pack 仍是旧源 → 旧包块必须失效
        new_src = src + "\n补一段新内容。\n"
        sf.write_text(new_src, encoding="utf-8")
        rep = DD.prepare_queue_entry(qp, "eval://s9/T1", store_dir=str(tmp_path))
        assert rep["status"] == "stale_stripped"
        saved = json.load(open(qp, encoding="utf-8"))[0]
        assert DD.MATERIAL_PACK_BEGIN not in saved["prompt"]
        assert saved["generation_context"]["prompt_sha256"] == text_hash(saved["prompt"])
        assert "distill_pack_key" not in saved["generation_context"]


# ---------------------------------------------------------------- 日志

class TestLog:
    def test_append_jsonl(self, tmp_path):
        lp = tmp_path / "log.jsonl"
        DD.log_dispatch({"route": "single", "reason": "short"}, log_path=str(lp))
        DD.log_dispatch({"route": "distill"}, log_path=str(lp))
        lines = open(lp, encoding="utf-8").read().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["route"] == "single"


# ---------------------------------------------------------------- 现役入口接线

class TestFilterPendingHook:
    """`filter_pending.distill_prepare`：默认关；开时才装配，无 pack 一律降级单轮。"""

    def _setup(self, tmp_path, monkeypatch, with_pack: bool):
        from scripts import filter_pending as FP
        src = _long_source(DD.LONG_SOURCE_MIN_CHARS + 1000)
        sf = tmp_path / "src.md"
        sf.write_text(src, encoding="utf-8")
        packdir = tmp_path / "packs"
        packdir.mkdir()
        if with_pack:
            DP.save_pack(_mk_pack(src), str(packdir))
        monkeypatch.setattr(DP, "PACKS_DIR", str(packdir))
        monkeypatch.setenv("NOTE_DISTILL_DISPATCH_LOG", str(tmp_path / "log.jsonl"))
        e = _queue_entry()
        e["raw_file"] = str(sf)
        return FP, e, src

    def test_flag_off_keeps_single_round(self, tmp_path, monkeypatch):
        FP, e, _src = self._setup(tmp_path, monkeypatch, with_pack=True)
        monkeypatch.delenv("NOTE_DISTILL_DISPATCH", raising=False)
        assert FP.distill_prepare(e) == "off"
        assert DD.MATERIAL_PACK_BEGIN not in e["prompt"]

    def test_flag_on_attaches_pack(self, tmp_path, monkeypatch):
        FP, e, src = self._setup(tmp_path, monkeypatch, with_pack=True)
        monkeypatch.setenv("NOTE_DISTILL_DISPATCH", "1")
        assert FP.distill_prepare(e) == "prepared"
        assert DD.MATERIAL_PACK_BEGIN in e["prompt"]
        from prompts.provenance import validate_dispatch
        validate_dispatch(e, source_text=src)      # 装配后 provenance 仍自洽
        assert FP.distill_prepare(e) == "already"  # 幂等，不二次拼接
        assert e["prompt"].count(DD.MATERIAL_PACK_BEGIN) == 1

    def test_flag_on_without_pack_falls_back(self, tmp_path, monkeypatch):
        FP, e, _src = self._setup(tmp_path, monkeypatch, with_pack=False)
        monkeypatch.setenv("NOTE_DISTILL_DISPATCH", "1")
        before = e["prompt"]
        assert FP.distill_prepare(e) == "fallback"
        assert e["prompt"] == before

    def test_refresh_then_prepare_order_keeps_block(self, tmp_path, monkeypatch):
        """refresh 会重建 prompt 主体 → 装配必须在其之后（顺序错了包块会被抹掉）。"""
        FP, e, _src = self._setup(tmp_path, monkeypatch, with_pack=True)
        monkeypatch.setenv("NOTE_DISTILL_DISPATCH", "1")
        FP.refresh_prompt(e)
        assert DD.MATERIAL_PACK_BEGIN not in e["prompt"]
        assert FP.distill_prepare(e) == "prepared"
        assert DD.MATERIAL_PACK_BEGIN in e["prompt"]
