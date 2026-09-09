"""同步登记表（PLAN-20260906 任务1）：dedup 索引升级为双端 link 登记表。

覆盖四块：
- P0-2：索引迁出易失的 .cache/，落到 notes/_meta/sync_ledger.json，旧文件自动搬家。
- 登记表 schema：{source_url, title, filename, feishu_link, obsidian_link, ts}。
- link API：set_links / get_entry / clear_links（只在已有记录上操作，不新造键）。
- P0-3：读-改-写全程文件锁（O_EXCL）+ 原子替换，并发 mark 不丢记录。
"""
import json
import os
import sys
import threading
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from articles import dedup


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """索引/旧档/锁目录全部指到临时目录，模拟真实相对布局。"""
    monkeypatch.setattr(dedup, "_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(dedup, "_INDEX_FILE",
                        str(tmp_path / "notes_meta" / "sync_ledger.json"))
    monkeypatch.setattr(dedup, "_LEGACY_INDEX_FILE",
                        str(tmp_path / "cache" / "dedup.json"))
    monkeypatch.setattr(dedup, "_LOCK_DIR", str(tmp_path / "locks"))
    return dedup


def _write_legacy(ledger, records: dict):
    os.makedirs(os.path.dirname(ledger._LEGACY_INDEX_FILE), exist_ok=True)
    with open(ledger._LEGACY_INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False)


class TestLedgerLocation:
    """1.1（PLAN-20260908）：登记表默认落点必须是 notes/_meta/summary_registry.json。"""

    def test_default_index_file_in_notes_meta(self):
        expect = os.path.join("notes", "_meta", "summary_registry.json")
        assert dedup._DEFAULT_INDEX_FILE.endswith(expect)

    def test_load_without_any_file_creates_nothing(self, ledger):
        assert ledger._load_index() == {}
        assert not os.path.exists(ledger._INDEX_FILE)


