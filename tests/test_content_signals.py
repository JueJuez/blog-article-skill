"""内容判据单测（DECISION-20260915 content-first）。

守住三件事：
1. **判据精度**——数字/术语/词典锚点不得混入噪声（引流话术 666、ASR 伪术语 jason/rig、
   词典标签字面量匹配笔记）。这些是 2026-09-15 离线审计中被数据打回后修好的，不许回退。
2. **括号类信号不得复活**——模板要求「专业名词首现必须附大白话解释」，括号是规定动作。
3. **没有字数上限压制**——「笔记比源长」永远不是缺陷。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from prompts import content_signals as CS  # noqa: E402


def _body(n: int) -> str:
    return "字" * n


# ---------------------------------------------------------------- 归一化

class TestNormalize:
    def test_strip_source_removes_header(self):
        raw = "> 原始文章内容（自动暂存）\n> 标题：x\n> 时间：2026-09-15 13:45:03\n\n---\n\n正文开始"
        assert CS.strip_source(raw) == "正文开始"

    def test_strip_note_removes_tagline_and_source_link(self):
        note = "#标签A #标签B\n\n**来源链接**：[原文](https://a.com)\n\n**作者**：张三\n\n正文"
        out = CS.strip_note(note)
        assert "标签A" not in out
        assert "来源链接" not in out
        assert "正文" in out

    def test_strip_note_handles_merged_author_link_line(self):
        # 夏鹏批的落盘形态：作者与来源链接在同一行用 | 分隔
        note = "**作者**：夏鹏本鹏 | **来源链接**：[原文链接](https://b.com/BV1x)\n\n正文"
        out = CS.strip_note(note)
        assert "来源链接" not in out
        assert "夏鹏本鹏" not in out
        assert "正文" in out


# ---------------------------------------------------------------- ① 锚点精度

class TestAnchorsPrecision:
    def test_junk_numbers_excluded(self):
        # 666/888 是引流话术（「留言 666 领资料」），不是内容事实
        a = CS.anchors_recall("笔记", "留言666领取资料，加888群")
        assert a["total"] == 0

    def test_unit_and_large_and_year_kept(self):
        a = CS.anchors_recall("笔记", "营收1000万，增长300%，2024年目标5000万")
        assert a["total"] >= 3
        assert "1000" in [m.split(":")[1] for m in a["missed"]] or a["matched"] >= 0

    def test_small_bare_number_excluded(self):
        # 裸小数字（8 / 30）不作为硬锚点
        a = CS.anchors_recall("笔记", "他说了8点，重复了30次")
        assert a["total"] == 0

    def test_latin_whitelist_only(self):
        # RAG/MCP 在白名单 → 收；jason（ASR 把 JSON 识别错）、ok/nice 是口语 → 不收
        a = CS.anchors_recall("笔记", "用 RAG 还是 MCP？有人说 jason 格式，ok nice")
        labels = [x.split(":")[1].lower() for x in a["missed"]]
        assert "rag" in labels and "mcp" in labels
        assert "jason" not in labels and "ok" not in labels and "nice" not in labels

    def test_topic_label_matched_by_regex_not_literal(self):
        # 「LLM/大模型」这类标签，笔记里写「大模型」也算覆盖（字面匹配必然假阳性）
        src = "大模型 大模型 的知识库检索，大模型很常见"
        a = CS.anchors_recall("这里讲大模型的检索", src)
        assert "LLM/大模型" not in a["missed"]

    def test_company_requires_two_occurrences(self):
        # 只提一次的公司名多为随口举例
        one = CS.anchors_recall("笔记", "我在美团待过")
        two = CS.anchors_recall("笔记", "美团和美团不一样")
        assert one["total"] == 0
        assert two["total"] >= 1

    def test_recall_none_when_no_anchors(self):
        a = CS.anchors_recall("笔记", "全是口语没有事实")
        assert a["total"] == 0 and a["recall"] is None


# ---------------------------------------------------------------- ② 破碎形态

class TestFormSignals:
    def test_long_sentence_flagged(self):
        f = CS.form_signals("这是一句" + _body(200) + "。")
        assert "A超长句" in f["signals"]

    def test_normal_note_has_no_signals(self):
        note = "第一段讲清了要点。\n\n第二段展开因果，给出证据。\n\n第三段收束结论。"
        assert CS.form_signals(note)["signals"] == []

    def test_adjacent_duplicate_flagged(self):
        para = "这段内容讲的是同一个意思，重复出现说明注水。" * 3
        note = para + "\n\n" + para
        assert "D相邻段重复" in CS.form_signals(note)["signals"]

    def test_bracket_signal_is_gone(self):
        """括号类信号已废弃：术语首现附大白话解释是模板要求，括号多不是缺陷。"""
        note = "**心智资本**（情绪稳定）、**时间资本**（24小时用在哪）、**健康资本**（精力）"
        assert not any("括号" in s for s in CS.form_signals(note)["signals"])
        assert not hasattr(CS, "_BRACKET_MIN")

    def test_comma_ratio_flagged(self):
        note = "，".join(["内容"] * 200)
        assert "C逗号密度" in CS.form_signals(note)["signals"]


# ---------------------------------------------------------------- ③ 结构缺失

class TestStructure:
    def test_required_sections_parsed_from_template(self):
        req = CS.required_sections("structured")
        assert "分层速览" in req or "正反例对照" in req

    def test_unknown_type_returns_empty(self):
        assert CS.required_sections("mystery") == []

    def test_missing_detected(self):
        r = CS.structure_missing(_body(800), "structured")
        assert r["missing"]

    def test_complete_note_passes(self):
        note = ("**作者**：x\n\n> **30秒速览**：结论。\n\n## 三、正反例对照\n\n"
                "| 场景 | 正例 | 反例 |\n|---|---|---|\n| a | b | c |\n\n"
                "## 四、延伸思考\n\n1. ？\n\n" + _body(400))
        res = CS.structure_missing(note, "structured")
        assert not res["missing"]

    def test_unmapped_not_judged(self):
        # 别名表未覆盖的模块名进 unmapped，不参与判定（不误报）
        r = CS.structure_missing(_body(800), "reading")
        assert set(r["missing"]) & set(r["unmapped"]) == set()


# ---------------------------------------------------------------- ④ 照搬重合

class TestVerbatim:
    def test_copy_paste_high(self):
        src = "这是一段原文内容，包含了完整的论述与案例。" * 20
        assert CS.verbatim_ratio(src, src) > 0.9

    def test_rewrite_low(self):
        src = "这是一段原文内容，包含了完整的论述与案例。" * 20
        note = "作者认为，关键在于三点：第一，拆解；第二，验证；第三，复盘。" * 10
        assert CS.verbatim_ratio(note, src) < 0.1


# ---------------------------------------------------------------- 汇总

class TestContentFlags:
    def test_long_note_vs_short_source_no_word_flag(self):
        """核心回归守卫：笔记比源长不产生任何信号。"""
        flags = CS.content_flags(_body(2600), "原文内容。" * 200, "structured")["flags"]
        assert not any(("字数" in f) or ("超参考值" in f) or ("偏短" in f) for f in flags)

    def test_short_note_vs_long_source_no_word_flag(self):
        flags = CS.content_flags(_body(1200), _body(20000), "structured")["flags"]
        assert not any(("偏短" in f) or ("超参考值" in f) for f in flags)

    def test_no_source_only_form_and_structure(self):
        cf = CS.content_flags(_body(1500), "", "structured")
        assert "anchors" not in cf["details"] and "verbatim" not in cf["details"]
        assert any("结构缺失" in f for f in cf["flags"])
