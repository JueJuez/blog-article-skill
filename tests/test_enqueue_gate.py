"""3.1 入队批量预查（登记表闸门）。

apply_summaries 入口在遍历 items 之前，batch_is_summarized 一次 IO 批量查
dedup 登记表，命中者在「抓取/总结」之前剔除，省 AI token 与重复落盘；
健康度行以「登记表跳过 N」汇报剔除条数。
"""
from articles import dedup
from monitors import run as run_mod

DYN_TEXT = "这是一条用于验证登记表批量闸门的长动态正文内容，" * 12


def _make_calls() -> dict:
    return {"skill_main": [], "queue": []}


def _patch_pipeline(monkeypatch, calls: dict, tmp_path) -> None:
    def _fake_skill(*args, **kwargs):
        calls["skill_main"].append(kwargs or args)
        return {"message": "ok"}

    def _fake_queue(it, res):
        calls["queue"].append(it.get("url", ""))

    monkeypatch.setattr("articles.main.skill_main", _fake_skill)
    monkeypatch.setattr(run_mod, "_queue_pending_summary", _fake_queue)
    monkeypatch.setenv(
        "MON_PENDING_SUMMARY_PATH", str(tmp_path / "pending_summaries.json")
    )
    monkeypatch.setenv(
        "MON_PENDING_REFETCH_PATH", str(tmp_path / "pending_refetch.json")
    )


def _dyn_item(url: str) -> dict:
    return {
        "route": "dynamic",
        "url": url,
        "title": "一条用于验证登记表闸门的动态标题",
        "content": DYN_TEXT,
        "mp_name": "测试UP",
        "publish_time": 1700000000,
    }


def test_registry_hit_skips_pipeline(tmp_path, monkeypatch, capsys):
    """已登记 URL 在入口即剔除，skill_main 与队列均不进入。"""
    calls = _make_calls()
    _patch_pipeline(monkeypatch, calls, tmp_path)

    url = "https://www.bilibili.com/opus/111"
    dedup.mark_summarized(url=url, title="已总结", filename="x.md")

    run_mod.apply_summaries([_dyn_item(url)], consume_prev_refetch=False)

    assert calls["skill_main"] == []
    assert calls["queue"] == []
    out = capsys.readouterr().out
    assert "登记表跳过 1" in out


def test_registry_miss_keeps_item(tmp_path, monkeypatch, capsys):
    """未登记 URL 照常放行，健康度行登记表跳过计数为 0。"""
    calls = _make_calls()
    _patch_pipeline(monkeypatch, calls, tmp_path)

    run_mod.apply_summaries(
        [_dyn_item("https://www.bilibili.com/opus/222")], consume_prev_refetch=False
    )

    assert len(calls["skill_main"]) == 1
    out = capsys.readouterr().out
    assert "登记表跳过 0" in out