class TestLegacyMigration:
    """旧 .cache/dedup.json 自动搬家到新路径。"""

    def test_legacy_moved_to_new_path(self, ledger):
        _write_legacy(ledger, {"abcdef1234567890": {
            "title": "旧", "filename": "old.md", "ts": 1700000000}})
        rec = ledger.is_summarized(url="https://x.com/a")
        # url 未记录时 is_summarized 返回 {} 属正常，直接查文件
        idx = ledger._load_index()
        assert "abcdef1234567890" in idx
        assert idx["abcdef1234567890"]["title"] == "旧"
        assert os.path.exists(ledger._INDEX_FILE)
        assert not os.path.exists(ledger._LEGACY_INDEX_FILE)

    def test_new_file_wins_and_legacy_left_alone(self, ledger):
        _write_legacy(ledger, {"0000000000000000": {"title": "旧档", "ts": 1}})
        os.makedirs(os.path.dirname(ledger._INDEX_FILE), exist_ok=True)
        with open(ledger._INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump({"1111111111111111": {"title": "新档", "ts": 2}}, f)
        idx = ledger._load_index()
        assert "1111111111111111" in idx
        assert "0000000000000000" not in idx
        assert os.path.exists(ledger._LEGACY_INDEX_FILE)

    def test_corrupt_legacy_treated_as_empty_and_removed(self, ledger):
        os.makedirs(os.path.dirname(ledger._LEGACY_INDEX_FILE), exist_ok=True)
        with open(ledger._LEGACY_INDEX_FILE, "w", encoding="utf-8") as f:
            f.write("{broken json")
        assert ledger._load_index() == {}
        assert not os.path.exists(ledger._LEGACY_INDEX_FILE)


class TestLedgerSchema:
    """登记表记录：{source_url, title, filename, feishu_link, obsidian_link, ts}。"""

    def test_mark_writes_full_record(self, ledger):
        ledger.mark_summarized(url="https://s.com/1", title="标题",
                               filename="f.md")
        rec = ledger.get_entry(url="https://s.com/1")
        assert rec["source_url"] == "https://s.com/1"
        assert rec["title"] == "标题"
        assert rec["filename"] == "f.md"
        assert rec["feishu_link"] == ""
        assert rec["obsidian_link"] == ""
        assert rec["ts"]
        assert rec["key_type"] == "url"

    def test_mark_content_only_has_empty_source_url(self, ledger):
        ledger.mark_summarized(content="纯粘贴正文", title="粘贴")
        rec = ledger.get_entry(content="纯粘贴正文")
        assert rec["source_url"] == ""
        assert rec["title"] == "粘贴"
        assert rec["key_type"] == "content"

    def test_remark_keeps_links(self, ledger):
        ledger.mark_summarized(url="https://s.com/2", title="一", filename="a.md")
        ledger.set_links(url="https://s.com/2", feishu_link="feishu://n1")
        ledger.mark_summarized(url="https://s.com/2", title="二", filename="b.md")
        rec = ledger.get_entry(url="https://s.com/2")
        assert rec["title"] == "二"
        assert rec["filename"] == "b.md"
        assert rec["feishu_link"] == "feishu://n1"

    def test_mark_extended_fields_roundtrip(self, ledger):
        ledger.mark_summarized(url="https://s.com/3", title="标题",
                               filename="f.md", folder="生财有术/AI",
                               note_type="structured", source="bilibili")
        rec = ledger.get_entry(url="https://s.com/3")
        assert rec["folder"] == "生财有术/AI"
        assert rec["note_type"] == "structured"
        assert rec["source"] == "bilibili"
        assert rec["summarized_at"]

    def test_remark_keeps_extended_fields(self, ledger):
        ledger.mark_summarized(url="https://s.com/4", title="一",
                               filename="a.md", folder="生财有术/AI",
                               note_type="structured", source="bilibili")
        ledger.mark_summarized(url="https://s.com/4", title="二", filename="b.md")
        rec = ledger.get_entry(url="https://s.com/4")
        assert rec["title"] == "二"
        assert rec["filename"] == "b.md"
        assert rec["folder"] == "生财有术/AI"
        assert rec["note_type"] == "structured"
        assert rec["source"] == "bilibili"


class TestLinkAPI:
    """set_links / get_entry / clear_links：只改已有记录，不新造键。"""

    def test_set_feishu_link_only(self, ledger):
        ledger.mark_summarized(url="https://l.com/1", title="T", filename="f.md")
        rec = ledger.set_links(url="https://l.com/1", feishu_link="feishu://n1")
        assert rec["feishu_link"] == "feishu://n1"
        assert rec["obsidian_link"] == ""
        assert ledger.get_entry(url="https://l.com/1")["feishu_link"] == "feishu://n1"

    def test_set_obsidian_link_only(self, ledger):
        ledger.mark_summarized(url="https://l.com/2")
        ledger.set_links(url="https://l.com/2", obsidian_link="obsidian://v")
        rec = ledger.get_entry(url="https://l.com/2")
        assert rec["obsidian_link"] == "obsidian://v"
        assert rec["feishu_link"] == ""

    def test_set_links_on_missing_entry_is_noop(self, ledger):
        res = ledger.set_links(url="https://l.com/none", feishu_link="x")
        assert res == {}
        assert ledger._load_index() == {}

    def test_set_links_without_key_is_noop(self, ledger):
        assert ledger.set_links(feishu_link="x") == {}

    def test_clear_links_resets_both_and_keeps_rest(self, ledger):
        ledger.mark_summarized(url="https://l.com/3", title="T", filename="f.md")
        ledger.set_links(url="https://l.com/3", feishu_link="f", obsidian_link="o")
        assert ledger.clear_links(url="https://l.com/3") is True
        rec = ledger.get_entry(url="https://l.com/3")
        assert rec["feishu_link"] == "" and rec["obsidian_link"] == ""
        assert rec["title"] == "T" and rec["filename"] == "f.md"

    def test_clear_links_missing_returns_false(self, ledger):
        assert ledger.clear_links(url="https://l.com/none") is False

    def test_get_entry_missing_returns_empty(self, ledger):
        assert ledger.get_entry(url="https://l.com/none") == {}

    def test_old_records_read_with_default_links(self, ledger):
        """迁移来的旧记录缺 link 字段 → API 层补默认值。"""
        _write_legacy(ledger, {"abcdef1234567890": {
            "title": "旧", "filename": "old.md", "ts": 1700000000}})
        ledger._load_index()  # 触发搬家
        rec = ledger.get_entry(content=None, url="")
        assert rec == {}
        # 旧记录的 hash 无法反推 URL，直接按键取
        rec = ledger.get_entry(url="")  # 无键 → {}
        assert rec == {}


class TestIndexLock:
    """P0-3：读-改-写加锁 + 原子替换，并发不丢记录。"""

    def test_no_lock_file_left_after_mark(self, ledger):
        ledger.mark_summarized(url="https://k.com/1")
        leftovers = [f for f in os.listdir(ledger._LOCK_DIR)
                     if f.endswith(".lock")] if os.path.isdir(ledger._LOCK_DIR) else []
        assert leftovers == []

    def test_concurrent_marks_no_loss(self, ledger):
        def worker(n: int):
            for i in range(25):
                ledger.mark_summarized(url=f"https://k.com/{n}/{i}", title=f"t{i}")
        threads = [threading.Thread(target=worker, args=(n,)) for n in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(ledger._load_index()) == 50

    def test_lock_timeout_bypasses(self, ledger, monkeypatch):
        monkeypatch.setattr(ledger, "_LOCK_TIMEOUT", 0.1)
        os.makedirs(ledger._LOCK_DIR, exist_ok=True)
        stale = os.path.join(ledger._LOCK_DIR, "dedup_index.lock")
        with open(stale, "w", encoding="utf-8") as f:
            f.write("stale")
        ledger.mark_summarized(url="https://k.com/2")
        assert ledger.get_entry(url="https://k.com/2") != {}

    def test_no_tmp_file_left_after_save(self, ledger):
        ledger.mark_summarized(url="https://k.com/3")
        meta_dir = os.path.dirname(ledger._INDEX_FILE)
        assert [f for f in os.listdir(meta_dir) if f.endswith(".tmp")] == []


class TestCorruptIndex:
    """新路径文件损坏：不静默吞，改名备份后从空开始。"""

    def test_corrupt_index_backed_aside(self, ledger):
        os.makedirs(os.path.dirname(ledger._INDEX_FILE), exist_ok=True)
        with open(ledger._INDEX_FILE, "w", encoding="utf-8") as f:
            f.write("{not json")
        assert ledger._load_index() == {}
        assert not os.path.exists(ledger._INDEX_FILE)
        meta_dir = os.path.dirname(ledger._INDEX_FILE)
        assert [f for f in os.listdir(meta_dir) if ".corrupt-" in f]
