"""去 H1（DECISION-20260907）：总结正文不提取/兜底一级标题当标题。

现行落盘唯一入口为 articles.main.save_summary_only（2026-09-09 收口，
articles/_save_summary.py 是 CLI 薄委托）；质量门禁 prompts.verifier.
verify_note_mechanical 拦截正文一级标题。旧 monitors/drain_pending.py →
TAGMAP 链路已随 2026-09-11 文档清理归档至 _archive/code/，本测试守护
现行入口不回退引入 H1 兜底。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import articles.main as am
from articles import _save_summary as cli
from prompts import verifier


class TestCurrentEntriesNoH1:
    """现行落盘入口无 H1 提取依赖（防 H1 兜底再次引入）。"""

    def test_main_has_no_extract_h1(self):
        assert not hasattr(am, "_extract_h1")

    def test_save_summary_cli_has_no_extract_h1(self):
        assert not hasattr(cli, "_extract_h1")

    def test_source_has_no_h1_fallback_call(self):
        src = Path(am.__file__).read_text(encoding="utf-8")
        assert "_extract_h1" not in src
        assert "choose_node_title(title, _extract_h1" not in src


class TestVerifierStillBlocksH1:
    """机械门禁必须仍拦截正文一级标题。"""

    def test_h1_re_and_gate_exist(self):
        assert hasattr(verifier, "_H1_RE")
        assert hasattr(verifier, "verify_note_mechanical")

    def test_h1_in_body_is_blocked(self):
        result = verifier.verify_note_mechanical(
            "# 意外的一级标题\n\n正文段落，用于触发 H1 拦截校验。"
        )
        assert result["passed"] is False
        assert any("一级标题" in i for i in result["issues"])

    def test_h1_inside_code_fence_not_blocked(self):
        result = verifier.verify_note_mechanical(
            "```\n# 这只是代码注释\n```\n\n正文段落，不含真正的一级标题。"
        )
        assert not any("一级标题" in i for i in result["issues"])
