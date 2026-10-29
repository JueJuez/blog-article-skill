# -*- coding: utf-8 -*-
"""F2 写作/自查/补稿契约 v6 用例——PLAN-20261030-intent-tree-summary §2.2/§2.3/§4 F2，RED 先行。

要点：
1. 自查器 tier 过滤：**仅 trunk 条目进拦截**（label 3-gram 阈值 0.2 冻结不变）；
   support/drop 进 info 报告（只观察不拦）；缺 tier 按主干保守拦截（兼容旧包）。
2. 补稿协议三出口：①改写已含；②核实为可舍弃（记录一句理由）——v6 新增；
   ③缺才补（纯插入，`--verify-insert-only` 核验不变）。三出口写进
   `scripts/check_note_coverage.py` 头注与补稿材料协议。
3. 写作契约：模板选材权段落（trunk 必落点 / support 按主线取舍 / drop 默认丢 /
   intent 不进稿 / 不设字数硬闸）进材料包块。
"""
import io
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from shared import note_coverage as NC


def _load_check_script():
    """按路径加载 scripts/check_note_coverage.py（scripts 无 __init__，不能直接 import）。"""
    path = os.path.join(ROOT, "scripts", "check_note_coverage.py")
    spec = importlib.util.spec_from_file_location("check_note_coverage_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pack_v6():
    return {"hierarchy": {"intent": "", "children": [
        {"id": "n1", "label": "主干话题", "intent": "用意", "source_spans": [[2, 3]],
         "children": []},
        {"id": "n2", "label": "支撑话题", "intent": "用意", "source_spans": [[5, 6]],
         "children": []},
        {"id": "n3", "label": "寒暄过程", "intent": "用意", "source_spans": [[8, 8]],
         "children": []},
    ]}, "items": [
        {"id": "i1", "tier": "trunk", "node_id": "n1",
         "label": "专业素养：知识体系与市场洞察", "source_spans": [[2, 3]]},
        {"id": "i2", "tier": "support", "node_id": "n2",
         "label": "供应链掌控：库存周转与账期", "source_spans": [[5, 6]]},
        {"id": "i3", "tier": "drop", "node_id": "n3",
         "label": "开场寒暄与天气", "source_spans": [[8, 8]]},
    ]}


def _pack_legacy():
    """v5 旧形态（无 tier）：缺 tier 按主干保守拦截。"""
    return {"items": [
        {"id": "i1", "label": "专业素养：知识体系与市场洞察", "source_spans": [[2, 3]]},
        {"id": "i2", "label": "供应链掌控：库存周转与账期", "source_spans": [[5, 6]]},
    ]}


class TestTierFilter:
    def test_trunk_only_intercepted(self):
        # trunk 缺落点 → 拦截；support/drop 缺落点 → 不拦
        note = "只写了供应链的账期细节。"
        issues = NC.coverage_issues(_pack_v6(), note)
        assert [x["ref"] for x in issues] == ["i1"]

    def test_support_drop_go_to_info_report(self):
        note = "只写了供应链的账期细节。"
        rep = NC.coverage_report("源", _pack_v6(), note)
        refs = [x["ref"] for x in rep["info"]["nonsupport_suspects"]]
        assert refs == ["i2", "i3"]
        assert all(x["tier"] in ("support", "drop") for x in rep["info"]["nonsupport_suspects"])

    def test_missing_tier_conservative_trunk(self):
        note = "正文谈了专业素养：知识体系与市场洞察。"
        issues = NC.coverage_issues(_pack_legacy(), note)
        assert [x["ref"] for x in issues] == ["i2"]

    def test_trunk_hit_rate_not_intercepted_when_covered(self):
        note = "专业素养：知识体系与市场洞察，供应链掌控：库存周转与账期，都写了。"
        assert NC.coverage_issues(_pack_v6(), note) == []

    def test_threshold_still_frozen(self):
        assert NC.LABEL_3GRAM_THRESHOLD == 0.2

    def test_issue_carries_tier_field(self):
        note = "无关正文。"
        issues = NC.coverage_issues(_pack_v6(), note)
        assert issues[0]["tier"] == "trunk"


class TestThreeExitProtocol:
    def test_check_script_header_documents_three_exits(self):
        text = io.open(os.path.join(ROOT, "scripts", "check_note_coverage.py"),
                       encoding="utf-8").read()
        assert "出口②" in text or "出口 ②" in text
        assert "可舍弃" in text
        assert "纯插入" in text
        assert "改写也算含" in text

    def test_emit_patch_material_documents_three_exits(self):
        text = io.open(os.path.join(ROOT, "scripts", "check_note_coverage.py"),
                       encoding="utf-8").read()
        # 补稿材料里的协议也要带三出口（补稿 Agent 只看材料不看脚本）
        assert "可舍弃" in text
        assert "舍弃理由" in text

    def test_checklist_refresh_only_for_newly_covered(self):
        # EXTRACT_CHECKLIST 刷新条件同步：出口②勾销的不登记，仅新增覆盖条目补登记
        text = io.open(os.path.join(ROOT, "scripts", "check_note_coverage.py"),
                       encoding="utf-8").read()
        assert "新增覆盖" in text


class TestWritingContractParagraph:
    def test_pack_block_carries_selection_rights(self):
        from shared import distill_dispatch as DD
        block = DD.render_pack_block(
            {"items": [], "hierarchy": {"intent": "", "children": []},
             "coverage": {"complete": True, "chunk_count": 1}}, style="json")
        for kw in ("trunk", "support", "drop", "选材权"):
            assert kw in block, f"材料包块缺选材权语义：{kw}"

    def test_selection_rights_intent_not_into_draft(self):
        from prompts import templates as T
        text = T.render_selection_rights()
        assert "不进稿" in text
        assert "不设字数" in text or "字数目标" in text
        assert "静默消失" in text or "显式声明舍弃" in text

    def test_lines_style_carries_selection_rights(self):
        from shared import distill_dispatch as DD
        block = DD.render_pack_block(
            {"items": [], "hierarchy": {"intent": "", "children": []},
             "coverage": {"complete": True, "chunk_count": 1}}, style="lines")
        assert "选材权" in block

    def test_selection_rights_covers_f3r_three_failures(self):
        """F3R §9.2：三类生成端失真（案例错配/概括越界/强度漂移）要进写作契约。"""
        from prompts import templates as T
        text = T.render_selection_rights()
        assert "素材归属" in text
        assert "作用范围" in text
        assert "强度" in text and "充分条件" in text
        assert "不得把 A 案例/节点的细节移进 B 案例/节点" in text

    def test_pack_block_carries_f3r_constraints(self):
        from shared import distill_dispatch as DD
        block = DD.render_pack_block(
            {"items": [], "hierarchy": {"intent": "", "children": []},
             "coverage": {"complete": True, "chunk_count": 1}}, style="json")
        assert "素材归属随条目走" in block
        assert "概括回查作用范围" in block
        assert "强度与条件一致" in block


class TestF3RSemanticReview:
    """F3R §9.3：补稿任务先做语义核对；生产只报不改，隔离复验可定点替换。"""

    def test_semantic_priorities_cover_three_failures(self):
        text = NC.SEMANTIC_REVIEW_PRIORITIES
        assert "速览" in text and "骨架" in text and "结论" in text
        assert "案例归属" in text
        assert "强度" in text and "条件" in text

    def test_patch_task_reports_semantics_without_rewrite_by_default(self, tmp_path):
        mod = _load_check_script()
        p = tmp_path / "patch.md"
        mod._emit_patch_task(str(p), "成稿正文", "源正文", {"items": []}, [])
        text = p.read_text(encoding="utf-8")
        assert "语义核对" in text
        assert "只报不改" in text
        assert "定点替换" not in text

    def test_patch_task_semantic_fix_allows_targeted_replace(self, tmp_path):
        mod = _load_check_script()
        p = tmp_path / "patch.md"
        mod._emit_patch_task(str(p), "成稿正文", "源正文", {"items": []}, [],
                             semantic_fix=True)
        text = p.read_text(encoding="utf-8")
        assert "定点替换" in text
        assert "不适用「只插不改」" in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
