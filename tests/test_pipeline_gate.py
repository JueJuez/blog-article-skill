# -*- coding: utf-8 -*-
"""管线门禁逻辑的兜底测试：`scripts/pipeline_gate.py`（单一真源）。

门禁是纯项目内机制，不依赖 git hook / 任何 git 配置：只要全量测试被跑
（项目纪律「改动后必跑全量测试」），本文件的 `test_gate_passes_current_tree`
就会拦截「未登记 CLI」与「新增完全重复函数」的漂移。

保护什么：
- 门禁本身在当前代码树必须通过（test_gate_passes_current_tree）——防止有人
  加「未登记 CLI」或「新增完全重复函数」时，门禁被手动绕过后无人察觉。
- 两类拦截判定各自可独立触发（monkeypatch 注入脏数据验证），不依赖真实仓库状态。
"""

from scripts import pipeline_gate


def test_gate_passes_current_tree():
    """正常场景：当前代码树应通过门禁（无未登记 CLI、无基线外完全重复）。"""
    problems = pipeline_gate.check()
    assert problems == [], "门禁不应拦截当前树，实际问题：%r" % problems


def _patched_dup_collect(monkeypatch, src, locs):
    """让某个源码在所有文件里重复出现，驱动门禁2。"""
    def fake_collect():
        return [(rel, "_phantom", i, 8, src) for i, rel in enumerate(locs)]
    monkeypatch.setattr("audit_duplicate_funcs.collect", fake_collect)


def test_unregistered_cli_detected(monkeypatch):
    """异常场景：出现文档未登记的 CLI 入口 → 门禁1 拦截。"""
    def fake_collect():
        cli = [("scripts/ghost_pipeline.py", True, "未登记的幻影入口")]
        return cli, {}
    monkeypatch.setattr("audit_pipeline_coverage.collect", fake_collect)
    problems = pipeline_gate.check()
    joined = "\n".join(problems)
    assert "门禁1" in joined and "ghost_pipeline.py" in joined


def test_new_exact_duplicate_detected(monkeypatch):
    """异常场景：两个 live 文件出现完全相同函数（基线外）→ 门禁2 拦截。"""
    _patched_dup_collect(
        monkeypatch,
        src="def _phantom_work():\n    return 1\n" * 4,
        locs=["scripts/live_a.py", "scripts/live_b.py"],
    )
    problems = pipeline_gate.check()
    joined = "\n".join(problems)
    assert "门禁2" in joined and "live_a.py" in joined and "live_b.py" in joined


def test_baseline_duplication_not_blocked(monkeypatch):
    """边界场景：文件组命中 ALLOWED_DUP 基线的存量重复不触发门禁2。"""
    baseline_locs = sorted(next(iter(pipeline_gate.ALLOWED_DUP)))
    src = "def _frozen_lark():\n    subprocess.run(['lark-cli', '--yes'])\n" * 3
    _patched_dup_collect(monkeypatch, src=src, locs=baseline_locs)
    problems = "\n".join(pipeline_gate.check())
    assert "门禁2" not in problems