"""test_land_scys_by_key.py — 覆盖 scys Obsidian 落盘脚本的核心逻辑。

重点验证「按 topicId 稳定键定位 temp + 出队」，这是 9-13 为修索引漂移坑引入的机制。
通过 monkeypatch 把 PENDING/TEMP 常量指向临时目录，并 mock save_summary_only /
resolve_folder，避免触碰真实队列与 Obsidian 落盘副作用。
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import scripts.land_scys_by_key as m
import articles.main as articles_main
import shared.routing as routing


def _write_queue(tmp_path, queue):
    p = tmp_path / "pending_summaries.json"
    p.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")
    return p


def test_extract_tags_and_strip():
    """标签行被剥离且去重；正文其余行保留。"""
    content = "#AI #出海 #出海\n正文第一行\n## 二、小节\n正文第二行\n"
    tags, stripped = m.extract_tags_and_strip(content)
    assert tags == ["AI", "出海"]
    assert "#AI" not in stripped
    assert "正文第一行" in stripped
    assert "## 二、小节" in stripped  # 非标签行的 markdown 标题保留


def test_topicid_stable_key_and_dequeue(tmp_path, monkeypatch):
    """两篇入队，topicId 稳定键定位 temp，落盘后全部出队；tags 含领域标签。"""
    temp = tmp_path / "_sum_temp"
    temp.mkdir()
    queue = [
        {"topicId": "T1", "url": "u1", "original_title": "t1",
         "project": "出海", "note_type": "case", "list_meta": {"gmtCreate": 123}},
        {"topicId": "T2", "url": "u2", "original_title": "t2",
         "project": "AI产品开发", "note_type": "structured", "list_meta": {}},
    ]
    pending = _write_queue(tmp_path, queue)
    (temp / "sum_T1.md").write_text("#AI #出海\n正文T1", encoding="utf-8")
    (temp / "sum_T2.md").write_text("无标签行\n正文T2", encoding="utf-8")

    monkeypatch.setattr(m, "PENDING", str(pending))
    monkeypatch.setattr(m, "TEMP", str(temp))

    calls = []
    def fake_save(d):
        calls.append(d)
        return {"success": True, "skipped": False, "filename": "x.md"}
    monkeypatch.setattr(articles_main, "save_summary_only", fake_save)
    monkeypatch.setattr(routing, "resolve_folder", lambda x: "生财有术/出海")

    m.main()

    # 两篇都落盘，且用的是 topicId 定位的 temp 内容
    assert len(calls) == 2
    bodies = {c["summarized_content"] for c in calls}
    assert any("正文T1" in b for b in bodies)
    assert any("正文T2" in b for b in bodies)
    # 落盘目标恒为 Obsidian
    assert all(c["obsidian"] is True for c in calls)
    # tags 含领域标签（生财有术 + project）
    assert any("生财有术" in c["tags"] and "出海" in c["tags"] for c in calls)
    # 出队后队列清空
    assert json.loads(pending.read_text(encoding="utf-8")) == []


def test_miss_when_temp_missing(tmp_path, monkeypatch):
    """temp 不存在的条目：不入队落盘、也不出队（保留在队列）。"""
    temp = tmp_path / "_sum_temp"
    temp.mkdir()
    queue = [
        {"topicId": "T3", "url": "u3", "original_title": "t3",
         "project": "出海", "note_type": "case", "list_meta": {}},
    ]
    pending = _write_queue(tmp_path, queue)
    # 故意不写 sum_T3.md

    monkeypatch.setattr(m, "PENDING", str(pending))
    monkeypatch.setattr(m, "TEMP", str(temp))
    saved = []
    monkeypatch.setattr(articles_main, "save_summary_only", lambda d: saved.append(d) or {"success": True, "filename": "x"})
    monkeypatch.setattr(routing, "resolve_folder", lambda x: "生财有术/出海")

    m.main()

    assert saved == []  # 没有落盘调用
    assert json.loads(pending.read_text(encoding="utf-8")) == queue  # 队列原样保留


def test_dequeue_by_url_fallback(tmp_path, monkeypatch):
    """条目无 topicId 时，回退用 url 作为 temp 键 + 出队定位。"""
    temp = tmp_path / "_sum_temp"
    temp.mkdir()
    queue = [
        {"url": "u9", "original_title": "t9", "project": "出海",
         "note_type": "case", "list_meta": {}},
    ]
    pending = _write_queue(tmp_path, queue)
    (temp / "sum_u9.md").write_text("正文U9", encoding="utf-8")

    monkeypatch.setattr(m, "PENDING", str(pending))
    monkeypatch.setattr(m, "TEMP", str(temp))
    calls = []
    monkeypatch.setattr(articles_main, "save_summary_only", lambda d: calls.append(d) or {"success": True, "filename": "x"})
    monkeypatch.setattr(routing, "resolve_folder", lambda x: "生财有术/出海")

    m.main()
    assert len(calls) == 1
    assert "正文U9" in calls[0]["summarized_content"]
    assert json.loads(pending.read_text(encoding="utf-8")) == []  # 出队成功
