"""回归测试：fetch_up_range 抓到字幕即入 pending_summaries 队列（scys_batch_fetch 同款闭环）。

保护的需求（2026-09-03 收编决策）：
- 入队条目 schema 与 monitors._queue_pending_summary 对齐，且带预计算 prompt/folder；
- URL 已在队列 / 已总结过（dedup 闸门）→ 跳过，不重复入队；
- MON_PENDING_SUMMARY_PATH 可覆盖队列路径（与并行模式约定一致）。
"""
import json

import pytest

import scripts.fetch_up_range as fur


@pytest.fixture
def queue_env(monkeypatch, tmp_path):
    qpath = tmp_path / "pending_summaries.json"
    monkeypatch.setenv("MON_PENDING_SUMMARY_PATH", str(qpath))
    return qpath


def test_enqueue_new_item(queue_env):
    status = fur.enqueue_pending(
        "https://www.bilibili.com/video/BV1abc", "测试视频标题", "测试UP",
        1700000000, __file__)
    assert status == "queued"
    entries = json.load(open(queue_env, encoding="utf-8"))
    assert len(entries) == 1
    e = entries[0]
    assert e["url"].endswith("BV1abc")
    assert e["author"] == "测试UP"
    assert isinstance(e["note_type"], str) and e["note_type"]  # 分类器产物
    assert len(e["prompt"]) > 200  # 预计算 prompt 已带上（含质量自检段）
    assert e["folder"]  # 路由器预计算，非空（至少落兜底收件箱）
    assert e["raw_file"] == __file__


def test_enqueue_dedup_by_url(queue_env):
    for _ in range(2):
        status = fur.enqueue_pending(
            "https://www.bilibili.com/video/BV1same", "同一条", "测试UP",
            1700000000, __file__)
    assert status == "in-queue"
    entries = json.load(open(queue_env, encoding="utf-8"))
    assert len([e for e in entries if e["url"].endswith("BV1same")]) == 1


def test_enqueue_skip_if_summarized(queue_env, monkeypatch):
    from articles import dedup
    monkeypatch.setattr(dedup, "is_summarized", lambda **kw: {"filename": "x.md"})
    status = fur.enqueue_pending(
        "https://www.bilibili.com/video/BV1done", "已总结过", "测试UP",
        1700000000, __file__)
    assert status == "summarized"
    assert not queue_env.exists() or json.load(open(queue_env, encoding="utf-8")) == []
