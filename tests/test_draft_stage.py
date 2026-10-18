"""T7 稿件不丢：暂存区与「拦下不崩」回归。

2026-10-18 的真实事故：`videos/main.py` 直接调 `stage_blocked(...)`，但这个名字既没导入
也没定义 —— 门禁拦下时不是「稿不进库」，而是 `NameError` 先把流程炸掉。所以除了暂存语义，
本文件专门钉住「拦下不崩」这一条。
"""
import ast
import json
import os

import pytest

from shared import draft_stage as DS


@pytest.fixture(autouse=True)
def _isolate_stage(monkeypatch, tmp_path):
    monkeypatch.setattr(DS, "STAGE_BASE", str(tmp_path / "staged_drafts"))


def test_stage_blocked_writes_draft_and_meta(tmp_path):
    res = DS.stage_blocked(
        "候选稿正文", reason="content_block", route="t", title="标题A",
        source_text="源正文", issues=["结构缺失"], warnings=["w"],
    )
    assert res["ok"] is True
    assert os.path.exists(res["draft"])
    meta = json.load(open(res["meta"], encoding="utf-8"))
    assert meta["reason"] == "content_block"
    assert meta["route"] == "t"
    assert meta["issues"] == ["结构缺失"]
    assert meta["note_sha256"] == res["note_sha256"]
    assert meta["source_sha256"] == DS.digest("源正文")
    assert open(res["draft"], encoding="utf-8").read() == "候选稿正文"


def test_stage_blocked_never_raises(monkeypatch, tmp_path):
    """暂存是保险，写不进去也只能告警：主流程必须照原样返回失败原因。"""
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    res = DS.stage_blocked("稿", reason="x", stage_dir=str(blocker / "sub"))
    assert res["ok"] is False
    assert "error" in res


def test_videos_stage_blocked_is_the_shared_one():
    """回归：videos/main.py 的 4 个拦截点调用 stage_blocked，它必须真的存在。"""
    import videos.main as vm
    assert getattr(vm, "stage_blocked", None) is DS.stage_blocked


def test_videos_gate_paths_reference_defined_names():
    """静态兜底：videos/main.py 里用到的 stage_blocked 既已导入，拦截点就不会 NameError。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "videos", "main.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    imported = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                for a in n.names}
    assert "stage_blocked" in imported


def test_resum_stage_failure_helper_stages(tmp_path, monkeypatch):
    """重做管线：被拦的候选稿也要留档（否则「新的比旧的差」永远无从比较）。"""
    sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from scripts import resum_save_batch as R

    R._stage_failure({"title": "旧标题", "url": "u", "note_type": "case",
                      "generation_context": {"prompt_sha256": "p"}},
                     "重做稿", "源", "content_block", ["锚点召回不足"])
    staged = DS.list_staged()
    assert len(staged) == 1
    meta = DS.read_meta(staged[0])
    assert meta["route"] == "resum_save_batch"
    assert meta["prompt_sha256"] == "p"
    assert meta["issues"] == ["锚点召回不足"]


def test_annotate_appends_decision_to_meta():
    """T8：决策（保留旧稿/替换）要回写到暂存 meta，和稿件放在一起。"""
    res = DS.stage_blocked("候选稿", reason="redo_candidate", route="t", title="标题B", quiet=True)
    assert DS.annotate(res["meta"], {"outcome": "kept_old", "replace_decision": {"reason": "no_gain"}})
    meta = DS.read_meta(res["draft"])
    assert meta["reason"] == "redo_candidate", "原有字段不得被覆盖丢失"
    assert meta["outcome"] == "kept_old"
    assert meta["replace_decision"]["reason"] == "no_gain"


def test_annotate_never_raises():
    """补记失败只返回 False：决策回写是保险，不许影响主流程。"""
    assert DS.annotate("", {"x": 1}) is False
    assert DS.annotate("Z:/definitely/missing/meta.json", {"x": 1}) is False
