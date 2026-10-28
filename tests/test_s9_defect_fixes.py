# -*- coding: utf-8 -*-
"""S9 缺陷修正回归测试（PLAN-20261026-low-cost-quality §8.5 第 1 条，2026-10-27）。

第 1 条要修的三类确定缺陷，本文件按 RED 先行的方式钉住：

1. **`prepare_entry` 先判路由再拆旧包**：已装配条目转为 `single`（源变短/数字密集/
   数字总量大）或**源不可读**时，旧材料包块不能被原样留在条目里继续消费——
   必须剥掉回单轮，并同步清 `distill_*` provenance（`pitfall`：不能靠
   `filter_pending.refresh_prompt` 先行重建来掩盖，那条路径不会保证先跑）。
2. **重复装配不得叠加包块**：历史上出现过同一篇挂两个包（隔离试验脚本 double-assemble
   的产物），老逻辑 `split_write_prompt` 只切第一刀会留下第二个。prepare 必须归一到
   **恰好一个**包块。`_tmp/` 试验脚本不接生产， restorative 归一在现役协议里做。
3. **提炼契约 quote 规则与示例自相矛盾**：i4 标 `quote` 却带「（源此处疑似读数断字…）」
   这类**模型注解**——注解让 content 不再是逐字原话，只能 paraphrase。示例自身要有
   机械自检（`prompts.distill.example_issues`），且**不得**为放行注解而取消
   `validate_pack` 对真实回包的 quote 机检。

另有 `emit_distill_tasks` 的 `url_key` 缺陷（变量名 `key` 未定义 → 派单直接 NameError）
一并钉在同一文件里——派单这一步没跑通过的情况下，后面的分块与回收证据都不成立。
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


# ---------------------------------------------------------------- 素材

_TALK = ("这一段是口语论述用来稀释数字密度本身不含任何读数只是把前因后果和相关背景交代清楚"
         "便于读者理解上下文不计入任何统计口径也不会被当成数据另外还要注意这段不是表格也不是清单")

_NUM_LINE = "2023年Q1营收4540万元，同比增长17.3%，毛利率31.2%，净利率8.7%。"


def _fill(line: str, chars: int) -> str:
    out, n = [], 0
    while n < chars:
        out.append(line + "\n")
        n += len("".join((line + "\n").split()))
    return "".join(out)


def _long_source(chars: int = 12000) -> str:
    return _fill(_TALK, chars)


def _numeric_source(chars: int = 12000) -> str:
    return _fill(_NUM_LINE, chars)


def _numeric_heavy_source(chars: int = 15000) -> str:
    """低密度但数字串绝对量大（S9-B 型）→ 路由回单轮。"""
    out, n, k = [], 0, 0
    while n < chars:
        k += 1
        out.append(f"第{k:03d}个观察点在{k * 7 + 100}元附近。{_TALK}\n")
        n += len("".join(out[-1].split()))
    return "".join(out)


def _mk_pack(source: str, url: str = "eval://s9/T1", title: str = "测试",
             prompt_hash_tag: str = "") -> dict:
    parsed = {
        "mainline": {"question": "怎么入行", "progression": ["先学", "再练"],
                     "conclusions": ["可行"]},
        "items": [{"id": "i1", "type": "step", "stance": "paraphrase",
                   "content": "先做背调再开发信", "source_lines": [1, 2]}],
        "relations": [],
    }
    return DP.build_pack(title=title, url=url, source_text=source,
                         distill_prompt_sha256=prompt_hash_tag
                         or DT.prompt_hash(title, source), parsed=parsed)


def _entry(url: str, title: str, prompt: str, gc: dict | None = None) -> dict:
    return {"url": url, "title": title, "prompt": prompt,
            "generation_context": dict(gc or {}), "raw_file": ""}


def _assembled_entry(tmp_path, src: str, url: str = "eval://s9/T1",
                     title: str = "测试") -> tuple:
    """造一条「已经装配过材料包块」的队列条目，返回 (entry, base_prompt)。"""
    base = "生产 prompt 主体\n自检段。\n"
    pack = _mk_pack(src, url=url, title=title)
    prompt = DD.assemble_write_prompt(base, pack)
    sf = tmp_path / "src.md"
    sf.write_text(src, encoding="utf-8")
    entry = _entry(url, title, prompt, {
        "prompt_sha256": text_hash(prompt),
        "distill_pack_key": pack.get("url_key") or "",
        "distill_pack_source_sha256": pack.get("source_sha256") or "",
        "distill_prompt_sha256": pack.get("distill_prompt_sha256") or "",
    })
    entry["raw_file"] = str(sf)
    return entry, base


def _write_source(tmp_path, text: str) -> str:
    sf = tmp_path / "src.md"
    sf.write_text(text, encoding="utf-8")
    return str(sf)


# ---------------------------------------------------------------- 1. 路由回退/源不可读不得残留旧包

class TestFallbackStripsStaleBlock:
    def test_source_shrunk_to_short_strips_block(self, tmp_path):
        """源被换成短源 → 路由回 single，旧包块必须剥掉而不是留在条目里。"""
        src = _long_source()
        entry, base = _assembled_entry(tmp_path, src)
        assert DD.MATERIAL_PACK_BEGIN in entry["prompt"]
        _write_source(tmp_path, "短源。\n" * 50)       # 换源：变短 → 不再适用

        with pytest.raises(DD.PrepareIneligible) as ei:
            DD.prepare_entry(entry, store_dir=str(tmp_path))
        assert ei.value.reason == "short_source"
        assert ei.value.stripped is True               # 条目确实变了，调用方须写回
        assert DD.MATERIAL_PACK_BEGIN not in entry["prompt"]
        assert entry["prompt"] == base.rstrip("\n")
        gc = entry["generation_context"]
        assert gc["prompt_sha256"] == text_hash(entry["prompt"])
        for k in ("distill_pack_key", "distill_pack_source_sha256", "distill_prompt_sha256"):
            assert k not in gc

    def test_source_becomes_numeric_dense_strips_block(self, tmp_path):
        """换成数字密集源（S8-D2 型）→ 同样回单轮，旧包不得残留。"""
        src = _long_source()
        entry, _base = _assembled_entry(tmp_path, src)
        _write_source(tmp_path, _numeric_source())
        with pytest.raises(DD.PrepareIneligible) as ei:
            DD.prepare_entry(entry, store_dir=str(tmp_path))
        assert ei.value.reason == "numeric_dense"
        assert ei.value.stripped is True
        assert DD.MATERIAL_PACK_BEGIN not in entry["prompt"]

    def test_source_becomes_numeric_heavy_strips_block(self, tmp_path):
        """换成「密度低但数字串量大」的源（S9-B 型，绝对量闸）→ 同样回单轮。"""
        src = _long_source()
        entry, _base = _assembled_entry(tmp_path, src)
        _write_source(tmp_path, _numeric_heavy_source())
        with pytest.raises(DD.PrepareIneligible) as ei:
            DD.prepare_entry(entry, store_dir=str(tmp_path))
        assert ei.value.reason == "numeric_heavy"
        assert ei.value.stripped is True
        assert DD.MATERIAL_PACK_BEGIN not in entry["prompt"]

    def test_source_unavailable_strips_block_and_states_reason(self, tmp_path):
        """源不可读：明确回退状态，且不继续消费旧包（含 #raw_file 被删的场景）。"""
        src = _long_source()
        entry, _base = _assembled_entry(tmp_path, src)
        os.remove(entry["raw_file"])                   # 源文件没了
        log_path = str(tmp_path / "log.jsonl")
        with pytest.raises(DD.PrepareIneligible) as ei:
            DD.prepare_entry(entry, store_dir=str(tmp_path), log_path=log_path)
        assert ei.value.reason == "source_unavailable"
        assert ei.value.stripped is True
        assert DD.MATERIAL_PACK_BEGIN not in entry["prompt"]
        rec = json.loads(open(log_path, encoding="utf-8").readlines()[-1])
        assert rec["status"] in ("ineligible", "source_unavailable")
        assert rec["fallback_reason"] == "source_unavailable"
        assert rec["blocks_removed"] >= 1

    def test_no_block_fallback_is_not_stripped(self, tmp_path):
        """没有旧包的条目回单轮：状态就是不适用，stripped=False，不触发写回。"""
        entry = _entry("eval://s9/noblock", "短", "BASE\n")
        with pytest.raises(DD.PrepareIneligible) as ei:
            DD.prepare_entry(entry, store_dir=str(tmp_path), source_text="短源。")
        assert ei.value.stripped is False
        assert entry["prompt"] == "BASE\n"

    def test_dry_run_reports_without_touching_entry(self, tmp_path):
        """dry-run 只报告：旧包留着不代表还能用，条目本体不得被改。"""
        src = _long_source()
        entry, _base = _assembled_entry(tmp_path, src)
        before = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        _write_source(tmp_path, "短源。\n" * 50)
        with pytest.raises(DD.PrepareIneligible) as ei:
            DD.prepare_entry(entry, store_dir=str(tmp_path), dry_run=True)
        assert ei.value.stripped is True
        assert json.dumps(entry, ensure_ascii=False, sort_keys=True) == before

    def test_prepare_queue_entry_persists_strip(self, tmp_path):
        """回退剥包必须落到队列文件里——否则下一轮还会消费失效包。"""
        src = _long_source()
        entry, _base = _assembled_entry(tmp_path, src)
        qp = tmp_path / "queue.json"
        qp.write_text(json.dumps([entry], ensure_ascii=False, indent=2), encoding="utf-8")
        _write_source(tmp_path, "短源。\n" * 50)
        with pytest.raises(DD.PrepareIneligible):
            DD.prepare_queue_entry(str(qp), "eval://s9/T1", store_dir=str(tmp_path))
        saved = json.load(open(qp, encoding="utf-8"))[0]
        assert DD.MATERIAL_PACK_BEGIN not in saved["prompt"]
        assert "distill_pack_key" not in saved["generation_context"]

    def test_prepare_queue_persists_strip_batch(self, tmp_path):
        """批量路径同样要写回（fallback 不等于「什么都没发生」）。"""
        src = _long_source()
        entry, _base = _assembled_entry(tmp_path, src, url="eval://s9/T2")
        qp = tmp_path / "queue.json"
        qp.write_text(json.dumps([entry], ensure_ascii=False, indent=2), encoding="utf-8")
        _write_source(tmp_path, "短源。\n" * 50)
        res = DD.prepare_queue(str(qp), store_dir=str(tmp_path))
        assert res["fallback"]
        saved = json.load(open(qp, encoding="utf-8"))[0]
        assert DD.MATERIAL_PACK_BEGIN not in saved["prompt"]

    def test_plan_queue_reports_pending_strip(self, tmp_path):
        """只读 plan 阶段就要能看见「这条已装配但已不适用，会被剥掉」。"""
        src = _long_source()
        entry, _base = _assembled_entry(tmp_path, src, url="eval://s9/T3")
        qp = tmp_path / "queue.json"
        _write_source(tmp_path, "短源。\n" * 50)
        qp.write_text(json.dumps([entry], ensure_ascii=False, indent=2), encoding="utf-8")
        plan = DD.plan_queue(str(qp), store_dir=str(tmp_path))
        row = plan["rows"][0]
        assert row["route"] == "single"
        assert row["pending_strip"] is True


