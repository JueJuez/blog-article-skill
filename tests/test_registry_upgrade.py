"""1.1 登记表升格：sync_ledger.json → summary_registry.json（PLAN-20260908）。

搬家链三代：.cache/dedup.json（P0 前）→ notes/_meta/sync_ledger.json（P0-2）
→ notes/_meta/summary_registry.json（本任务）。新档已存在则所有旧档保留不动。
索引键为纯 hash（_key_for 返回 (prefix, hash)，存储时只用 hash 作键）。
"""
import json
import os
import time

from articles import dedup


def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _entries(*urls: str) -> dict:
    index = {}
    for u in urls:
        index[dedup._hash(dedup._normalize_url(u))] = {
            "source_url": u,
            "title": "t",
            "filename": "f.md",
            "feishu_link": "",
            "obsidian_link": "",
            "ts": 0,
        }
    return index


def test_default_registry_path(monkeypatch):
    """默认落点必须是 notes/_meta/summary_registry.json。"""
    monkeypatch.undo()  # 绕开 conftest 隔离，读模块真值
    expect = os.path.join("notes", "_meta", "summary_registry.json")
    assert dedup._DEFAULT_INDEX_FILE.endswith(expect)


def test_migrate_sync_ledger_to_registry():
    """一代搬家：旧 sync_ledger（P0-2 真源）→ 新 registry，源档删除。"""
    _write_json(dedup._LEGACY_INDEX_FILE, _entries("https://example.com/a"))
    assert not os.path.exists(dedup._INDEX_FILE)
    rec = dedup.is_summarized(url="https://example.com/a")
    assert rec and rec["source_url"] == "https://example.com/a"
    assert os.path.exists(dedup._INDEX_FILE)
    assert not os.path.exists(dedup._LEGACY_INDEX_FILE)


def test_migrate_cache_dedup_jump_generation():
    """跳代搬家：sync_ledger 不存在但最老档 .cache/dedup.json 在 → 仍要搬。"""
    _write_json(dedup._LEGACY_CACHE_FILE, _entries("https://example.com/b"))
    rec = dedup.is_summarized(url="https://example.com/b")
    assert rec and rec["source_url"] == "https://example.com/b"
    assert os.path.exists(dedup._INDEX_FILE)
    assert not os.path.exists(dedup._LEGACY_CACHE_FILE)
    assert not os.path.exists(dedup._LEGACY_INDEX_FILE)


def test_registry_wins_when_both_exist():
    """共存：registry 已在 → 旧档保留不动，查询只走 registry。"""
    _write_json(dedup._INDEX_FILE, _entries("https://example.com/new"))
    _write_json(dedup._LEGACY_INDEX_FILE, _entries("https://example.com/old"))
    assert dedup.is_summarized(url="https://example.com/new")
    assert not dedup.is_summarized(url="https://example.com/old")
    assert os.path.exists(dedup._LEGACY_INDEX_FILE)


def test_corrupt_registry_backed_up():
    """损坏 registry：改名 .corrupt-<ts> 备份后从空开始，不静默吞。"""
    _write_json(dedup._INDEX_FILE, _entries("https://example.com/x"))
    with open(dedup._INDEX_FILE, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert dedup.is_summarized(url="https://example.com/x") == {}
    meta_dir = os.path.dirname(dedup._INDEX_FILE)
    assert any(n.startswith("summary_registry.json.corrupt-")
               for n in os.listdir(meta_dir))


# ---------------- 1.2 register API（PLAN-20260908 阶段1.2） ----------------

def test_register_writes_extended_schema():
    """register 薄封装：落盘 key_type/folder/note_type/source/summarized_at 扩展字段。"""
    rec = dedup.register(url="https://example.com/r1", title="标题R",
                         folder="【我的总结】/作者/x", note_type="structured",
                         source="video")
    assert rec["key_type"] == "url"
    assert rec["folder"] == "【我的总结】/作者/x"
    assert rec["note_type"] == "structured"
    assert rec["source"] == "video"
    assert rec["summarized_at"] == time.strftime("%Y-%m-%d")
    hit = dedup.is_summarized(url="https://example.com/r1")
    assert hit and hit["source"] == "video"


def test_register_missing_params_returns_empty():
    """缺参：url 与 title 都空 → 返回 {} 且不建键。"""
    assert dedup.register() == {}
    assert dedup._load_index() == {}


def test_register_repeat_is_idempotent():
    """重复登记幂等：非空字段覆盖、未传字段保留、link 永不清空、不重复建键。"""
    dedup.register(url="https://example.com/r2", title="A", folder="F1",
                   note_type="structured", source="article")
    dedup.set_links(url="https://example.com/r2", feishu_link="fl")
    rec = dedup.register(url="https://example.com/r2", title="A2",
                         note_type="key_points")
    assert rec["title"] == "A2"
    assert rec["folder"] == "F1"
    assert rec["source"] == "article"
    assert rec["note_type"] == "key_points"
    assert rec["feishu_link"] == "fl"
    keys = [k for k, v in dedup._load_index().items()
            if v.get("source_url", "").endswith("r2")]
    assert len(keys) == 1


def test_register_without_url_uses_title_fp():
    """无 URL 笔记（如直播回放）：register 走 title_fp 指纹键。"""
    rec = dedup.register(title="第10期直播回放", folder="【监控】", source="video")
    assert rec["key_type"] == "title_fp"
    assert rec["source_url"] == ""
    assert dedup.is_summarized(title="第10期直播回放")


# ---------------- 1.3 title_fp 键读侧（PLAN-20260908 阶段1.3） ----------------

def test_is_summarized_by_title_fingerprint():
    """is_summarized 接受标题：指纹归一化（标点/空白）后命中；不同标题未命中。"""
    dedup.register(title="第10期直播回放", source="video")
    assert dedup.is_summarized(title="第10期 直播回放！")
    assert not dedup.is_summarized(title="第11期直播回放")


def test_batch_is_summarized_with_titles():
    """batch_is_summarized 支持 titles 批量指纹查询，返回命中原文集合。"""
    dedup.register(title="A课第1集", source="series")
    hits = dedup.batch_is_summarized([], titles=["A课第1集", "B课第2集"])
    assert hits == {"A课第1集"}
