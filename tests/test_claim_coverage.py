"""A5 / T5 论点级判据测试（报告项）。

保护需求（PLAN-20261018 §6 A5 / §12.3 #5）：
- 判据下沉到**论点句**，补上「硬锚点召回不适用」的那 63% 盲区。
- 机械半只出**报告项**：不计分、不拦盘、不触发重交；判定交独立审核者（宿主＝A3 异步审核）。
- 失真优先：源外数字、方向反转要单独列出来（比精度损失更危险）。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from prompts import claim_coverage as CC

SOURCE = (
    "大家好，我们下期再见。\n"
    "作者认为短期关系的本质是人群资源问题，不是聊天技术问题。\n"
    "必须拥有大量人群供你筛选，靠一两个人无法精准筛出目标人群。\n"
    "长期看好这个赛道，2023 年营收 12.3 亿美元。\n"
    "风险在于政策随时可能转向。\n"
)


# ---------------------------------------------------------------- 候选提取

class TestCandidates:
    def test_skips_filler_and_keeps_claims(self):
        texts = [c["text"] for c in CC.claim_candidates(SOURCE)]
        assert not any("下期再见" in t for t in texts)
        assert any("短期关系的本质" in t for t in texts)
        assert any("必须拥有大量人群" in t for t in texts)

    def test_returns_source_order(self):
        cands = CC.claim_candidates(SOURCE)
        assert [c["i"] for c in cands] == sorted(c["i"] for c in cands)

    def test_too_short_sentences_skipped(self):
        assert CC.claim_candidates("好的。嗯。是的。") == []


# ---------------------------------------------------------------- 覆盖率

class TestCoverage:
    def test_suspect_missed_listed(self):
        note = "作者认为短期关系的本质是人群资源问题，不是聊天技术问题。"
        rep = CC.claim_coverage(note, SOURCE)
        assert rep["applicable"] is True
        assert any("必须拥有大量人群" in t for t in rep["suspect_missed"])

    def test_paraphrase_same_statement_not_suspected(self):
        # 同义改写：粗筛口径下不该被列进「疑似无落点」（误报会推高审核成本）
        note = "必须拥有大量人群供你筛选，一两个人筛不出目标人群。"
        rep = CC.claim_coverage(note, SOURCE)
        assert not any("必须拥有大量人群" in t for t in rep["suspect_missed"])

    def test_not_applicable_when_source_has_no_claims(self):
        rep = CC.claim_coverage("随便一句。", "好的。嗯。")
        assert rep["applicable"] is False
        assert rep["suspect_missed"] == []


# ---------------------------------------------------------------- 失真嫌疑

class TestDistortion:
    def test_number_not_in_source_flagged(self):
        note = "2023 年营收 99.9 亿美元，长期看好。"         # 源是 12.3 亿美元
        rep = CC.claim_coverage(note, SOURCE)
        assert "99.9亿美元" in rep["numbers_not_in_source"]

    def test_number_in_source_not_flagged(self):
        note = "2023 年营收 12.3 亿美元。"
        rep = CC.claim_coverage(note, SOURCE)
        assert rep["numbers_not_in_source"] == []

    def test_reversal_suspect(self):
        # 源说「看好」、笔记只说「看空」，且两边互不出现 → 疑方向反转
        src = "作者认为这个行业风险很大。" + "他长期看好这个赛道。" * 2
        note = "作者看空这个赛道，风险很大。"
        rep = CC.claim_coverage(note, src)
        assert rep["reversal_suspects"]


# ---------------------------------------------------------------- 报告项出口

class TestReasons:
    def test_reasons_and_block(self):
        rep = {"applicable": True, "candidates": 5, "suspect_missed": ["a", "b"],
               "numbers_not_in_source": ["99.9亿美元"], "reversal_suspects": []}
        rs = CC.review_reasons(rep)
        assert any(r.startswith("claim_suspect:") for r in rs)
        assert any(r.startswith("number_not_in_source:") for r in rs)
        block = CC.render_review_block(rep)
        assert "机械前置" in block and "待你裁定" in block and "99.9亿美元" in block

    def test_no_score_is_emitted(self):
        # 实测机械覆盖率与手工保留率相关性弱 → 报告里不给分数，避免被当指标用
        rep = CC.claim_coverage("随便写点。", SOURCE)
        assert "coverage" not in rep
        assert not any("coverage" in r for r in CC.review_reasons(rep))

    def test_inapplicable_yields_nothing(self):
        rep = {"applicable": False}
        assert CC.review_reasons(rep) == [] and CC.render_review_block(rep) == ""
