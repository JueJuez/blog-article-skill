"""save_pending 支持一次性队列 JSON 路径（单篇/单视频两阶段落盘）。"""
import json

from scripts import save_pending as SP


def _entry():
    return {
        "url": "https://example.com/long", "title": "长源", "author": "作者",
        "note_type": "structured", "prompt": "prompt-body", "raw_file": "raw.md",
        "generation_context": {"schema_version": 1, "note_type": "structured",
                               "type_origin": "manual", "source_sha256": "unknown",
                               "prompt_sha256": "unknown"},
        "folder": "【监控】/测试", "tags": ["测试"], "publish_time": 0,
    }


def test_path_queue_uses_monitors_shape(tmp_path):
    path = tmp_path / "entry.json"
    path.write_text(json.dumps([_entry()], ensure_ascii=False), encoding="utf-8")
    payload = SP.build_payload(str(path), _entry(), "稿", False)
    assert payload["raw_file"] == "raw.md"
    assert payload["folder"] == "【监控】/测试"
    assert payload["prompt"] == "prompt-body"
    assert payload["generation_context"]["type_origin"] == "manual"
    assert payload["obsidian"] is True


def test_load_entry_reads_url_from_path_queue(tmp_path):
    path = tmp_path / "entry.json"
    path.write_text(json.dumps([_entry()], ensure_ascii=False), encoding="utf-8")
    assert SP._load_entry(str(path), "https://example.com/long")["title"] == "长源"


def test_missing_path_queue_exits_with_error(tmp_path):
    try:
        SP._load_entry(str(tmp_path / "nope.json"), "https://example.com/long")
    except SystemExit as exc:
        assert "队列文件不存在" in str(exc)
    else:
        raise AssertionError("缺失队列文件必须报错退出")
