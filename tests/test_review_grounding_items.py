# -*- coding: utf-8 -*-
"""P2：复核清单固定两项（机制链完整性 / 数字归属）。"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from prompts.templates import QUALITY_GATE_PROMPT  # noqa: E402


def test_quality_gate_has_mechanism_and_attribution_items():
    assert "机制链完整性" in QUALITY_GATE_PROMPT
    assert "数字归属" in QUALITY_GATE_PROMPT
    assert "只给端点" in QUALITY_GATE_PROMPT
    assert "挂错人按 P1" in QUALITY_GATE_PROMPT


def test_build_round_check_instruction_has_items():
    text = (BASE_DIR / "scripts" / "build_round.py").read_text(encoding="utf-8")
    assert "mechanism=ok|missing" in text
    assert "attribution=ok|wrong" in text
