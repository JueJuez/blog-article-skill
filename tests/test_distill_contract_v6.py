# -*- coding: utf-8 -*-
"""提炼契约 v6（意图树）回归测试——PLAN-20261030-intent-tree-summary §2.1/§4 F1，RED 先行。

契约要点（v5 → v6）：
1. `hierarchy`：树形随源形态自适应——有显式章节用 章节→小节→节点；无显式章节的
   口播/直播按话题分段。每级一句 `intent`（作者用意推断）；根 intent 可空；
   话题节点 intent 必填（填不出用意的不配成节点）。
2. `items` 挂 `node_id`，新增 `tier` 三档：trunk（主干必落点）/ support（按主线取舍）/
   drop（默认丢）。
3. 话题回跳（A→B→A′）：同一话题两段归同一节点，非连续区间照实分列（复用
   source_spans 非连续语义）。
4. `no_substance` 出口：唯一降级出口——提炼者显式判纯寒暄/无实质内容；「无结构」
   不降级，口播照样按话题分段。
5. 机械校验：span 不越界、node 引用存在、trunk 不为空。
6. 版本门：旧 v5 pack 被 `contract_version_mismatch` 拒消费，不做兼容。
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

# 有显式章节的短源（章节树形态）
SOURCE = """> 原始文章内容（自动暂存）

---

# 一、为什么做代运营

作者说：「代运营的本质是把别人的坑变成自己的经验。」

2019 年他接了第一个客户，报价 8000 元/月。
条件是客户必须自备货源，否则不接。

# 二、踩过的坑