# ---------------------------------------------------------------- 2. 重复装配不得叠包

class TestNoStackedBlocks:
    def test_historic_double_block_collapses_to_one(self, tmp_path):
        """历史双包条目（_tmp 试验脚本产物）再装配后必须只剩一个包块。"""
        src = _long_source()
        pack = _mk_pack(src, url="eval://s9/double", title="双包")
        scrambled = DD.assemble_write_prompt(
            DD.assemble_write_prompt("BASE PROMPT\n", pack), pack)  # 模拟重复装配
        assert scrambled.count(DD.MATERIAL_PACK_BEGIN) == 2
        DP.save_pack(pack, str(tmp_path))
        entry = _entry("eval://s9/double", "双包", scrambled,
                       {"prompt_sha256": text_hash(scrambled)})
        rep = DD.prepare_entry(entry, store_dir=str(tmp_path), source_text=src)
        assert entry["prompt"].count(DD.MATERIAL_PACK_BEGIN) == 1
        assert rep["status"] in ("prepared", "repaired_duplicate_blocks")
        assert entry["prompt"].startswith("BASE PROMPT")

    def test_repeated_prepare_keeps_single_block(self, tmp_path):
        """重复 prepare 幂等：包块数恒为 1，正文前缀逐字节不变。"""
        src = _long_source()
        DP.save_pack(_mk_pack(src, url="eval://s9/repeat", title="重复"), str(tmp_path))
        entry = _entry("eval://s9/repeat", "重复", "BASE PROMPT\n")
        for _ in range(3):
            DD.prepare_entry(entry, store_dir=str(tmp_path), source_text=src)
        assert entry["prompt"].count(DD.MATERIAL_PACK_BEGIN) == 1
        assert entry["prompt"].startswith("BASE PROMPT\n")

    def test_strip_pack_blocks_removes_every_block(self):
        base = "BASE\n"
        once = DD.assemble_write_prompt(base, _mk_pack("源"))
        twice = DD.assemble_write_prompt(once, _mk_pack("源"))
        thrice = DD.assemble_write_prompt(twice, _mk_pack("源"))
        clean, n = DD.strip_pack_blocks(thrice)
        assert clean == "BASE"
        assert n == 3
        clean0, n0 = DD.strip_pack_blocks(base)
        assert (clean0, n0) == ("BASE", 0)


