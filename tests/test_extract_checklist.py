"""A4 清单内化（T15/T6）测试：长源提取清单的注入、剥离、机械校验、对账与存档。

保护需求（PLAN-20261018 §6 A4 / §12.3 #11-a、#12、#13）：
- 主路径无外部 provider，提取只能内化进条目 prompt；**只有源长过阈值才注入**，短源零改动。
- 清单是**机器可解析块**，落盘前必须剥离出正文，否则污染门禁字数与结构判据。
- 每条必须能被机械校验（原句能在源里命中）；命中不了即丢弃，不许蒙混进清单。
- 对账是**报告项**：未命中只记录、进审核队列观察，不计分、不拦盘、不触发重交。
"""
import json
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from prompts import extract_checklist as EX
from prompts import templates as templates_mod

BLOCK = EX.BEGIN + """
- [数字] 2023 年营收 12.3 亿美元 ‖ 2023 年营收达到 12.3 亿美元
- [专名] 京东 ‖ 与京东达成合作
- [判断] 长期看好该赛道 ‖ 我个人长期看好这个赛道
- [散落] 持仓里有腾讯 ‖ 我的持仓里有腾讯
- [数字] 根本不存在的数字 99999 ‖ 这个原句源里没有
""" + EX.END

SOURCE = (
    "我们这个行业去年变化很大。\n"
    "2023 年营收达到 12.3 亿美元，同比增长四成。\n"
    "后来与京东达成合作，打开了渠道。\n"
    "我个人长期看好这个赛道，但也要注意政策风险。\n"
    "顺带说一句，我的持仓里有腾讯。\n"
)

# 成文正文：含清单里可机械核对的数字与专名（对账才有 hit；纯 CJK 语义条目走 needs_model）
NOTE_BODY = "## 正文\n2023 年营收 12.3 亿美元，与京东达成合作，长期看好该赛道。" + "字" * 400


# ---------------------------------------------------------------- ① 注入

class TestInjection:
    def test_no_injection_in_middle_band(self):
        assert EX.render_extraction_rules(0) == ""
        assert EX.render_extraction_rules(5000) == ""
        assert EX.render_extraction_rules(EX.EXTRACT_MIN_CHARS) == ""

    def test_long_band_gets_full_checklist(self):
        rules = EX.render_extraction_rules(EX.EXTRACT_MIN_CHARS + 1)
        assert EX.BEGIN in rules and EX.END in rules
        assert "先列清单" in rules and "‖" in rules and "阶段一" in rules

    def test_short_band_gets_thin_hook_only(self):
        rules = EX.render_extraction_rules(1500)
        assert EX.SOURCE_THIN_TAG in rules
        assert "阶段一" not in rules          # 短源不要求列清单，只要事前判断

    def test_get_note_prompt_gated_by_source_chars(self):
        assert EX.BEGIN not in templates_mod.get_note_prompt("structured", 5000)
        assert EX.BEGIN in templates_mod.get_note_prompt("structured", 20000)
        assert EX.SOURCE_THIN_TAG in templates_mod.get_note_prompt("structured", 1500)

    def test_default_call_unchanged(self):
        # source_chars 默认 0：既不注入覆盖块也不注入清单（保持既有取模板语义）
        assert templates_mod.get_note_prompt("opinion") == templates_mod.NOTE_TEMPLATES["opinion"]["prompt"]

    @pytest.mark.parametrize("note_type", ["structured", "opinion", "key_points"])
    def test_long_prompt_keeps_single_fidelity_contract(self, note_type):
        prompt = templates_mod.get_note_prompt(note_type, 20000)
        assert prompt.count(templates_mod.SOURCE_FIDELITY_RULES) == 1


# ---------------------------------------------------------------- ② 解析 / 剥离

class TestParseStrip:
    def test_parse_wellformed(self):
        items = EX.parse_checklist(BLOCK)
        cats = [i["cat"] for i in items]
        assert cats == ["数字", "专名", "判断", "散落", "数字"]
        assert items[0]["quote"] == "2023 年营收达到 12.3 亿美元"
        assert all(not i["malformed"] for i in items)

    def test_parse_marks_malformed_without_silent_drop(self):
        items = EX.parse_checklist("- 没有方括号的条目\n- [数字] 缺原句\n")
        assert len(items) == 2
        assert items[0]["malformed"] is True
        assert items[1]["malformed"] is True   # 有类别但缺 ‖ 原句

    def test_parse_source_thin_tag(self):
        items = EX.parse_checklist(f"- [{EX.SOURCE_THIN_TAG}] 源只有三句话，无案例\n")
        assert items[0]["thin"] is True and items[0]["cat"] == EX.SOURCE_THIN_TAG

    def test_strip_removes_block_keeps_note(self):
        note = BLOCK + "\n\n## 正文\n这里是正文。\n"
        clean, block = EX.strip_block(note)
        assert EX.BEGIN not in clean and EX.END not in clean
        assert "这里是正文" in clean
        assert "2023 年营收 12.3 亿美元" in block   # 返回的是块内条目（marker 已剥离）

    def test_strip_noop_without_block(self):
        note = "## 正文\n没有清单。\n"
        clean, block = EX.strip_block(note)
        assert clean == note and block == ""
        assert EX.has_block(note) is False


