"""consume_migrate_queue 阶段 5 消费驱动（PLAN-20260908 阶段 5，D5）TDD。

覆盖：plan 统计 / registry 断点跳过（url + old_title 双键）/ article·bili 降级入队
（folder=folder_hint 落同目录 + prompt 预计算 + old_paths 溯源）/ 风险 4 B站标题
失配转人工 / D8 失败 3 次转人工 / 412 熔断停轮 / clean 机械清洗（已总结移 done、
raw 缺失回重抓态）/ cleanup-old（v3 铁律：新文已登记才删旧文，默认 dry-run）。
"""
import json
import os

import pytest

from articles import dedup

import consume_migrate_queue as cmq


ART = {
    "kind": "article",
    "url": "https://mp.weixin.qq.com/s/abc",
    "folder_hint": "【监控】/生财有术/副业增长/日更",
    "old_paths": ["D:/vault/AI 总结笔记/【监控】/生财有术/旧文A.md"],
    "old_titles": ["旧标题A"],
}
BILI = {
    "kind": "bili_video",
    "url": "https://www.bilibili.com/video/BV11p3t6nEPM",
    "folder_hint": "【监控】/B站/价投小猪仔/小猪仔拆公司",
    "old_paths": ["D:/vault/AI 总结笔记/【监控】/B站/旧视频.md"],
    "old_titles": ["小猪仔拆公司第3期"],
}
NO_URL = {
    "kind": "manual_no_url",
    "url": "",
    "folder_hint": "【监控】/生财有术/日更",
    "old_paths": ["D:/vault/AI 总结笔记/【监控】/生财有术/无链接旧文.md"],
    "old_titles": ["无链接旧文"],
}


def _write_scan(tmp_path, entries):
    scan = tmp_path / "migrate_scan_20260909_000000.json"
    scan.write_text(json.dumps(
        {"generated_at": "t", "vault": "v", "apply": True, "scan": {}, "queue": entries},
        ensure_ascii=False), encoding="utf-8")
    return str(scan)


