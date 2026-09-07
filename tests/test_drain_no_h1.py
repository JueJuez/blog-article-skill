"""去 H1（DECISION-20260907）：monitors/drain_pending.py 不再从总结正文提取 H1 当标题兜底。

新格式正文不含一级标题/标签行，node_title 纯由来源侧 title（derive_title_from_body
确定性提炼）决定；choose_node_title(title) 单参调用，summary_h1 兜底废止。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from monitors import drain_pending


class TestDrainPendingNoH1:
    """drain_pending 已无 H1 提取依赖（防 H1 兜底再次引入）。"""

    def test_extract_h1_removed(self):
        assert not hasattr(drain_pending, "_extract_h1")

    def test_source_has_no_h1_fallback_call(self):
        # choose_node_title 调用必须是单参数（不再传 _extract_h1 结果）
        src = Path(drain_pending.__file__).read_text(encoding="utf-8")
        assert "choose_node_title(title)" in src
        assert "choose_node_title(title, _extract_h1" not in src
