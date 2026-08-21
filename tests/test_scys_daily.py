"""scys 日常监控（跑一下）接入测试：命令拼装 / 领域过滤 / 失败降级 / 抓取互斥锁。"""
import json
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(BASE_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(BASE_DIR / "scripts"))

from monitors import run as run_mod


def _fake_completed(rc: int = 0):
    class _Result:
        returncode = rc
    return _Result()


class TestScysDailyCmd:
    def test_cmd_contains_script_project_window_pages(self):
        cmd = run_mod._scys_daily_cmd("出海", 7)
        assert cmd[0] == sys.executable
        assert cmd[1].endswith("scys_batch_fetch.py")
        assert cmd[cmd.index("--project") + 1] == "出海"
        assert cmd[cmd.index("--since-days") + 1] == "7"
        assert "--pages" in cmd

    def test_cmd_includes_nondigested_engagement_mode(self):
        # 2026-08-21：监控也抓高互动非精华新帖（锚≥30，或 赞≥80且锚≥10，精华直通），
        # 防止「晚精华/高互动答疑帖」永久漏抓
        cmd = run_mod._scys_daily_cmd("出海", 7)
        assert "--no-digested-only" in cmd


class TestRunScysDaily:
    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch, tmp_path):
        cfg = tmp_path / "scys_projects.json"
        cfg.write_text(json.dumps(
            {"projects": {"自媒体": 2850207, "出海": 3492647}}, ensure_ascii=False),
            encoding="utf-8")
        monkeypatch.setattr(run_mod, "SCYS_CONFIG_PATH", str(cfg))
        monkeypatch.setattr(run_mod.time, "sleep", lambda *_: None)
        self.calls = []
        calls = self.calls
        monkeypatch.setattr(run_mod.subprocess, "run",
                            lambda cmd, cwd=None: (calls.append(cmd), _fake_completed(0))[1])

    def test_runs_each_configured_project(self):
        run_mod.run_scys_daily([{"project": "自媒体"}, {"project": "出海"}], mode="auto")
        assert len(self.calls) == 2

    def test_unknown_project_skipped(self):
        run_mod.run_scys_daily([{"project": "小程序"}], mode="auto")
        assert self.calls == []

    def test_subprocess_crash_does_not_raise(self, monkeypatch):
        def boom(cmd, cwd=None):
            raise OSError("CDP 不可达")
        monkeypatch.setattr(run_mod.subprocess, "run", boom)
        run_mod.run_scys_daily([{"project": "自媒体"}], mode="auto")

    def test_nonzero_exit_continues_to_next_project(self, monkeypatch):
        codes = [1, 0]
        seen = []

        def fake_run(cmd, cwd=None):
            seen.append(cmd[cmd.index("--project") + 1])
            return _fake_completed(codes.pop(0))
        monkeypatch.setattr(run_mod.subprocess, "run", fake_run)
        run_mod.run_scys_daily([{"project": "自媒体"}, {"project": "出海"}], mode="auto")
        assert seen == ["自媒体", "出海"]

    def test_first_mode_uses_first_window(self):
        run_mod.run_scys_daily([{"project": "自媒体"}], mode="first")
        cmd = self.calls[0]
        assert cmd[cmd.index("--since-days") + 1] == str(run_mod.SCYS_FIRST_WINDOW_DAYS)

    def test_entry_since_days_override(self):
        run_mod.run_scys_daily([{"project": "自媒体", "since_days": 30}], mode="auto")
        cmd = self.calls[0]
        assert cmd[cmd.index("--since-days") + 1] == "30"


class TestBatchLock:
    def test_lock_acquire_block_and_release(self, monkeypatch, tmp_path):
        import scys_batch_fetch as sbf
        monkeypatch.setattr(sbf, "BASE", tmp_path)
        lock = sbf._acquire_lock()
        assert lock.exists()
        with pytest.raises(SystemExit):
            sbf._acquire_lock()
        sbf._release_lock(lock)
        assert not lock.exists()
        lock2 = sbf._acquire_lock()
        sbf._release_lock(lock2)