# ---------------------------------------------------------------- ③ 机械校验（对源）

class TestVerify:
    def _items(self, raw):
        return EX.parse_checklist(raw)

    def test_quote_hit_kept(self):
        got = EX.verify_items(self._items(BLOCK), SOURCE)
        kept_texts = [i["text"] for i in got["kept"]]
        assert "2023 年营收 12.3 亿美元" in kept_texts
        assert "京东" in kept_texts

    def test_quote_not_in_source_dropped(self):
        got = EX.verify_items(self._items(BLOCK), SOURCE)
        dropped = [i["text"] for i in got["dropped"]]
        assert any("99999" in t for t in dropped)
        assert all(i["drop_reason"] == "quote_not_in_source" for i in got["dropped"])

    def test_ellipsis_quote_segments(self):
        block = EX.BEGIN + "\n- [数字] 营收 ‖ 2023 年营收……12.3 亿美元\n" + EX.END
        got = EX.verify_items(self._items(block), SOURCE)
        assert len(got["kept"]) == 1

    def test_token_hit_without_quote_still_malformed(self):
        # 无 ‖ 原句 = malformed → 丢弃（不许只靠 token 蒙混）
        block = EX.BEGIN + "\n- [数字] 12.3 亿美元\n" + EX.END
        got = EX.verify_items(self._items(block), SOURCE)
        assert not got["kept"] and got["dropped"][0]["drop_reason"] == "malformed"

    def test_whitespace_insensitive_match(self):
        block = EX.BEGIN + "\n- [专名] 京东 ‖ 与 京 东 达成合作\n" + EX.END
        got = EX.verify_items(self._items(block), SOURCE)
        assert len(got["kept"]) == 1


# ---------------------------------------------------------------- ④ 对账

class TestReconcile:
    def test_statuses(self):
        note = "2023 年营收 12.3 亿美元，与京东合作。"
        items = [{"cat": "数字", "text": "2023 年营收 12.3 亿美元", "quote": ""},
                 {"cat": "数字", "text": "毛利 8.8 亿美元", "quote": ""},
                 {"cat": "判断", "text": "长期看好", "quote": ""}]
        rep = EX.reconcile(note, items)
        assert rep["total"] == 3
        assert rep["hit"] == 1                 # 全 token 命中
        assert rep["miss"] == 1                # 一个 token 都不在
        assert rep["needs_model"] == 1         # 无机械 token → 交模型
        assert rep["unmatched"] == 1
        assert rep["unmatched_items"][0]["text"] == "毛利 8.8 亿美元"

    def test_partial_counts_as_unmatched(self):
        note = "2023 年营收 12.3 亿美元。"
        items = [{"cat": "数字", "text": "2023 年营收 12.3 亿美元，毛利 8.8 亿美元", "quote": ""}]
        rep = EX.reconcile(note, items)
        assert rep["partial"] == 1 and rep["unmatched"] == 1


# ---------------------------------------------------------------- ⑤ 端到端 process

