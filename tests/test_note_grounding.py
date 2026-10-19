# -*- coding: utf-8 -*-
"""P1：分析层数字溯源 + 表格数字归属（只读审计）。"""
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from shared import note_grounding as NG  # noqa: E402


# ---------------------------------------------------------------- 分析层数字

def test_analysis_unsourced_chinese_number_flagged():
    src = "这套系统我已经用了半年，现在从选题到出稿 5 分钟。"
    note = "## 七、笔记者分析\n**脉络还原**：他已经有三年份的素材。\n"
    flags = NG.analysis_number_flags(note, src)
    assert [f["number"] for f in flags] == ["三年"]


def test_analysis_list_number_and_question_skipped():
    src = "半年，5 分钟。"
    note = ("## 八、自测三问\n"
            "1. 如果现金只够撑三个月，该怎么办？\n"
            "> [!question]- 只有 90 天现金，还做不做？\n")
    assert NG.analysis_number_flags(note, src) == []


def test_analysis_estimate_marker_skipped():
    src = "半年，5 分钟。"
    note = "## 七、笔记者分析\n（估算）约两年后能回本。\n"
    assert NG.analysis_number_flags(note, src) == []


def test_analysis_number_present_in_source_not_flagged():
    src = "他用半年沉淀，从选题到出稿 5 分钟。"
    note = "## 七、笔记者分析\n沉淀半年之后，5 分钟出稿。\n"
    assert NG.analysis_number_flags(note, src) == []


# ---------------------------------------------------------------- 表格归属

SRC = """哥飞：SEO 分享
哥飞说 2025 年谷歌排名因素：内容 21%、元标签 15%、反链 14%。
阿彪：AI 产品出海
阿彪这次分享海外营销三板斧：SEO + 广告 + 红人。
阿彪也不建议选品 PDF 编辑、AI 客服知识库。
"""

NOTE = """| 主体 | 结果 | 口径 |
|---|---|---|
| 哥飞 | 排名因素 21/15/14 | 讲者引用 |
| 阿彪 | 2025 排名因素五项占比 | 讲者引用 |
| 看爆款 | 每天自动采集，不用手动翻 | — |
"""


def test_table_attribution_wrong_speaker_flagged():
    flags = NG.table_attribution_flags(NOTE, SRC)
    assert len(flags) == 1
    assert flags[0]["subject"] == "阿彪"
    assert flags[0]["term"] == "排名因素"
    assert flags[0]["owners"] == ["哥飞"]


def test_table_attribution_correct_row_not_flagged():
    note = NOTE.replace("| 阿彪 | 2025 排名因素五项占比 | 讲者引用 |",
                        "| 阿彪 | 海外营销三板斧 | 讲者经验 |")
    assert NG.table_attribution_flags(note, SRC) == []


def test_table_non_speaker_row_not_flagged():
    note = "| 看爆款 | 每天自动采集，不用手动翻 | — |\n"
    assert NG.table_attribution_flags(note, SRC) == []


def test_speaker_section_ignores_step_headings():
    src = "第一步：建目录\n第二步：写文件\n"
    assert NG.speaker_sections(src) == {}


def test_manifest_cli_runs_batch(tmp_path, monkeypatch, capsys):
    src = tmp_path / "src.md"
    note = tmp_path / "note.md"
    src.write_text("哥飞：SEO\n哥飞说排名因素 21%。\n阿彪：产品\n阿彪做海外营销。\n", encoding="utf-8")
    note.write_text("| 主体 | 结果 |\n|---|---|\n| 哥飞 | 排名因素 21% |\n| 阿彪 | 排名因素五项 |\n", encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"items": [
        {"id": "X1", "note": "note.md", "source": "src.md"}]}, ensure_ascii=False),
        encoding="utf-8")

    from scripts import audit_note_grounding as AG
    monkeypatch.setattr(sys, "argv", ["audit_note_grounding.py", "--manifest", str(manifest)])
    assert AG.main() == 0
    out = capsys.readouterr().out
    assert "X1" in out and "表格归属 1" in out