# ---------------------------------------------------------------- emit 派单（url_key 缺陷）

class TestEmitTasks:
    def test_emit_writes_tasks_and_manifest_key(self, tmp_path):
        """派单必须能真跑通并给全 url_key——历史上 `key` 未定义会让整步 NameError。"""
        src = _long_source()
        sf = _write_source(tmp_path, src)
        entry = _entry("eval://s9/emit", "派单测试", "BASE\n") | {"raw_file": sf}
        qp = tmp_path / "queue.json"
        qp.write_text(json.dumps([entry], ensure_ascii=False, indent=2), encoding="utf-8")
        out = str(tmp_path / "tasks")
        man = DD.emit_distill_tasks(str(qp), out)
        assert len(man["tasks"]) == 1
        t = man["tasks"][0]
        assert t["url_key"]
        assert t["task_file"] == f"{t['url_key']}_distill.txt"
        assert os.path.exists(os.path.join(out, t["task_file"]))
        # 任务文件是自包含的：写明标题并带整篇源码标记
        body = open(os.path.join(out, t["task_file"]), encoding="utf-8").read()
        assert "派单测试" in body and DT.DISTILL_MARKER in body

    def test_emit_skips_entry_with_valid_pack(self, tmp_path):
        src = _long_source()
        sf = _write_source(tmp_path, src)
        DP.save_pack(_mk_pack(src, url="eval://s9/emit2", title="复用"), str(tmp_path))
        entry = _entry("eval://s9/emit2", "复用", "BASE\n") | {"raw_file": sf}
        qp = tmp_path / "queue.json"
        qp.write_text(json.dumps([entry], ensure_ascii=False, indent=2), encoding="utf-8")
        man = DD.emit_distill_tasks(str(qp), str(tmp_path / "tasks2"),
                                    store_dir=str(tmp_path))
        assert man["tasks"] == []
        assert man["skipped"][0]["reason"] == "pack_reused"

    def test_emit_multi_chunk_keys_share_one_url_key(self, tmp_path):
        """>40k 源分块：每块一份任务文件，但 url_key 必须指回同一篇（回收要能归组）。"""
        src = _long_source(DD.CHUNK_MAX_CHARS * 2 + 6000)
        sf = _write_source(tmp_path, src)
        entry = _entry("eval://s9/chunky", "长源", "BASE\n") | {"raw_file": sf}
        qp = tmp_path / "queue.json"
        qp.write_text(json.dumps([entry], ensure_ascii=False, indent=2), encoding="utf-8")
        man = DD.emit_distill_tasks(str(qp), str(tmp_path / "tasks3"))
        assert len(man["tasks"]) >= 2
        assert len({t["url_key"] for t in man["tasks"]}) == 1
        for t in man["tasks"]:
            assert t["task_file"].startswith(t["url_key"] + "_c")
            assert t["chunk"]["index"] >= 1


