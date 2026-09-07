"""monitors 门禁拦截台账（shared/gate_blockers）测试。"""

import json
import os
import time

import pytest

from shared import gate_blockers


@pytest.fixture()
def tmp_base(tmp_path, monkeypatch):
    base = str(tmp_path / "gate_blockers.jsonl")
    monkeypatch.setattr(gate_blockers, "GATE_BLOCKERS_BASE", base)
    return base


class TestLogGateBlock:
    def test_writes_valid_jsonl_line(self, tmp_base):
        p = gate_blockers.log_gate_block(
            source="queue", note_type="structured", url="https://a.b/c",
            title="测试", issues=["字数 412 低于硬下限 900"], warnings=["w1"],
        )
        assert p is not None and os.path.exists(p)
        with open(p, "r", encoding="utf-8") as f:
            rec = json.loads(f.readline())
        assert rec["source"] == "queue"
        assert rec["note_type"] == "structured"
        assert rec["url"] == "https://a.b/c"
        assert rec["title"] == "测试"
        assert rec["issues"] == ["字数 412 低于硬下限 900"]
        assert rec["warnings"] == ["w1"]
        assert "ts" in rec

    def test_daily_filename_contains_today(self, tmp_base):
        p = gate_blockers.log_gate_block(source="series", note_type="general",
                                         url="u", title="t", issues=["i"])
        today = time.strftime("%Y%m%d")
        assert f"gate_blockers.{today}.jsonl" == os.path.basename(p)

    def test_warnings_default_empty(self, tmp_base):
        p = gate_blockers.log_gate_block(source="series", note_type="general",
                                         url="u", title="t", issues=["i"])
        with open(p, "r", encoding="utf-8") as f:
            rec = json.loads(f.readline())
        assert rec["warnings"] == []

    def test_never_raises_on_log_failure(self, tmp_base, monkeypatch):
        def boom(*_a, **_k):
            raise OSError("disk full")
        monkeypatch.setattr(gate_blockers, "append_rolling", boom)
        assert gate_blockers.log_gate_block(source="queue", note_type="structured",
                                            url="u", title="t", issues=["i"]) is None

    def test_cleanup_expired_logs(self, tmp_base):
        """台账过期清理：同前缀旧日期文件被删。"""
        d = os.path.dirname(tmp_base)
        old = os.path.join(d, f"gate_blockers.20260101.jsonl")
        with open(old, "w", encoding="utf-8") as f:
            f.write("{}\n")
        old_ts = time.time() - 30 * 86400
        os.utime(old, (old_ts, old_ts))
        gate_blockers.log_gate_block(source="queue", note_type="structured",
                                     url="u", title="t", issues=["i"])
        assert not os.path.exists(old)


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
