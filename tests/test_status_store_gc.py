"""status_store 旧 run 分片惰性 GC 测试（按 mtime，LOG_KEEP_DAYS 默认 7 天）。

背景：run_status/<run_id>.<shard>.tasks.jsonl 按 run 无限堆积，需要与
rolling_log 同策略的惰性清理（run_id 含日期但格式 YYYYMMDD-HHMMSS 不匹配
8 位纯日期校验，故按 mtime 直接判断）。
"""
import os
import tempfile
import time
import unittest
from unittest import mock

from monitors import status_store


class StatusStoreGcTestBase(unittest.TestCase):
    """把 RUN_STATUS_DIR 指到临时目录，避免污染真实监控状态。"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="status_store_gc_test_")
        self._patcher = mock.patch.object(status_store, "RUN_STATUS_DIR", self._tmpdir)
        self._patcher.start()
        # 重置 GC 速率限制，保证每个用例独立触发
        self._gc_patcher = mock.patch.object(status_store, "_last_gc_ts", 0.0)
        self._gc_patcher.start()

    def tearDown(self):
        self._gc_patcher.stop()
        self._patcher.stop()
        for name in os.listdir(self._tmpdir):
            try:
                os.remove(os.path.join(self._tmpdir, name))
            except OSError:
                pass
        try:
            os.rmdir(self._tmpdir)
        except OSError:
            pass

    def _make_shard(self, run_id: str, shard: str = "bili",
                    mtime: float = None) -> str:
        path = status_store._shard_path(run_id, shard)
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"run_id": "%s"}\n' % run_id)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path


class TestCleanupOldRuns(StatusStoreGcTestBase):
    def test_old_shard_removed(self):
        """30 天前的旧分片被删除，返回删除数 1。"""
        old_ts = time.time() - 30 * 86400
        path = self._make_shard("20260101-000000", mtime=old_ts)
        removed = status_store.cleanup_old_runs(keep_days=7)
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(path))

    def test_recent_shard_kept(self):
        """2 天内的新分片保留。"""
        recent_ts = time.time() - 2 * 86400
        path = self._make_shard("20260905-000000", mtime=recent_ts)
        removed = status_store.cleanup_old_runs(keep_days=7)
        self.assertEqual(removed, 0)
        self.assertTrue(os.path.exists(path))

    def test_non_shard_file_untouched(self):
        """latest.json 等非 *.tasks.jsonl 文件不受 GC 影响。"""
        latest = os.path.join(self._tmpdir, "latest.json")
        old_ts = time.time() - 30 * 86400
        with open(latest, "w", encoding="utf-8") as f:
            f.write("{}")
        os.utime(latest, (old_ts, old_ts))
        removed = status_store.cleanup_old_runs(keep_days=7)
        self.assertEqual(removed, 0)
        self.assertTrue(os.path.exists(latest))

    def test_missing_dir_returns_zero(self):
        """目录不存在时返回 0，不抛异常。"""
        with mock.patch.object(status_store, "RUN_STATUS_DIR",
                               os.path.join(self._tmpdir, "no_such_dir")):
            self.assertEqual(status_store.cleanup_old_runs(keep_days=7), 0)

    def test_keep_days_zero_deletes_everything(self):
        """keep_days=0（mtime 语义）= 全删严格早于此刻的文件（分片无「今天文件」概念）。"""
        path = self._make_shard("20260907-120000", mtime=time.time() - 1)
        self.assertEqual(status_store.cleanup_old_runs(keep_days=0), 1)
        self.assertFalse(os.path.exists(path))


class TestRecordTaskLazyGc(StatusStoreGcTestBase):
    def test_record_task_triggers_gc(self):
        """record_task 触发惰性 GC：旧分片被清掉。"""
        old_ts = time.time() - 30 * 86400
        self._make_shard("20260101-000000", mtime=old_ts)
        status_store.record_task("20260907-100000", "bili", "BV1", "fetch",
                                 "ok")
        self.assertFalse(os.path.exists(
            status_store._shard_path("20260101-000000", "bili")))

    def test_gc_rate_limited_once_per_interval(self):
        """GC 有速率限制：一次 record_task 只触发一次清理。"""
        with mock.patch.object(status_store, "cleanup_old_runs",
                               return_value=0) as gc_mock:
            status_store.record_task("20260907-100000", "bili", "BV1",
                                     "fetch", "ok")
            status_store.record_task("20260907-100000", "bili", "BV2",
                                     "fetch", "ok")
        self.assertEqual(gc_mock.call_count, 1)

    def test_record_task_writes_after_gc(self):
        """GC 后记录仍正常写入对应分片。"""
        status_store.record_task("20260907-100000", "bili", "BV1",
                                 "fetch", "ok")
        recs = status_store.read_run_tasks("20260907-100000")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["item_id"], "BV1")


if __name__ == "__main__":
    unittest.main()
