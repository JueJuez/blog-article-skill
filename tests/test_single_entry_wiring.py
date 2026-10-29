"""文章/视频单入口接入两阶段：降级返回里带一次性队列交接字段。"""
from shared import distill_dispatch as DD

LONG_SOURCE = "完整论证与条件应随原文保留，这里提供非数字密集的长源。\n" * 600


def test_articles_long_source_need_continue_carries_handoff(tmp_path, monkeypatch):
    from articles import main as am
    raw = tmp_path / "article_raw.md"
    raw.write_text(LONG_SOURCE, encoding="utf-8")
    monkeypatch.setattr(am, "_LAST_RAW_FILEPATH", str(raw))
    monkeypatch.setattr(am, "_LAST_PUBLISH_TIME", 0)
    monkeypatch.setattr(
        am, "summarize_and_save",
        lambda *a, **kw: (None, LONG_SOURCE, "https://example.com/long", "长源", None))
    res = am.skill_main({"content": "x", "url": "https://example.com/long"})
    assert res.get("need_continue_summary") is True
    assert res.get("distill_route") == "distill"
    assert res.get("distill_status") == "needs_distill"
    assert res.get("distill_next_action") == "distill_then_write"
    assert res.get("distill_queue")


def test_videos_long_source_need_continue_carries_handoff(monkeypatch):
    from videos import main as vm
    monkeypatch.setenv("AI_PROVIDER", "no_such_provider_xyz")
    res = vm.summarize_video({"url": "https://example.com/video",
                              "content": LONG_SOURCE, "note_type": "key_points"})
    assert res.get("need_continue_summary") is True
    assert res.get("distill_route") == "distill"
    assert res.get("distill_next_action") in ("distill_then_write", "write")


def test_videos_queue_callers_can_skip_one_off_handoff(monkeypatch):
    from videos import main as vm
    monkeypatch.setenv("AI_PROVIDER", "no_such_provider_xyz")
    res = vm.summarize_video({"url": "https://example.com/video",
                              "content": LONG_SOURCE, "note_type": "key_points",
                              "_entry_handoff": False})
    assert res.get("need_continue_summary") is True
    assert "distill_route" not in res


def test_articles_queue_callers_can_skip_one_off_handoff(tmp_path, monkeypatch):
    """监控/补齐等常驻队列调用方要显式跳过一次性交接，避免两处各登记一份。"""
    from articles import main as am
    raw = tmp_path / "raw.md"
    raw.write_text(LONG_SOURCE, encoding="utf-8")
    monkeypatch.setattr(am, "_LAST_RAW_FILEPATH", str(raw))
    monkeypatch.setattr(am, "_LAST_PUBLISH_TIME", 0)
    monkeypatch.setattr(
        am, "summarize_and_save",
        lambda *a, **kw: (None, LONG_SOURCE, "https://example.com/queued", "长源", None))
    res = am.skill_main({"content": "x", "url": "https://example.com/queued",
                         "_entry_handoff": False})
    assert res.get("need_continue_summary") is True
    assert "distill_route" not in res


def test_articles_short_source_keeps_single_round(tmp_path, monkeypatch):
    from articles import main as am
    short = "短源内容。\n" * 50
    raw = tmp_path / "short_raw.md"
    raw.write_text(short, encoding="utf-8")
    monkeypatch.setattr(am, "_LAST_RAW_FILEPATH", str(raw))
    monkeypatch.setattr(am, "_LAST_PUBLISH_TIME", 0)
    monkeypatch.setattr(
        am, "summarize_and_save",
        lambda *a, **kw: (None, short, "https://example.com/short", "短源", None))
    res = am.skill_main({"content": "x", "url": "https://example.com/short"})
    assert res.get("need_continue_summary") is True
    assert "distill_route" not in res


def test_handoff_paths_are_consumable_by_distill_prepare(tmp_path, monkeypatch):
    from scripts import distill_prepare as DPREP
    monkeypatch.setattr(DPREP, "QUEUES", {})
    assert DPREP._queue_path(str(tmp_path / "entry.json")) == str(tmp_path / "entry.json")
