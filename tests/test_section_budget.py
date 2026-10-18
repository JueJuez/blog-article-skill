"""短源篇幅取向（PLAN-20261019-short-source-trim）的机械护栏。

守四件事：

1. **阈值边界**：`≤5000` 进短源模式、`>5000` 不进、`0/None`（未知源长）**不误伤**；
2. **块内容可核**：短源块含标记与黑名单小节名；黑名单**只列该类型模板里确实存在**的小节
   （否则等于对不存在的节下禁令，白说）；
3. **注入点**：`get_note_prompt` 短源含块、长源不含（长源一行不动）；
4. **对照臂**：`--prompt-profile baseline` 能剥掉块（A/B 用），且定位不到时**报错**不静默。
"""
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from prompts import section_budget as SB  # noqa: E402
from scripts import build_round as BR  # noqa: E402


def test_threshold_boundary():
    assert SB.in_short_source_mode(4999) is True
    assert SB.in_short_source_mode(5000) is True          # 含阈值
    assert SB.in_short_source_mode(5001) is False         # 超一点即长源，一行不动
    assert SB.in_short_source_mode(0) is False            # 未知源长不误伤
    assert SB.in_short_source_mode(None) is False
    assert SB.in_short_source_mode("abc") is False


def test_render_empty_for_long_source():
    assert SB.render_section_budget(5001) == ""
    assert SB.render_section_budget(0) == ""
    assert SB.render_section_budget(None) == ""


def test_render_block_content_for_short_source():
    block = SB.render_section_budget(3000, "structured")
    assert SB.BLOCK_MARKER in block
    assert "3000" in block
    # 黑名单小节确实被点名
    assert "延伸思考" in block and "自测三问" in block and "讲出来" in block
    assert "笔记者分析" in block and "小结闭环" in block
    # 保留项与被允许的扩展方式也在块里（防止模型顺着「去水分」删多了）
    assert "分层速览" in block
    for mode in ("重组", "条件与前提", "反例", "私房术语"):
        assert mode in block


def test_blacklist_only_names_sections_that_exist_in_that_template():
    """黑名单里的每一节，关键词必须能在该类型模板里找到——否则是对空气下禁令。"""
    from prompts.templates import NOTE_TEMPLATES
    for note_type in NOTE_TEMPLATES:
        text = SB._template_text(note_type)
        assert text, f"{note_type} 模板文本取不到"
        for name, kw in SB._BLACKLIST:
            if name in SB.blacklist_for(note_type):
                assert kw in text, f"{note_type} 的模板里没有「{name}」（关键词 {kw!r}）"


def test_get_note_prompt_injects_only_for_short_source():
    from prompts.templates import get_note_prompt
    short = get_note_prompt("structured", 3000)
    long_ = get_note_prompt("structured", 20000)
    assert SB.BLOCK_MARKER in short
    assert SB.BLOCK_MARKER not in long_
    assert len(short) > len(long_) or True  # 长度关系非判据，只看块的有无


def test_baseline_profile_strips_block():
    from prompts.templates import get_note_prompt
    prompt = get_note_prompt("structured", 3000)
    assert SB.BLOCK_MARKER in prompt
    stripped = BR.apply_prompt_profile(prompt, "baseline")
    assert SB.BLOCK_MARKER not in stripped
    assert len(stripped) < len(prompt)


def test_baseline_profile_raises_when_no_block():
    """长源 prompt 不含块 —— baseline 用错子集必须报错，不能静默返回原文。"""
    from prompts.templates import get_note_prompt
    long_ = get_note_prompt("structured", 20000)
    with pytest.raises(SystemExit):
        BR.apply_prompt_profile(long_, "baseline")


def test_current_profile_keeps_block():
    from prompts.templates import get_note_prompt
    prompt = get_note_prompt("structured", 3000)
    assert BR.apply_prompt_profile(prompt, "current") == prompt


def test_subset_short_picks_source_words_within_threshold():
    rows = [
        {"eval_id": "S1", "source_words": 500},      # 0-2k
        {"eval_id": "S2", "source_words": 4800},     # 2-5k
        {"eval_id": "S3", "source_words": 5000},     # 恰在阈值
        {"eval_id": "S4", "source_words": 5001},     # 超阈值 → 排除
        {"eval_id": "S5", "source_words": 0},        # 未知 → 排除
        {"eval_id": "S6", "source_words": 30000},    # 长源 → 排除
    ]
    got = {r["eval_id"] for r in BR.subset_rows(rows, "short")}
    assert got == {"S1", "S2", "S3"}
