"""悬空指代检测护栏：`PLAN-20261018-knowledge-layer` §4 / §6 A7（T4）。

守的是「这个判据本身可信」，而不是凑覆盖率：

1. **真阳能被抓到**：典型的悬空段首（这个方法 / 如上所述 / 人称代词）必须命中。
2. **结构性行不参与**：标题、表格、代码、任务列表、引用块不得被当成正文段落统计。
3. **形态信号的老教训不得重演**：本项目括号密度假阳性 63.2% 的前车之鉴——
   这里的每一条 pattern 都是白名单短语，且 `这个总则` 之类的非回指开头不得命中。
4. **命中率低的结论要能被机器说出来**：`verdict()` 阈值决定「要不要为它立项」，
   这条结论不能靠人看图说话。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import pytest  # noqa: E402

from shared import dangling_ref as DR  # noqa: E402


def hits_of(raw: str) -> list:
    return DR.scan_note(raw)["hits"]


class TestTruePositives:
    @pytest.mark.parametrize("para", [
        "这个方法的成本只有原来的三分之一，值得一试。",
        "这一点很重要，因为它决定了后面所有的取舍。",
        "如上所述，我们需要重新审视这个假设。",
        "后者的问题在于缺少对照组。",
        "他自己也说不清为什么当时会那样决定。",
        "同样的逻辑，在消费行业里也成立。",
        "后者与前者最大的区别是现金流结构。",
    ])
    def test_typical_dangling_starts_detected(self, para):
        body = f"来源：某某\n\n{para}\n\n下一段内容说明别的事情，长度足够长以免被过滤掉。"
        assert hits_of(body), f"应命中：{para}"

    def test_after_heading_not_counted_as_body(self):
        """标题行本身是结构，不作只读正文判；标题后的段首才是检索单元。"""
        body = "## 这个阶段\n\n这个方法基于上一节的定义，长度足够长以免被过滤掉。"
        kinds = [h["kind"] for h in hits_of(body)]
        assert kinds == ["指示名词/para"]


class TestNoFalsePositives:
    @pytest.mark.parametrize("line", [
        "| 这个方法 | 说明 |",
        "```这个方法只是代码```",
        "- [ ] 这个待办事项不属于回指",
        "> 这个引用块由上游规则渲染，不参与本篇统计",
        "核心结论：先把口径对齐，再谈收益率高低差别在哪里。",
        "定价合理的标的通常不需要频繁择时，真正的差别在执行频率和持有周期。",
        "此外，还有一个前提需要先说明清楚。",
    ])
    def test_structural_or_non_anaphoric_lines_not_counted(self, line):
        body = f"开头一段足够长的内容铺垫在这里作为上下文使用。\n\n{line}"
        assert not [h for h in hits_of(body) if line[:6] in h["text"]]

    def test_short_transition_paragraphs_ignored(self):
        body = "前一段足够长的内容铺垫在这里作为上下文使用。\n\n如上\n\n再一段足够长的内容。"
        assert hits_of(body) == []


class TestGranularity:
    NOTE = (
        "开头一句足够长的内容作为铺垫使用。\n\n"
        "这个做法在上一段已经铺垫过，长度足够长。\n"
        "它还会延续同一个意思但不算新的块首。\n\n"
        "另外一段独立内容写在这里，长度也足够长。\n"
    )

    def test_block_level_counts_only_block_heads(self):
        res = DR.scan_note(self.NOTE)
        assert res["paragraphs"] == 3
        assert len(res["hits"]) == 1
        assert res["hits"][0]["kind"] == "指示名词/para"

    def test_line_level_is_a_superset_observation(self):
        """行级是辅助观察：命中应 >= block 级（下游会把邻文补回来，故不作主判据）。"""
        res = DR.scan_note(self.NOTE)
        assert res["line_scan"]["hits"], "行级应能看到块内的延续行"
        assert len(res["line_scan"]["hits"]) >= len(res["hits"])


class TestVerdict:
    def test_low_rate_says_do_nothing(self):
        agg = {"paras_with_hits_pct": 0.0092}
        assert "不建议" in DR.verdict(agg)

    def test_high_rate_still_forbids_rewrite_pass(self):
        agg = {"paras_with_hits_pct": 0.08}
        v = DR.verdict(agg)
        assert "生成端预防" in v and "不建独立改写轮" in v


class TestAggregate:
    def test_aggregate_math(self):
        r1 = {"paragraphs": 10, "hits": [{"kind": "指示名词/para", "text": "x"}],
              "line_scan": {"lines": 20, "hits": [1]}}
        r2 = {"paragraphs": 10, "hits": [], "line_scan": {"lines": 20, "hits": []}}
        agg = DR.aggregate([r1, r2])
        assert agg["paragraphs"] == 20 and agg["hits"] == 1
        assert agg["paras_with_hits_pct"] == pytest.approx(0.05)
        assert agg["notes_with_hits"] == 1
        assert agg["line_scan"] == {"lines": 40, "hits": 1, "rate": 0.025}