# ---------------------------------------------------------------- 3. quote 规则与示例一致

class TestQuoteRuleConsistency:
    def test_rules_forbid_annotation_on_quote(self):
        """契约正文必须写明「带模型注解的 content 不得标 quote」，防止再退化。"""
        rules = DT.rules_text()
        assert "quote" in rules and "注解" in rules
        low = rules.replace(" ", "")
        assert ("不得标`quote`" in low or "不得标quote" in low
                or "只能 paraphrase" in low or "只能`paraphrase`" in low)

    def test_schema_example_is_self_consistent(self):
        """示例自身过机械自检：quote 条目不得含模型注解、id 唯一、关系不悬空。"""
        assert DT.example_issues() == [], DT.example_issues()

    def test_real_output_quote_still_machine_checked(self):
        """一致性要求只收紧示例，不放宽真实回包：quote 未逐字命中仍须报错。"""
        src = "行一内容。\n行二内容。\n"
        parsed = {
            "mainline": {"question": "q", "progression": ["p"], "conclusions": ["c"]},
            "items": [{"id": "i1", "type": "number", "stance": "quote",
                       # 带注解的错误 quote：既不是逐字原话，也不应被放行
                       "content": "利率上涨了（源此处疑似读数断字）", "source_lines": [1, 1]}],
            "relations": [],
        }
        pack = DP.build_pack(title="t", url="eval://s9/quote", source_text=src,
                             distill_prompt_sha256=DT.prompt_hash("t", src), parsed=parsed)
        rep = DP.validate_pack(pack, src)
        # v5 已不接收模型抄写的正文；错写不能进入素材。
        assert rep["ok"]
        assert pack["items"][0]["content"] == "行一内容。"
        pack["items"][0]["content"] = "伪造原话"
        assert not DP.validate_pack(pack, src)["ok"]

    def test_paraphrase_with_annotation_passes_quote_check(self):
        """同一句改成 paraphrase 就合法——规则要区分的是 stance，不是删掉限制。"""
        src = "利率从4.67上涨到了4.7 一 三\n"
        parsed = {
            "mainline": {"question": "q", "progression": ["p"], "conclusions": ["c"]},
            "items": [{"id": "i1", "type": "number", "stance": "paraphrase",
                       "content": "利率从4.67上涨到了4.7 一 三（读数存疑，不得拼写成 4.713%）",
                       "source_lines": [1, 1]}],
            "relations": [],
        }
        pack = DP.build_pack(title="t", url="eval://s9/para", source_text=src,
                             distill_prompt_sha256=DT.prompt_hash("t", src), parsed=parsed)
        rep = DP.validate_pack(pack, src)
        assert rep["ok"], rep["issues"]