class TestProcess:
    def test_end_to_end(self, tmp_path, monkeypatch):
        monkeypatch.setattr(EX, "STORE_DIR", str(tmp_path))
        note = BLOCK + "\n\n" + NOTE_BODY + "\n"
        out = EX.process(note, SOURCE, note_type="structured", title="T",
                         source_path="raw.md", source_chars=len(SOURCE))
        assert out["has_block"] is True
        assert EX.BEGIN not in out["note"]
        rep = out["report"]
        assert rep["total"] == 4                      # 丢弃的 1 条不进对账
        assert rep["dropped_unverifiable"] == 1
        assert rep["hit"] == 2
        assert "清单 4 条" in out["report_line"]
        # 存档契约：按源 hash 归档，含 items / 对账 / 版本 hash
        assert out["store_path"] and Path(out["store_path"]).exists()
        rec = json.load(open(out["store_path"], encoding="utf-8"))
        assert rec["schema_version"] == EX.SCHEMA_VERSION
        assert rec["prompt_sha256"] == EX.rules_hash()
        assert rec["source_sha256"] != "unknown"
        assert len(rec["items"]) == 4

    def test_thin_only_block_from_short_source(self, tmp_path, monkeypatch):
        monkeypatch.setattr(EX, "STORE_DIR", str(tmp_path))
        note = (f"{EX.BEGIN}\n- [{EX.SOURCE_THIN_TAG}] 无案例、无数据，只有一句结论\n{EX.END}\n"
                "\n## 正文\n" + "字" * 400 + "\n")
        out = EX.process(note, "短源。", note_type="case", source_chars=200)
        assert out["has_block"] is True
        assert EX.BEGIN not in out["note"]
        assert out["report"]["source_thin"] is True
        assert out["report"]["total"] == 0
        assert "source_thin" in EX.review_reasons(out["report"])

    def test_no_block_is_passthrough(self, tmp_path, monkeypatch):
        monkeypatch.setattr(EX, "STORE_DIR", str(tmp_path))
        note = "## 正文\n没有清单。\n"
        out = EX.process(note, SOURCE)
        assert out["has_block"] is False
        assert out["note"] == note
        assert list(tmp_path.iterdir()) == []          # 不产生存档

    def test_never_raises_on_garbage(self, tmp_path, monkeypatch):
        monkeypatch.setattr(EX, "STORE_DIR", str(tmp_path))
        out = EX.process(EX.BEGIN + "\n乱码\n" + EX.END, None)
        assert out["note"] is not None                 # 有降级、无异常

    def test_regen_appends_superseded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(EX, "STORE_DIR", str(tmp_path))
        note1 = BLOCK + "\n\n## 正文\n" + "字" * 400 + "\n"
        EX.process(note1, SOURCE, note_type="structured", title="T", source_chars=len(SOURCE))
        note2 = BLOCK + "\n\n## 正文\n" + "词" * 400 + "\n"
        out2 = EX.process(note2, SOURCE, note_type="structured", title="T", source_chars=len(SOURCE))
        rec = json.load(open(out2["store_path"], encoding="utf-8"))
        assert rec["superseded"] and rec["superseded"][0]["note_sha256"]

    def test_lookup_by_source_and_note(self, tmp_path, monkeypatch):
        monkeypatch.setattr(EX, "STORE_DIR", str(tmp_path))
        note = BLOCK + "\n\n## 正文\n" + "字" * 400 + "\n"
        out = EX.process(note, SOURCE, note_type="structured", source_chars=len(SOURCE))
        rec = json.load(open(out["store_path"], encoding="utf-8"))
        assert EX.load_for_source(rec["source_sha256"], str(tmp_path))["note_sha256"] == rec["note_sha256"]
        assert EX.load_for_note(rec["note_sha256"], str(tmp_path))["title"] == ""
        assert EX.load_for_note("deadbeef", str(tmp_path)) == {}


# ---------------------------------------------------------------- ⑥ 审核理由（报告项出口）

class TestReviewReasons:
    def test_no_reasons_when_clean(self):
        assert EX.review_reasons({"unmatched": 0}) == []

    def test_unmatched_and_thin_labels(self):
        rs = EX.review_reasons({"unmatched": 3, "source_thin": True})
        assert any(r.startswith("checklist_miss:") for r in rs)
        assert "source_thin" in rs


# ---------------------------------------------------------------- ⑦ 落盘接线

class TestSaveSummaryOnlyWiring:
    """落盘路径必须剥离清单：否则清单块会被算进字数与结构判据，污染门禁。"""

    @pytest.fixture(autouse=True)
    def _stub(self, monkeypatch, tmp_path):
        from articles import dedup
        from articles import main as articles_main
        from prompts import review_rubric
        self.tmp = tmp_path
        self.captured = []
        monkeypatch.setattr(articles_main, "save_summarized_article",
                            lambda content, **k: (self.captured.append(content), ("fmt", "f.md"))[1])
        monkeypatch.setattr(dedup, "mark_summarized", lambda *a, **k: None)
        monkeypatch.setattr(dedup, "_CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dedup, "_INDEX_FILE", str(tmp_path / "dedup.json"))
        monkeypatch.setattr(review_rubric, "queue_for_review", lambda **k: None)
        monkeypatch.setattr(EX, "STORE_DIR", str(tmp_path / "cx"))

    def test_block_stripped_before_gate_and_save(self):
        from articles import main as articles_main
        note = BLOCK + "\n\n" + NOTE_BODY + "\n"
        res = articles_main.save_summary_only({
            "summarized_content": note, "note_type": "",
            "source_text": SOURCE, "source_chars": len(SOURCE)})
        assert res.get("success") is True
        assert self.captured, "save_summarized_article 未被调用"
        assert EX.BEGIN not in self.captured[0]
        assert res.get("checklist_report", {}).get("hit") == 2
        # 存档落盘（§12.3 #12）
        assert list((self.tmp / "cx").glob("*.json"))
