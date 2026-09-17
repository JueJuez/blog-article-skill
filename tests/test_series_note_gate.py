"""系列课落盘机械门禁测试（DECISION-20260907）：_save_series_note 接入 verify_note_mechanical。

缺口（三层保障审计结论）：save_summary_only 串式管道有机械质量门禁，而系列课
单独实现的 _save_series_note 输入是纯模型产出，却无任何机械校验——模型产出失控
（写 H1 / 写来源链接行 / 篇幅崩塌）会直接落盘。本测试锁定：
- 门禁先于 formatter 与落盘执行，违规抛 ValueError("VERIFIER_FAILED: ...") 不落盘；
- 合规内容正常走完 formatter 与保存流程。
"""
import os
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import articles.main as articles_main
import videos.main as videos_main
from prompts.verifier import NOTE_WORD_LIMITS


def _body(n: int) -> str:
    """构造 n 字正文（单字重复，便于精确控制字数）。"""
    return "字" * n


# structured 区间 (1500, 3000)，1600 字落在区间内；含必备模块（分层速览/正反例对照/我的想法）
# 才满足「结构完整」，否则 2026-09-18 起结构缺失升为内容硬失败会拦盘。
VALID_STRUCT_CONTENT = (
    "速览：本集讲了三个核心要点。\n\n"
    "正反例：正面这么做拿到结果，反面那么做踩了坑。\n\n"
    "我的想法：可以迁移到自己项目里。\n\n"
    + _body(NOTE_WORD_LIMITS["structured"][0] + 100)
)


class _StubOutput:
    name = "stub"

    def ensure_folder_path(self, dirs):
        return "tok-folder"

    def ensure_series_node(self, title, parent_token=""):
        return "tok-series"

    def save(self, content, filename, parent_token=""):
        self.saved = (filename, content)
        return True

    def save_series(self, content, filename, series_title):
        self.saved = (filename, content)
        return True


class _StubManager:
    def __init__(self, obsidian=False):
        self.out = _StubOutput()

    def get_available_outputs(self):
        return [self.out]


class TestSeriesNoteMechanicalGate:
    """门禁接入 _save_series_note：违规拦截不落盘，合规正常保存。"""

    @pytest.fixture(autouse=True)
    def _stub_env(self, monkeypatch, tmp_path):
        self.formatter_calls = []
        self.manager_calls = []
        self.series_dir = str(tmp_path / "系列A")
        monkeypatch.setattr(videos_main, "_local_write_enabled", lambda: False)
        monkeypatch.setattr(videos_main, "format_note_with_prompt",
                            lambda *a, **k: self.formatter_calls.append(1) or "FORMATTED")
        monkeypatch.setattr(articles_main, "OutputManager",
                            lambda *a, **k: self.manager_calls.append(1) or _StubManager())

    def _save(self, content: str):
        return videos_main._save_series_note(
            content=content, series_dir=self.series_dir, base_name="第01集_测试",
            author="UP主", url="https://example.com/ep1", tags=["#测试"],
            note_type="structured", obsidian=False, folder="")

    def test_compliant_passes_and_saves(self):
        path = self._save(VALID_STRUCT_CONTENT)
        assert path == os.path.join(self.series_dir, "第01集_测试.md")
        assert self.formatter_calls == [1]  # 合规内容才走到 formatter
        assert self.manager_calls == [1]

    def test_h1_blocked_before_formatter(self):
        with pytest.raises(ValueError) as ei:
            self._save("# 一级标题\n\n" + VALID_STRUCT_CONTENT)
        assert str(ei.value).startswith("VERIFIER_FAILED")
        assert "一级标题" in str(ei.value)
        assert self.formatter_calls == []  # 门禁先于 formatter，违规不渲染
        assert self.manager_calls == []    # 违规不落盘

    def test_source_link_line_blocked(self):
        content = VALID_STRUCT_CONTENT + "\n\n**来源链接**：[原文](https://example.com/ep1)"
        with pytest.raises(ValueError) as ei:
            self._save(content)
        assert str(ei.value).startswith("VERIFIER_FAILED")
        assert "来源链接" in str(ei.value)
        assert self.manager_calls == []

    def test_below_content_floor_blocked(self):
        # content-first（2026-09-15）：字数只兜「几乎没写」——下限 300，与模板无关
        with pytest.raises(ValueError) as ei:
            self._save(_body(299))
        assert str(ei.value).startswith("VERIFIER_FAILED")
        assert "内容缺失" in str(ei.value)
        assert self.manager_calls == []

    def test_short_note_no_longer_blocked(self):
        """回归守卫：短稿不再被字数拦。

        旧规则 structured 硬下限 = 1500×0.6 = 900，400 字必拦——这正是逼子 Agent
        「为过门禁而注水/压碎」的来源，2026-09-15 已废除。
        """
        from prompts.verifier import verify_note_mechanical
        assert verify_note_mechanical(_body(400), "structured")["passed"] is True

    def test_source_url_in_body_blocked(self):
        content = "正文引用了 https://example.com/ep1 当论据。\n\n" + VALID_STRUCT_CONTENT
        with pytest.raises(ValueError) as ei:
            self._save(content)
        assert str(ei.value).startswith("VERIFIER_FAILED")
        assert "原文 URL" in str(ei.value)
        assert self.manager_calls == []
