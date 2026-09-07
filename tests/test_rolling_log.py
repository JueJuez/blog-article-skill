"""shared/rolling_log 按天滚动日志测试：路径规则、写入、惰性过期清理。"""

import json
import os
import time
from datetime import datetime

import pytest

from shared import rolling_log


class TestRollingLogPath:
    def test_inserts_date_before_extension(self):
        p = rolling_log.rolling_log_path("x/foo.log", ts=datetime(2026, 9, 7))
        assert p.endswith("foo.20260907.log")

    def test_jsonl_extension(self):
        p = rolling_log.rolling_log_path("x/gate_blockers.jsonl", ts=datetime(2026, 9, 7))
        assert p.endswith("gate_blockers.20260907.jsonl")

    def test_no_extension(self):
        p = rolling_log.rolling_log_path("x/foo", ts=datetime(2026, 9, 7))
        assert p.endswith("foo.20260907")

    def test_keeps_directory(self):
        p = rolling_log.rolling_log_path("/tmp/dir/foo.log", ts=datetime(2026, 9, 7))
        assert p.startswith(os.path.join("/tmp", "dir")) or p.startswith("/tmp/dir")


class TestAppendRolling:
    def test_writes_today_file_and_returns_path(self, tmp_path):
        base = str(tmp_path / "foo.log")
        p = rolling_log.append_rolling(base, "hello\n")
        assert p == rolling_log.rolling_log_path(base)
        with open(p, "r", encoding="utf-8") as f:
            assert f.read() == "hello\n"

    def test_append_accumulates(self, tmp_path):
        base = str(tmp_path / "foo.log")
        rolling_log.append_rolling(base, "a\n")
        p = rolling_log.append_rolling(base, "b\n")
        with open(p, "r", encoding="utf-8") as f:
            assert f.read() == "a\nb\n"

    def test_creates_missing_directory(self, tmp_path):
        base = str(tmp_path / "sub" / "dir" / "foo.log")
        p = rolling_log.append_rolling(base, "x\n")
        assert os.path.exists(p)


class TestCleanupExpired:
    def _make(self, path: str, mtime_ts: float) -> str:
        with open(path, "w", encoding="utf-8") as f:
            f.write("old\n")
        os.utime(path, (mtime_ts, mtime_ts))
        return path

    def test_removes_expired_files(self, tmp_path):
        base = str(tmp_path / "foo.log")
        old_ts = time.time() - 30 * 86400
        self._make(str(tmp_path / "foo.20260101.log"), old_ts)
        self._make(str(tmp_path / "foo.20260102.log"), old_ts)
        rolling_log.append_rolling(base, "today\n")
        assert not os.path.exists(str(tmp_path / "foo.20260101.log"))
        assert not os.path.exists(str(tmp_path / "foo.20260102.log"))

    def test_keeps_fresh_files(self, tmp_path):
        base = str(tmp_path / "foo.log")
        fresh_ts = time.time() - 2 * 86400
        fresh = self._make(str(tmp_path / "foo.20260905.log"), fresh_ts)
        rolling_log.append_rolling(base, "today\n")
        assert os.path.exists(fresh)

    def test_never_deletes_today_file(self, tmp_path):
        base = str(tmp_path / "foo.log")
        p = rolling_log.append_rolling(base, "first\n")
        rolling_log.append_rolling(base, "second\n")
        assert os.path.exists(p)

    def test_keep_days_zero_keeps_today(self, tmp_path):
        base = str(tmp_path / "foo.log")
        p = rolling_log.append_rolling(base, "x\n", keep_days=0)
        assert os.path.exists(p)

    def test_skips_non_date_files(self, tmp_path):
        """文件名里的 8 位数字不是合法日期时不得误删。"""
        base = str(tmp_path / "foo.log")
        stranger = self._make(str(tmp_path / "foo.99999999.log"), time.time() - 30 * 86400)
        rolling_log.append_rolling(base, "today\n")
        assert os.path.exists(stranger)

    def test_missing_dir_returns_zero(self, tmp_path):
        base = str(tmp_path / "nope" / "foo.log")
        assert rolling_log.cleanup_expired(base) == 0

    def test_cleanup_never_raises(self, tmp_path):
        base = str(tmp_path / "foo.log")
        rolling_log.append_rolling(base, "x\n")
        # 目录里塞一个只读文件模拟删除失败，不应影响主流程
        locked = str(tmp_path / "foo.20260101.log")
        self._make(locked, time.time() - 30 * 86400)
        os.chmod(locked, 0o444)
        try:
            rolling_log.append_rolling(base, "y\n")
        finally:
            os.chmod(locked, 0o644)


class TestLogKeepDaysEnv:
    def test_default_is_seven(self):
        assert rolling_log.LOG_KEEP_DAYS == 7


class TestJsonlLineIntegrity:
    def test_written_line_is_valid_json(self, tmp_path):
        base = str(tmp_path / "gate_blockers.jsonl")
        p = rolling_log.append_rolling(base, json.dumps({"k": "v"}, ensure_ascii=False) + "\n")
        with open(p, "r", encoding="utf-8") as f:
            rec = json.loads(f.readline())
        assert rec == {"k": "v"}


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
