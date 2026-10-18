"""A6 / T9 条件必备结构测试：源侧机械信号 → 小节升为必备。

保护需求（PLAN-20261018 §6 A6 / §12.3 #6）：
- 信号必须**从严**（宁可不触发，不可误伤）：实测全库 1298 篇只有 ~31 例触发、1 例缺节。
- 信号触发时该小节升为**硬拦**（源支持却被省略 = 确定性内容缺陷）；不触发时仍只是可选模块。
- 无条件必备（`required`）行为不变；未知类型不施加结构假设。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from prompts import content_signals as CS
from prompts import templates


# ---------------------------------------------------------------- 信号本身

class TestSignals:
    def test_counterexample_needs_marker(self):
        assert CS.signal_counterexample("这是一段普通叙述，讲流程。")["fired"] is False
        assert CS.signal_counterexample("这里有个反例：小王照做了却亏了。")["fired"] is True
        # 单个弱标记不算（要从严）；要 ≥2 个**不同**标记（同词重复不算）
        assert CS.signal_counterexample("这是个例外。")["fired"] is False
        assert CS.signal_counterexample("这是个例外，另一个例外也是。")["fired"] is False
        assert CS.signal_counterexample("这是个例外，与之相反的说法更常见。")["fired"] is True

    def test_formula_needs_repetition_or_equations(self):
        assert CS.signal_formula("这个公式很有用")["fired"] is False          # 只提一次不算
        assert CS.signal_formula("先看公式，再看公式的推导")["fired"] is True
        assert CS.signal_formula("a = b\nc = d\ne = f")["fired"] is True

    def test_chapters(self):
        assert CS.signal_chapters("随便讲讲")["fired"] is False
        assert CS.signal_chapters("第一章讲起因")["fired"] is False           # 单章名不算
        assert CS.signal_chapters("第一章起因，第二章转折")["fired"] is True
        assert CS.signal_chapters("本书的目录如下")["fired"] is True

    def test_comparison_needs_two_sentences_with_shared_unit(self):
        assert CS.signal_comparison("苹果 10 元。")["fired"] is False
        one = "苹果 10 元，香蕉 20 元。"
        assert CS.signal_comparison(one)["fired"] is False                    # 只 1 组
        two = "苹果 10 元，香蕉 20 元。上月苹果 8 元，香蕉 15 元。"
        assert CS.signal_comparison(two)["fired"] is True

    def test_numeric_dense(self):
        assert CS.signal_numeric_dense("营收 10 亿")["fired"] is False
        dense = "营收 10 亿、利润 2 亿、毛利 3 亿、费用 1 亿、现金 5 亿、负债 4 亿、" \
                "用户 6 亿、订单 7 亿、产能 8 亿、库存 9 亿、研发 11 亿、广告 12 亿"
        assert CS.signal_numeric_dense(dense)["fired"] is True


# ---------------------------------------------------------------- 契约与判缺

class TestObligations:
    def test_every_type_declares_conditional(self):
        for t, spec in templates.SECTION_CONTRACTS.items():
            assert "conditional" in spec, t
            for section, sig in spec["conditional"].items():
                assert sig in CS.CONDITIONAL_SIGNALS, (t, section, sig)
                assert section in CS.SECTION_ALIAS, (t, section)   # 无别名 = 判不了，等于白配

    def test_required_behaviour_unchanged(self):
        assert CS.required_sections("structured") == ["分层速览"]
        assert CS.required_sections("unknown") == []

    def test_conditional_fires_from_source(self):
        src = "先看公式，再看公式的推导。这里有个反例：照做的人亏了。"
        fired = CS.conditional_sections("structured", src)
        assert fired == {"正反例对照": "counterexample", "核心公式": "formula"}

    def test_no_signal_means_no_obligation(self):
        # 源里既无公式也无反例 → 不新增任何必备（不触发就不拦）
        note = "> **30秒速览**：讲了个流程。\n\n## 分维度拆解\n按步骤做。"
        got = CS.structure_obligations(note, "structured", "这是一段普通流程叙述。")
        assert got["missing"] == [] and got["missing_conditional"] == []

    def test_signal_fires_but_section_absent_is_flagged(self):
        src = "先看公式，再看公式的推导。"
        note = "> **30秒速览**：讲了个流程。\n\n## 分维度拆解\n按步骤做。"
        got = CS.structure_obligations(note, "structured", src)
        assert got["missing_conditional"] == [{"section": "核心公式", "signal": "formula"}]

    def test_signal_fires_and_section_present_passes(self):
        src = "先看公式，再看公式的推导。"
        note = "> **30秒速览**：讲流程。\n\n## 核心公式\n总价 = 单价 × 数量。"
        got = CS.structure_obligations(note, "structured", src)
        assert got["missing_conditional"] == []

    def test_roundup_comparison_requires_matrix(self):
        src = "苹果 10 元，香蕉 20 元。上月苹果 8 元，香蕉 15 元。"
        note = "## 使用建议\n买苹果。"
        got = CS.structure_obligations(note, "roundup", src)
        sections = {m["section"] for m in got["missing_conditional"]}
        assert sections == {"对比矩阵", "横向结论"}


# ---------------------------------------------------------------- 接线（硬拦）

class TestGateWiring:
    def test_flag_added_and_classified_hard(self):
        src = "先看公式，再看公式的推导。" + "补充说明。" * 40
        note = "> **30秒速览**：讲流程。\n\n## 分维度拆解\n" + "按步骤执行并记录每一步的输入输出。" * 20
        cf = CS.content_flags(note, src, "structured")
        hit = [f for f in cf["flags"] if "条件结构缺失" in f]
        assert hit and "核心公式" in hit[0]
        assert hit[0] in CS.classify_content_flags(cf["flags"])["hard"]

    def test_no_source_means_no_conditional_judgement(self):
        # 没给源时只能判无条件必备（保持旧行为，不瞎报条件结构缺失）
        note = "> **30秒速览**：x\n\n## 分维度拆解\n" + "按步骤。" * 40
        cf = CS.content_flags(note, "", "structured")
        assert not [f for f in cf["flags"] if "条件结构缺失" in f]
