"""land_migrate_entry 纯函数单测（parse_idx / _file_exists）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from land_migrate_entry import _file_exists, parse_idx


class TestParseIdx:
    def test_normal_range(self):
        assert parse_idx("0-2", 5) == [0, 1, 2]

    def test_normal_single(self):
        assert parse_idx("3", 5) == [3]

    def test_normal_mixed(self):
        assert parse_idx("0,2-3,4", 5) == [0, 2, 3, 4]

    def test_dedup_and_sorted(self):
        assert parse_idx("4,1,1,2-3", 5) == [1, 2, 3, 4]

    def test_boundary_full_range(self):
        assert parse_idx("0-4", 5) == [0, 1, 2, 3, 4]

    def test_boundary_out_of_range_filtered(self):
        assert parse_idx("3-9", 5) == [3, 4]

    def test_empty_spec_returns_empty(self):
        assert parse_idx("", 5) == []

    def test_invalid_input_raises(self):
        with pytest.raises(ValueError):
            parse_idx("abc", 5)


class TestFileExists:
    def test_abs_path_exists(self, tmp_path):
        f = tmp_path / "a.md"
        f.write_text("x", encoding="utf-8")
        assert _file_exists(str(tmp_path), str(f)) is True

    def test_relative_path_joins_vault(self, tmp_path):
        f = tmp_path / "a.md"
        f.write_text("x", encoding="utf-8")
        assert _file_exists(str(tmp_path), "a.md") is True

    def test_missing_file_false(self, tmp_path):
        assert _file_exists(str(tmp_path), "ghost.md") is False

    def test_empty_filename_false(self, tmp_path):
        assert _file_exists(str(tmp_path), "") is False