def _degrade(url, title="抓回的标题", folder="", raw_file=""):
    """模拟 FORCE_AGENT_MODE 降级返回（skill_main / summarize_video 同构）。"""
    return {
        "success": True, "need_continue_summary": True,
        "article_content": "正文内容",
        "note_type": "structured",
        "prompt": "PROMPT+CHECK",
        "original_url": url, "original_title": title,
        "author": "作者", "tags": [], "raw_file": raw_file, "folder": folder,
        "publish_time": 0,
    }


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cmq, "STAGING_PATH", str(tmp_path / "staging.json"))
    monkeypatch.setattr(cmq, "DONE_PATH", str(tmp_path / "done.json"))
    monkeypatch.setattr(cmq, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(cmq, "MANUAL_PATH", str(tmp_path / "manual.json"))
    monkeypatch.setattr(cmq, "_sleep", lambda *_: None)
    return tmp_path


def _mock_article(monkeypatch, res=None, calls=None, raw=""):
    def fake(url, folder):
        if calls is not None:
            calls.append(url)
        if isinstance(res, list):
            return res.pop(0)
        return res if res is not None else _degrade(url, folder=folder, raw_file=raw)
    monkeypatch.setattr(cmq, "_fetch_article", fake)


def _mock_video(monkeypatch, res=None, calls=None, raw=""):
    def fake(url, folder):
        if calls is not None:
            calls.append(url)
        if isinstance(res, list):
            return res.pop(0)
        return res if res is not None else _degrade(url, folder=folder, raw_file=raw)
    monkeypatch.setattr(cmq, "_fetch_video", fake)


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

def test_plan_reports_kind_counts(iso):
    scan = _write_scan(iso, [ART, BILI, NO_URL])
    r = cmq.cmd_plan(scan)
    assert r["kinds"] == {"article": 1, "bili_video": 1, "manual_no_url": 1}
    assert r["total"] == 3


def test_plan_counts_registered_url_as_done(iso):
    scan = _write_scan(iso, [ART, BILI, NO_URL])
    dedup.mark_summarized(url=ART["url"], title="t", filename="n.md")
    r = cmq.cmd_plan(scan)
    assert r["registered"] == 1
    assert r["to_fetch"] == 1


def test_plan_counts_registered_old_title_as_done(iso):
    """风险 4 对称面：old_titles 标题指纹命中登记表同样算已处理。"""
    scan = _write_scan(iso, [ART, BILI, NO_URL])
    dedup.mark_summarized(url="", title=BILI["old_titles"][0], filename="n.md")
    r = cmq.cmd_plan(scan)
    assert r["registered"] == 1
    assert r["to_fetch"] == 1


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

def test_fetch_article_enqueues_staging(iso, monkeypatch):
    scan = _write_scan(iso, [ART])
    _mock_article(monkeypatch, raw="r.md")
    r = cmq.cmd_fetch(scan)
    staging = json.loads(open(cmq.STAGING_PATH, encoding="utf-8").read())
    assert r["enqueued"] == 1 and len(staging) == 1
    e = staging[0]
    assert e["url"] == ART["url"]
    assert e["folder"] == ART["folder_hint"]
    assert e["prompt"] == "PROMPT+CHECK"
    assert e["old_paths"] == ART["old_paths"]
    assert e["kind"] == "article"


def test_fetch_bili_title_mismatch_goes_manual(iso, monkeypatch):
    """风险 4：抓回标题与 old_titles 全失配 → 不入队，转人工（URL 已变防错抓）。"""
    scan = _write_scan(iso, [BILI])
    _mock_video(monkeypatch, res=_degrade(BILI["url"], title="完全无关的另一档节目XYZ"))
    r = cmq.cmd_fetch(scan)
    staging = json.loads(open(cmq.STAGING_PATH, encoding="utf-8").read())
    manual = json.loads(open(cmq.MANUAL_PATH, encoding="utf-8").read())
    assert staging == []
    assert r["to_manual"] == 1
    assert manual[0]["reason"] == "url_changed"


def test_fetch_bili_title_match_enqueues(iso, monkeypatch):
    """风险 4 对称面：抓回标题与 old_titles 相似（ratio>=0.55）→ 正常入队。"""
    scan = _write_scan(iso, [BILI])
    _mock_video(monkeypatch, res=_degrade(BILI["url"], title="小猪仔拆公司第3期（重传）", raw_file="r.md"))
    r = cmq.cmd_fetch(scan)
    staging = json.loads(open(cmq.STAGING_PATH, encoding="utf-8").read())
    assert r["enqueued"] == 1 and len(staging) == 1
    assert staging[0]["folder"] == BILI["folder_hint"]


def test_fetch_failure_three_strikes_manual(iso, monkeypatch):
    """D8：同条累计失败 3 次转人工清单，之后不再自动重试。"""
    scan = _write_scan(iso, [ART])
    calls = []

    def fake(url, folder):
        calls.append(url)
        return {"success": False, "message": "抓取失败"}

    monkeypatch.setattr(cmq, "_fetch_article", fake)
    for _ in range(3):
        cmq.cmd_fetch(scan)
    manual = json.loads(open(cmq.MANUAL_PATH, encoding="utf-8").read())
    assert len(calls) == 3
    assert manual[0]["url"] == ART["url"] and manual[0]["reason"] == "fail_3"
    cmq.cmd_fetch(scan)
    assert len(calls) == 3


def test_fetch_manual_no_url_direct(iso, monkeypatch):
    scan = _write_scan(iso, [NO_URL])
    r = cmq.cmd_fetch(scan)
    manual = json.loads(open(cmq.MANUAL_PATH, encoding="utf-8").read())
    assert r["to_manual"] == 1
    assert manual[0]["reason"] == "no_url"


def test_fetch_412_aborts_round(iso, monkeypatch):
    """412 风控：本轮立即停止，后续条目不处理。"""
    scan = _write_scan(iso, [BILI, dict(BILI, url="https://www.bilibili.com/video/BVnext")])
    calls = []
    _mock_video(monkeypatch, res={"success": False, "message": "RISK_CONTROL_412_STOP"}, calls=calls)
    r = cmq.cmd_fetch(scan)
    assert r["aborted"] is True
    assert len(calls) == 1


def test_fetch_limit_caps_processed(iso, monkeypatch):
    scan = _write_scan(iso, [ART, dict(ART, url="https://mp.weixin.qq.com/s/def")])
    calls = []
    _mock_article(monkeypatch, calls=calls)
    r = cmq.cmd_fetch(scan, limit=1)
    assert len(calls) == 1
    assert r["enqueued"] == 1


def test_fetch_skips_registered_and_staged(iso, monkeypatch):
    scan = _write_scan(iso, [ART, BILI])
    calls = []
    _mock_article(monkeypatch, calls=calls)
    _mock_video(monkeypatch, calls=calls)
    dedup.mark_summarized(url=ART["url"], title="t", filename="n.md")
    cmq.cmd_fetch(scan)
    assert calls == [BILI["url"]]
    calls.clear()
    cmq.cmd_fetch(scan)
    assert calls == []


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------

def _stage(entries):
    with open(cmq.STAGING_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.loads(f.read())


def test_clean_moves_registered_to_done(iso):
    _stage([dict(ART, raw_file="r.md", prompt="P", note_type="structured")])
    dedup.mark_summarized(url=ART["url"], title="t", filename="n.md")
    r = cmq.cmd_clean()
    assert r["done"] == 1
    assert _read(cmq.STAGING_PATH) == []
    done = _read(cmq.DONE_PATH)
    assert done[0]["old_paths"] == ART["old_paths"]


def test_clean_drops_missing_raw_to_refetch(iso):
    _stage([dict(ART, raw_file="Z:/no/such/file.md", prompt="P", note_type="structured")])
    r = cmq.cmd_clean()
    assert r["refetch"] == 1
    assert _read(cmq.STAGING_PATH) == []
    state = _read(cmq.STATE_PATH)
    assert state[ART["url"]]["fails"] == 1


def test_clean_keeps_valid_pending(iso, tmp_path):
    raw = tmp_path / "r.md"
    raw.write_text("x" * 500, encoding="utf-8")
    _stage([dict(ART, raw_file=str(raw), prompt="P", note_type="structured")])
    r = cmq.cmd_clean()
    assert r["kept"] == 1
    assert len(_read(cmq.STAGING_PATH)) == 1


# ---------------------------------------------------------------------------
# cleanup-old（v3 铁律：重抓成功落盘 → 校验 → 才删旧文）
# ---------------------------------------------------------------------------

def _make_old(tmp_path, entry):
    old = tmp_path / "old_note.md"
    old.write_text("旧文", encoding="utf-8")
    entry = dict(entry, old_paths=[str(old)])
    _stage([])
    with open(cmq.DONE_PATH, "w", encoding="utf-8") as f:
        json.dump([entry], f, ensure_ascii=False)
    return old


def test_cleanup_old_dry_run_keeps_files(iso, tmp_path):
    old = _make_old(tmp_path, ART)
    dedup.mark_summarized(url=ART["url"], title="t", filename="n.md")
    r = cmq.cmd_cleanup_old(apply=False)
    assert old.exists()
    assert r["would_delete"] == 1


def test_cleanup_old_apply_deletes_when_registered(iso, tmp_path):
    old = _make_old(tmp_path, ART)
    dedup.mark_summarized(url=ART["url"], title="t", filename="n.md")
    r = cmq.cmd_cleanup_old(apply=True)
    assert not old.exists()
    assert r["deleted"] == 1


def test_cleanup_old_keeps_when_not_registered(iso, tmp_path):
    old = _make_old(tmp_path, ART)
    r = cmq.cmd_cleanup_old(apply=True)
    assert old.exists()
    assert r["deleted"] == 0
    assert r["kept"] == 1


# ---------------------------------------------------------------------------
# scan 发现与容错
# ---------------------------------------------------------------------------

def test_latest_scan_picks_newest(tmp_path):
    (tmp_path / "migrate_scan_20260908_020630.json").write_text("{}", encoding="utf-8")
    (tmp_path / "migrate_scan_20260908_232003.json").write_text("{}", encoding="utf-8")
    assert cmq.latest_scan(str(tmp_path)).endswith("migrate_scan_20260908_232003.json")


def test_latest_scan_missing_dir_returns_none(tmp_path):
    assert cmq.latest_scan(str(tmp_path / "nope")) is None


def test_load_queue_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        cmq.load_queue(str(tmp_path / "nope.json"))