第一个月他没有签竞业条款，客户直接绕过他找上游，损失约 3 万。
"""


def _hierarchy(**kw) -> dict:
    """最小合法树：两个话题节点（回跳形态：n1 非连续两段），根 intent 空。"""
    base = {
        "intent": "",
        "children": [
            {"id": "n1", "label": "入行动机", "intent": "先讲清为什么做代运营，为踩坑铺垫",
             "source_spans": [[5, 6], [9, 10]], "children": []},
            {"id": "n2", "label": "踩坑", "intent": "用竞业条款反例说明合规的代价",
             "source_spans": [[14, 14]], "children": []},
        ],
    }
    base.update(kw)
    return base


def _parsed(**kw) -> dict:
    base = {
        "hierarchy": _hierarchy(),
        "items": [
            {"id": "i1", "type": "viewpoint", "tier": "trunk", "node_id": "n1",
             "label": "代运营的本质", "source_spans": [[5, 5]], "notes": ""},
            {"id": "i2", "type": "number", "tier": "support", "node_id": "n1",
             "label": "首单报价", "source_spans": [[9, 9]], "notes": ""},
            {"id": "i3", "type": "case", "tier": "support", "node_id": "n2",
             "label": "竞业条款损失", "source_spans": [[14, 14]], "notes": ""},
        ],
        "relations": [],
        "deferred": [],
    }
    base.update(kw)
    return base


def _pack(parsed=None, source=SOURCE, **kw):
    return DP.build_pack(
        title="代运营复盘", url="https://example.com/v6", source_text=source,
        distill_prompt_sha256=DT.prompt_hash("代运营复盘", source),
        parsed=_parsed() if parsed is None else parsed, **kw)


# ---------------------------------------------------------------- 版本与文案

class TestContractVersion:
    def test_contract_version_is_6(self):
        assert DT.CONTRACT_VERSION == 6

    def test_marker_is_v6(self):
        assert "v6" in DT.DISTILL_MARKER

    def test_item_tiers_enum(self):
        assert DT.ITEM_TIERS == ("trunk", "support", "drop")

    def test_rules_mention_tier_semantics(self):
        text = DT.rules_text()
        for kw in ("trunk", "support", "drop", "node_id", "intent"):
            assert kw in text, f"契约正文缺少 {kw} 定义"

    def test_rules_mention_topic_segmentation_and_jumpback(self):
        text = DT.rules_text()
        assert "话题" in text
        assert "回跳" in text or "非连续" in text

    def test_rules_mention_no_substance_exit(self):
        assert "no_substance" in DT.rules_text()

    def test_rules_intent_fidelity_boundary(self):
        # intent 是笔记者推断：不进稿、不得写成作者明确主张
        text = DT.rules_text()
        assert "不进稿" in text

    def test_prompt_renders_contract(self):
        p = DT.render_distill_prompt("t", SOURCE)
        assert DT.DISTILL_MARKER in p
        assert "no_substance" in p


class TestExampleSelfCheck:
    def test_example_is_self_consistent(self):
        # 示例自带 hierarchy/tier 后不得教反向做法
        assert DT.example_issues() == []

    def test_example_has_trunk_item(self):
        ex = DT.example_schema()
        tiers = {it.get("tier") for it in ex.get("items") or []}
        assert "trunk" in tiers
        assert all(it.get("node_id") for it in ex.get("items") or [])


# ---------------------------------------------------------------- 树形与回跳

class TestHierarchyBuildAndValidate:
    def test_v6_pack_roundtrip_ok(self):
        pack = _pack()
        rep = DP.validate_pack(pack, SOURCE)
        assert rep["ok"], rep["issues"]

    def test_root_intent_may_be_empty(self):
        pack = _pack()
        assert pack["hierarchy"]["intent"] == ""
        assert DP.validate_pack(pack, SOURCE)["ok"]

    def test_topic_jumpback_non_contiguous_spans_preserved(self):
        # n1 的两段（[[5,6],[9,10]]）非连续：回跳合并后照实分列，不伪装连续
        pack = _pack()
        n1 = pack["hierarchy"]["children"][0]
        assert n1["source_spans"] == [[5, 6], [9, 10]]

    def test_items_carry_node_id_and_tier(self):
        pack = _pack()
        by_id = {it["id"]: it for it in pack["items"]}
        assert by_id["i1"]["node_id"] == "n1"
        assert by_id["i1"]["tier"] == "trunk"
        assert by_id["i3"]["tier"] == "support"

    def test_content_still_verbatim_from_source(self):
        pack = _pack()
        it = next(x for x in pack["items"] if x["id"] == "i1")
        assert "为什么做代运营" in it["content"]

    def test_missing_node_intent_reported(self):
        parsed = _parsed()
        parsed["hierarchy"]["children"][0]["intent"] = ""
        pack = _pack(parsed)
        rep = DP.validate_pack(pack, SOURCE)
        assert not rep["ok"]
        assert any("n1" in m and "intent" in m for m in rep["issues"])

    def test_missing_hierarchy_reported(self):
        pack = _pack()
        del pack["hierarchy"]
        rep = DP.validate_pack(pack, SOURCE)
        assert not rep["ok"]
        assert any("hierarchy" in m for m in rep["issues"])

    def test_duplicate_node_id_reported(self):
        parsed = _parsed()
        parsed["hierarchy"]["children"][1]["id"] = "n1"
        rep = DP.validate_pack(_pack(parsed), SOURCE)
        assert not rep["ok"]
        assert any("n1" in m for m in rep["issues"])

    def test_node_span_out_of_range_reported(self):
        # 越界 span 在规范化时被剥为空 → 以「缺 source_spans」形态被拦（同样不过）
        parsed = _parsed()
        parsed["hierarchy"]["children"][0]["source_spans"] = [[100, 200]]
        rep = DP.validate_pack(_pack(parsed), SOURCE)
        assert not rep["ok"]
        assert any("source_spans" in m for m in rep["issues"])


# ---------------------------------------------------------------- tier 校验

class TestTierValidation:
    def test_dangling_node_ref_reported(self):
        parsed = _parsed()
        parsed["items"][0]["node_id"] = "nx"
        rep = DP.validate_pack(_pack(parsed), SOURCE)
        assert not rep["ok"]
        assert any("nx" in m for m in rep["issues"])

    def test_invalid_tier_reported(self):
        parsed = _parsed()
        parsed["items"][0]["tier"] = "optional"
        rep = DP.validate_pack(_pack(parsed), SOURCE)
        assert not rep["ok"]
        assert any("tier" in m for m in rep["issues"])

    def test_empty_trunk_reported(self):
        # 全 support/drop、无 trunk → 主干为空，机械拦下
        parsed = _parsed()
        for it in parsed["items"]:
            it["tier"] = "support"
        rep = DP.validate_pack(_pack(parsed), SOURCE)
        assert not rep["ok"]
        assert any("trunk" in m for m in rep["issues"])

    def test_drop_item_allowed_in_pack(self):
        parsed = _parsed()
        parsed["items"][2]["tier"] = "drop"
        assert DP.validate_pack(_pack(parsed), SOURCE)["ok"]


# ---------------------------------------------------------------- no_substance 出口

class TestNoSubstanceExit:
    def test_no_substance_pack_builds_and_validates(self):
        pack = _pack(parsed={"no_substance": True, "reason": "纯寒暄与流程性问候，无实质内容"})
        assert pack.get("no_substance") is True
        assert "寒暄" in pack.get("no_substance_reason", "")
        assert pack["items"] == []
        rep = DP.validate_pack(pack, SOURCE)
        assert rep["ok"], rep["issues"]

    def test_no_substance_requires_reason(self):
        pack = _pack(parsed={"no_substance": True, "reason": ""})
        rep = DP.validate_pack(pack, SOURCE)
        assert not rep["ok"]

    def test_no_substance_pack_consumable_via_version_gate(self, tmp_path):
        pack = _pack(parsed={"no_substance": True, "reason": "无实质内容"})
        DP.save_pack(pack, str(tmp_path))
        got, why = DD.load_valid_pack_for_source("https://example.com/v6", SOURCE,
                                                 title="代运营复盘",
                                                 store_dir=str(tmp_path))
        assert got is not None and why == "ok"

    def test_normal_path_unaffected(self):
        assert DP.validate_pack(_pack(), SOURCE)["ok"]


# ---------------------------------------------------------------- 版本门

class TestVersionGate:
    def test_v5_pack_rejected_by_contract_gate(self, tmp_path):
        pack = _pack()
        pack["contract_version"] = 5
        pack["schema_version"] = 3
        pack["mainline"] = {"question": "q", "progression": [], "conclusions": []}
        DP.save_pack(pack, str(tmp_path))
        got, why = DD.load_valid_pack_for_source("https://example.com/v6", SOURCE,
                                                 title="代运营复盘",
                                                 store_dir=str(tmp_path))
        assert got is None
        assert why == "contract_version_mismatch"

    def test_validate_reports_v6_missing_fields(self):
        # v6 pack 缺 hierarchy/树字段时 validate 报告（版本门之外的第二道）
        pack = _pack()
        pack.pop("hierarchy")
        rep = DP.validate_pack(pack, SOURCE)
        assert not rep["ok"]


# ---------------------------------------------------------------- 行式序列化

class TestLinesSerialization:
    def test_lines_roundtrip_preserves_tree_and_tiers(self):
        pack = _pack()
        issues = DP.lines_roundtrip_issues(pack)
        assert issues == []

    def test_lines_output_has_node_lines(self):
        text = DP.serialize_pack_lines(_pack())
        assert "\n@h " in text or text.startswith("@h ")

    def test_lines_roundtrip_no_substance(self):
        pack = _pack(parsed={"no_substance": True, "reason": "无实质内容"})
        assert DP.lines_roundtrip_issues(pack) == []


# ---------------------------------------------------------------- 旧版回归锚点

class TestLegacyMainlineRemovedInV6:
    def test_v6_pack_has_no_mainline_key(self):
        # mainline 并入树根（根 intent）：v6 pack 不再有平面 mainline 字段
        assert "mainline" not in _pack()

    def test_mainline_check_only_for_legacy_shape(self):
        # 带 mainline 的旧形态 pack（被版本门拦）在 validate 里不再按 v6 树判，
        # 也不因无树而误报「主线地图缺失」之外的双份问题——版本门是唯一裁决点
        pack = _pack()
        pack["contract_version"] = 5
        pack["mainline"] = {"question": "", "progression": [], "conclusions": []}
        issues = DP.validate_pack(pack, SOURCE)["issues"]
        assert any("契约版本不一致" in m for m in issues)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
