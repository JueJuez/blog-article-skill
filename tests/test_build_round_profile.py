"""对照轮工具的 prompt profile 护栏（A4/A6 正式轮归因隔离）。

保护需求（PLAN-20261018 §8 归因纪律）：A4 与 A6 都改生成端，**不得合进同一对照出
「84.2%→X%」**。A4 正式轮靠 `--prompt-profile a4only` 按 A4 落地态回退 A6 的两段模板散文来隔离
A4 主效应。这里守三件事：

1. 两段 A6 散文都能在**生产 prompt 里定位到**（模板改了却没同步 profile → 隔离静默失效）；
2. `a4only` 确实把 A6 的痕迹抹掉、且**没有**误伤 A4 自己的两阶段清单指令；
3. `current` 原样返回（A6 轮必须拿到生产态）。
"""
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scripts import build_round as BR  # noqa: E402


def _prompt(source_chars: int = 15000) -> str:
    from prompts.templates import QUALITY_GATE_SELFCHECK, get_note_prompt
    return get_note_prompt("general", source_chars) + QUALITY_GATE_SELFCHECK


def test_a6_prose_markers_present_in_production_prompt():
    """模板若改了 A6 那两段措辞，profile 就会定位不到 —— 必须在此暴露。"""
    prompt = _prompt()
    for a6, _old in BR._A6_PROSE:
        assert a6 in prompt, f"A6 段未出现在生产 prompt 里（模板已变）：{a6[:40]!r}"


def test_a4only_strips_a6_prose_and_keeps_a4_instructions():
    prompt = _prompt(15000)
    a4 = BR.apply_prompt_profile(prompt, "a4only")
    assert "散落信息点" not in a4                      # A6 模板条款已回退
    assert "判定范围按上下文绑定" not in a4
    assert "长源提取清单" in a4                        # A4 的两阶段指令保留
    assert len(a4) < len(prompt)


def test_current_profile_is_identity():
    prompt = _prompt()
    assert BR.apply_prompt_profile(prompt, "current") == prompt


def test_a4only_raises_when_template_drifts():
    """定位不到 A6 段必须报错——静默返回原文等于这一轮没隔离。"""
    with pytest.raises(SystemExit):
        BR.apply_prompt_profile("这是一段不含 A6 条款的假 prompt", "a4only")


def test_subset_lowretention_is_opinion_structured_plus_short_control():
    rows = [
        {"eval_id": "X1", "note_type": "opinion", "source_words": 9000},
        {"eval_id": "X2", "note_type": "structured", "source_words": 12000},
        {"eval_id": "X3", "note_type": "general", "source_words": 1000},   # 短源对照
        {"eval_id": "X4", "note_type": "general", "source_words": 1500},   # 短源对照
        {"eval_id": "X5", "note_type": "general", "source_words": 8000},   # 既不低保留也不短
    ]
    got = {r["eval_id"] for r in BR.subset_rows(rows, "lowretention")}
    assert got == {"X1", "X2", "X3", "X4"}
