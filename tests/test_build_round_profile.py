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


def test_noblocks_identity_when_block_present_and_raises_when_absent(monkeypatch):
    """`noblocks` 是恒等变换，但**必须校验条款真在 prompt 里**（否则这一轮等于白跑）。"""
    from prompts.section_budget import GRANULARITY_ENV

    monkeypatch.setenv(GRANULARITY_ENV, "1")
    prompt = _prompt(3000)
    assert BR.apply_prompt_profile(prompt, "noblocks") == prompt

    monkeypatch.delenv(GRANULARITY_ENV, raising=False)
    without = _prompt(3000)          # 关掉 env 后生产 prompt 不含该块
    with pytest.raises(SystemExit):
        BR.apply_prompt_profile(without, "noblocks")


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


# ---- vizpurpose（PLAN-20261026 §6 按用途选图表 A/B 臂）----

def _all_production_prompts() -> dict:
    """渲染全部笔记类型的生产 prompt（vizpurpose 的目标条款散在多个模板里）。"""
    from prompts.templates import QUALITY_GATE_SELFCHECK, get_note_prompt, list_note_types
    return {t["key"]: get_note_prompt(t["key"], 15000) + QUALITY_GATE_SELFCHECK
            for t in list_note_types()}


def test_vizpurpose_targets_present_in_production_templates():
    """每对 vizpurpose 改动都必须至少在一个生产模板里定位到（模板改了没同步 → 在此暴露）。"""
    prompts = _all_production_prompts()
    joined = "\n".join(prompts.values())
    for old, _new in BR._VIZ_EDITS:
        assert old in joined, f"vizpurpose 目标条款不在任何生产模板里（模板已变）：{old[:40]!r}"


def test_vizpurpose_only_diffs_target_blocks():
    """A/B 纪律：试验臂 == 生产 prompt 对目标条款的精确替换，其余逐字节不变。"""
    prompts = _all_production_prompts()
    changed_any = False
    for _key, prompt in prompts.items():
        out = BR.apply_prompt_profile(prompt, "vizpurpose")
        expected = prompt
        hits = 0
        for old, new in BR._VIZ_EDITS:
            n = expected.count(old)
            hits += n
            expected = expected.replace(old, new)
        if hits:
            changed_any = True
            # 同一条款在一篇 prompt 内重复出现也必须全部替换（不留半新半旧）
            for old, _new in BR._VIZ_EDITS:
                assert old not in out
        assert out == expected, "vizpurpose 改动了目标条款之外的内容"
    assert changed_any, "vizpurpose 未改动任何文本，profile 失效"


def test_vizpurpose_removes_mandatory_clauses_keeps_mermaid():
    """「必须用表格」与 chart 确定性触发被替换；Mermaid 按需条款保持原样。"""
    prompts = _all_production_prompts()
    for _key, prompt in prompts.items():
        out = BR.apply_prompt_profile(prompt, "vizpurpose")
        assert "必须用表格" not in out
        assert "≥3 组可对比的数字**（增长对比/价格对比/占比/趋势序列）时，产出" not in out
        assert "按表达用途选形式" in out or out == prompt
        # Mermaid 条款不动
        if "禁止为线性罗列强画图" in prompt:
            assert "禁止为线性罗列强画图" in out


def test_vizpurpose_raises_when_template_drifts():
    with pytest.raises(SystemExit):
        BR.apply_prompt_profile("这是一段不含图表条款的假 prompt", "vizpurpose")
