# -*- coding: utf-8 -*-
"""材料包输入规模收敛回归测试（PLAN-20261026-low-cost-quality §8.5 第 2 条）。

为什么要有这个文件：旧做法把整份 pack 以 `indent=1` 的 JSON 塞进写作 prompt，
A 篇包块 95,700 字符、占整份写作 prompt 的 90%（主体只有 10.5k）——包一复杂就不可用。
第 2 条要收敛它，但**不许靠删内容换字符数**（条件/反例/数字归属/关系/独有信息都得在）。

钉死三件事：
1. **压缩必须是语法层面的**：`json` → `compact`（去缩进）→ `lines`（去重复键名），
   三种形态都要能通过 `lines_roundtrip_issues` 的逐条还原比对；
2. **内容一个都不能少**：同内容不同行号/不同类型的条目不得被去重合并，
   合并条目的关系要重映射，去重数写进 notes（不许静默删）；
3. **换形态不能打断版本绑定**：行式包块仍能读出 `source_sha256`/`distill_prompt_sha256`，
   `block_is_current` 的「源变/契约升级」判定一律照旧。

字符数是**唯一敢报的硬指标**；token 与耗时取不到一律写 `unknown`（不拿估算充数）。
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

REAL_PACKS = os.path.join(ROOT, "notes", "_meta", "eval", "20261027-s9-fix", "packs")


def _mk_items(n: int = 60) -> list:
    items = []
    for i in range(1, n + 1):
        items.append({
            "id": f"i{i}",
            "type": ["viewpoint", "case", "step", "number", "condition",
                     "counterexample"][i % 6],
            "stance": "quote" if i % 7 == 0 else "paraphrase",
            "content": (f"第{i}条：这是正文里的一段话，含口径 20{i}% 与条件说明；"
                        "换行用 \\n 表示，竖线 | 和双竖线 ‖ 也可能出现"),
            "source_lines": [i, i + 1],
        })
    return items


def _mk_pack(n: int = 60) -> dict:
    src_lines = ["源第 %d 行内容。" % i for i in range(1, 400)]
    src = "\n".join(src_lines)
    return DP.build_pack(
        title="规模测试", url="eval://s9/size", source_text=src,
        distill_prompt_sha256=DT.prompt_hash("规模测试", src),
        parsed={"mainline": {"question": "要回答什么", "progression": ["先", "再"],
                             "conclusions": ["结论带强度"]},
                "items": _mk_items(n),
                "relations": [{"from": f"i{k}", "to": f"i{k + 1}", "kind": "supports",
                               "note": f"第{k}条支持第{k + 1}条"} for k in range(1, n)]})


class TestSerializationKeepsEverything:
    def test_size_ordering_json_gt_compact_gt_lines(self):
        pack = _mk_pack(80)
        sizes = {s: len(DD.render_pack_block(pack, style=s))
                 for s in ("json", "compact", "lines")}
        assert sizes["json"] > sizes["compact"] > sizes["lines"]
        # 行式必须真的减下来（否则所谓压缩没意义）——实测 ./docs 记 −52%
        saved = DD.measure_assembly("BASE\n", pack, style="lines")["savings_pct"]
        assert saved >= 40, saved

    def test_every_style_roundtrips_lossless(self):
        pack = _mk_pack(40)
        assert DP.lines_roundtrip_issues(pack) == [], DP.lines_roundtrip_issues(pack)

    def test_content_with_delimiter_and_newline_survives(self):
        """正文里出现分隔符候选字符本身也不能把行切坏（分隔符动态挑 + 转义兜底）。"""
        pack = _mk_pack(4)
        pack["items"][0]["content"] = "含 ‖ 双竖线 与 ❙ 竖线 与 \\n 换行 与 \\\\ 反斜杠"
        pack["items"][1]["content"] = "另一种：␟ 控制符 + | 普通竖线"
        assert DP.lines_roundtrip_issues(pack) == []

    def test_conditions_numbers_and_relations_are_present(self):
        """压缩后条件/反例/数字归属/关系必须还在材料包里一个不少。"""
        pack = _mk_pack(30)
        text = DP.serialize_pack(pack, style="lines")
        for it in pack["items"]:
            assert DP.parse_pack_lines(text)["items"][pack["items"].index(it)]["content"] == it["content"]
            assert it["id"] in text
        for kind in ("condition", "counterexample", "number"):
            if any(i["type"] == kind for i in pack["items"]):
                assert kind in text
        for r in pack["relations"]:
            assert r["kind"] in text and r["note"] in text


class TestDedupeIsNotSilentDeletion:
    def test_identical_items_merged_and_relations_remapped(self):
        pack = _mk_pack(5)
        pack["items"].append(dict(pack["items"][0]))            # 完全同样的重复条目
        pack["relations"].append({"from": "i1", "to": "i1", "kind": "supports", "note": "重复"})
        deduped, removed = DP.dedupe_pack_items(pack)
        assert removed == 1
        assert len(deduped["items"]) == len(pack["items"]) - 1
        assert "条目去重 1 条" in deduped["notes"]               # 不许静默删

    def test_same_content_different_lines_is_not_merged(self):
        """同内容不同位置是两处取材点，合并会把「哪里讲的」信息丢掉。"""
        pack = _mk_pack(3)
        twin = dict(pack["items"][0])
        twin["source_lines"] = [200, 201]
        pack["items"].append(twin)
        deduped, removed = DP.dedupe_pack_items(pack)
        assert removed == 0
        assert len(deduped["items"]) == len(pack["items"])

    def test_real_packs_have_nothing_to_dedupe(self):
        """实测结论：现有两颗真实 pack 一个重复都没有（先去重这一步收益为 0）。"""
        if not os.path.isdir(REAL_PACKS):
            pytest.skip("本机没有 s9-fix 隔离 pack，跳过真数据复核")
        for f in os.listdir(REAL_PACKS):
            pack = json.load(open(os.path.join(REAL_PACKS, f), encoding="utf-8"))
            _, removed = DP.dedupe_pack_items(pack)
            assert removed == 0, f


class TestRealPacksShrinkWithoutLoss:
    @pytest.mark.skipif(not os.path.isdir(REAL_PACKS),
                        reason="本机没有 s9-fix 隔离 pack")
    def test_real_packs_lines_style_is_lossless_and_halved(self):
        for f in os.listdir(REAL_PACKS):
            pack = json.load(open(os.path.join(REAL_PACKS, f), encoding="utf-8"))
            assert DP.lines_roundtrip_issues(pack) == [], f
            saved = DD.measure_assembly("BASE\n", pack, style="lines")["savings_pct"]
            assert saved >= 45, (f, saved)


class TestVersionBindingSurvivesStyle:
    def _pack_for(self, src: str, phash: str = "") -> dict:
        return DP.build_pack(title="形态测试", url="eval://s9/style", source_text=src,
                             distill_prompt_sha256=phash
                             or DT.prompt_hash("形态测试", src),
                             parsed={"mainline": {"question": "q", "progression": ["p"],
                                                 "conclusions": ["c"]},
                                     "items": _mk_items(3), "relations": []})

    def test_lines_block_still_readable_for_version_binding(self):
        src = "源文本第一行。\n源文本第二行。\n"
        pack = self._pack_for(src)
        block = DD.render_pack_block(pack, style="lines")
        assert DD.block_source_sha(block) == pack["source_sha256"]
        assert DD.block_is_current(block, src, title="形态测试") == (True, "ok")

    def test_lines_block_detects_source_and_contract_change(self):
        src = "源文本第一行。\n源文本第二行。\n"
        pack = self._pack_for(src)
        block = DD.render_pack_block(pack, style="lines")
        assert DD.block_is_current(block, src + "改了。\n", title="形态测试")[0] is False
        assert DD.block_is_current(block, src, title="换标题")[1] == "contract_upgraded"
        assert DP.style_of_pack_block(block) == "lines"

    def test_json_is_the_default_and_serialization_stays_byte_compatible(self):
        """生产默认必须是 `json`；pack 序列化与包说明逐字节不变。

        N3 起块开头多了一段**带版本标记**的交接/统稿契约（回源行号、跨块对账）——
        那是写作消费契约的升级，不是序列化形态变化：`json.dumps(pack)` 与
        `PACK_BLOCK_HEADER` 仍逐字节保持，S8/S9 的字节数对照继续有效。
        """
        from prompts import synthesis as SY
        assert DD.resolved_pack_style() == "json"
        pack = _mk_pack(3)
        block = DD.render_pack_block(pack)
        tail = (f"{DD.PACK_BLOCK_HEADER}\n```json\n"
                f"{json.dumps(pack, ensure_ascii=False, indent=1)}\n```\n"
                f"{DD.MATERIAL_PACK_END}")
        assert block.endswith(tail)
        assert block.startswith(f"{DD.MATERIAL_PACK_BEGIN}\n{SY.HANDOFF_MARKER}")
        assert DP.style_of_pack_block(block) == "json"

    def test_style_switch_and_unknown_value_fall_back(self, monkeypatch):
        monkeypatch.setenv("NOTE_DISTILL_PACK_STYLE", "lines")
        assert DD.resolved_pack_style() == "lines"
        monkeypatch.setenv("NOTE_DISTILL_PACK_STYLE", "不是任何一种")
        assert DD.resolved_pack_style() == "json"


class TestCostTelemetryIsHonest:
    def test_measure_reports_chars_and_unknown_token(self):
        pack = _mk_pack(20)
        cost = DD.measure_assembly("BASE PROMPT\n", pack, style="lines")
        assert cost["token"] == "unknown" and cost["latency"] == "unknown"
        assert cost["total_chars"] == cost["base_chars"] + cost["pack_block_chars"]
        assert cost["style"] == "lines"
        assert cost["duplicate_items"] >= 0

    def test_prepare_records_cost_and_style(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOTE_DISTILL_PACK_STYLE", "lines")
        line = "这一段口语论述用来凑足字数本身不含任何读数也不包含任何数字口径。\n"
        src, n = [], 0
        while n < 12000:
            src.append(line)
            n += len("".join(line.split()))
        src = "".join(src)
        pack = self._make_valid_pack(src)
        DP.save_pack(pack, str(tmp_path))
        entry = {"url": "eval://s9/cost", "title": "成本核算", "prompt": "BASE\n",
                 "generation_context": {}}
        log_path = str(tmp_path / "log.jsonl")
        DD.prepare_entry(entry, store_dir=str(tmp_path), source_text=src,
                         log_path=log_path, pack_style="lines")
        rec = json.loads(open(log_path, encoding="utf-8").readlines()[-1])
        assert rec["cost"]["style"] == "lines"
        assert rec["cost"]["savings_pct"] > 40
        assert entry["generation_context"]["distill_pack_style"] == "lines"
        assert entry["prompt"].count(DD.MATERIAL_PACK_BEGIN) == 1

    def _make_valid_pack(self, src: str) -> dict:
        """多条目（贴近真实量级）——单条目样本下固定开销占比过高，
        压缩比没有参考价值，还会把结论误导成「压缩无效」。"""
        parsed = {"mainline": {"question": "q", "progression": ["p"], "conclusions": ["c"]},
                  "items": [{"id": "i1", "type": "step", "stance": "paraphrase",
                             "content": "先做背调再开发信", "source_lines": [1, 2]}]
                  + [{"id": f"j{k}", "type": ["number", "condition", "case"][k % 3],
                      "stance": "paraphrase",
                      "content": f"第{k}处：口径 {k * 3}% 的条件与反例都保留在材料包里",
                      "source_lines": [k + 2, k + 2]} for k in range(1, 25)],
                  "relations": [{"from": "i1", "to": "j1", "kind": "supports",
                                 "note": "步骤依赖前置判断"}]}
        return DP.build_pack(title="成本核算", url="eval://s9/cost", source_text=src,
                             distill_prompt_sha256=DT.prompt_hash("成本核算", src),
                             parsed=parsed)
