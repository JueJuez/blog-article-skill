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
    monkeypatch.setenv("BILI_COOKIE", "test_cookie")  # classify ASR 探测硬依赖；无 cookie 场景单独 delenv

    def _unmocked_probe(url):
        raise RuntimeError("probe_unmocked")  # 兜底：未显式 mock 探测时禁真网，probe_error 交原管线

    monkeypatch.setattr(cmq, "_fetch_transcript_probe", _unmocked_probe)  # --fetch 字幕探测仍用此点
    monkeypatch.setattr(cmq, "_asr_probe", _unmocked_probe, raising=False)  # classify ASR 直转写探测
    monkeypatch.setattr(cmq, "_check_asr_deps", lambda: (True, []), raising=False)  # 默认依赖齐备
    monkeypatch.setattr(cmq, "_bili_view_code", lambda url: 0, raising=False)  # 默认环境健康
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


def _mock_transcript(monkeypatch, result):
    """mock 字幕探测（fetch_transcript）：result 为 None 即无CC。"""
    monkeypatch.setattr(cmq, "_fetch_transcript_probe", lambda url: result)


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
    _mock_transcript(monkeypatch, ("t", [], "a"))
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


# ---------------------------------------------------------------------------
# 无CC暂缓（deferred）：探测直标 + 错误指纹 + 批量标记 + plan 统计
# ---------------------------------------------------------------------------

def test_is_no_cc_error_matches_fingerprint():
    assert cmq._is_no_cc_error("该视频无可用字幕，且 ASR 兜底也失败（音频下载或本地转写未成功）") is True
    assert cmq._is_no_cc_error("no_cc_transcript") is True
    assert cmq._is_no_cc_error("HTTP 403 Forbidden") is False
    assert cmq._is_no_cc_error("") is False


def test_fetch_bili_no_cc_probe_defers(iso, monkeypatch):
    """无CC：探测返回 None → 立即暂缓标记，不走视频管线、不烧 fails。"""
    scan = _write_scan(iso, [BILI])
    calls = []
    _mock_video(monkeypatch, calls=calls)
    _mock_transcript(monkeypatch, None)
    r = cmq.cmd_fetch(scan)
    assert calls == []
    assert r["deferred"] == 1
    state = _read(cmq.STATE_PATH)
    assert state[BILI["url"]]["deferred"] is True
    assert state[BILI["url"]]["last_error"] == "no_cc_transcript"
    assert state[BILI["url"]].get("fails", 0) == 0
    assert _read(cmq.STAGING_PATH) == []


def test_fetch_bili_cc_probe_passes_pipeline(iso, monkeypatch):
    """有CC：探测返回字幕 → 正常走原管线入 staging。"""
    scan = _write_scan(iso, [BILI])
    calls = []
    _mock_video(monkeypatch, res=_degrade(BILI["url"], title="小猪仔拆公司第3期（重传）", raw_file="r.md"), calls=calls)
    _mock_transcript(monkeypatch, ("t", [], "a"))
    r = cmq.cmd_fetch(scan)
    assert calls == [BILI["url"]]
    assert r["enqueued"] == 1


def test_fetch_bili_probe_error_falls_back(iso, monkeypatch):
    """探测自身异常 → 不标记暂缓，交原管线按普通失败处理。"""
    scan = _write_scan(iso, [BILI])
    calls = []

    def boom(url):
        raise RuntimeError("net down")

    monkeypatch.setattr(cmq, "_fetch_transcript_probe", boom)
    _mock_video(monkeypatch, res={"success": False, "message": "抓取失败"}, calls=calls)
    cmq.cmd_fetch(scan)
    state = _read(cmq.STATE_PATH)
    assert state[BILI["url"]]["fails"] == 1
    assert "deferred" not in state[BILI["url"]]


def test_fetch_no_cc_error_defers_without_retry(iso, monkeypatch):
    """管线返回无CC+ASR失败 → 立即暂缓（不烧 3 次重试），下轮不再触碰。"""
    scan = _write_scan(iso, [ART])
    calls = []
    _mock_article(monkeypatch, res={
        "success": False,
        "message": "该视频无可用字幕，且 ASR 兜底也失败（音频下载或本地转写未成功）",
    }, calls=calls)
    r = cmq.cmd_fetch(scan)
    assert len(calls) == 1
    assert r["deferred"] == 1
    state = _read(cmq.STATE_PATH)
    assert state[ART["url"]]["deferred"] is True
    assert state[ART["url"]].get("fails", 0) == 0
    calls.clear()
    cmq.cmd_fetch(scan)
    assert calls == []


def test_fetch_skips_deferred(iso, monkeypatch):
    """已暂缓条目跳过且不占 limit 配额。"""
    scan = _write_scan(iso, [ART, dict(ART, url="https://mp.weixin.qq.com/s/def")])
    calls = []
    _mock_article(monkeypatch, calls=calls)
    with open(cmq.STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({ART["url"]: {"fails": 1, "last_error": "x", "deferred": True}}, f)
    r = cmq.cmd_fetch(scan)
    assert calls == ["https://mp.weixin.qq.com/s/def"]
    assert r["enqueued"] == 1


def test_fetch_probe_defer_does_not_consume_limit(iso, monkeypatch):
    """当轮探测暂缓不占 limit 配额：紧随其后的 article 不被无CC视频挤掉。"""
    bili = dict(BILI, url="https://www.bilibili.com/video/BVnoc1")
    scan = _write_scan(iso, [bili, ART])
    calls = []
    _mock_article(monkeypatch, calls=calls)
    _mock_video(monkeypatch, calls=calls)
    _mock_transcript(monkeypatch, None)
    r = cmq.cmd_fetch(scan, limit=1)
    assert r["deferred"] == 1
    assert calls == [ART["url"]]
    assert r["enqueued"] == 1


def test_defer_failed_marks_no_cc_state(iso):
    """--defer-failed：把 state 里带无CC指纹的失败记录批量暂缓。"""
    state = {
        ART["url"]: {"fails": 2, "last_error": "该视频无可用字幕，且 ASR 兜底也失败（…）"},
        "https://mp.weixin.qq.com/s/other": {"fails": 1, "last_error": "HTTP 403"},
    }
    with open(cmq.STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f)
    r = cmq.cmd_defer_failed()
    assert r["deferred"] == 1
    st = _read(cmq.STATE_PATH)
    assert st[ART["url"]]["deferred"] is True
    assert "deferred" not in st["https://mp.weixin.qq.com/s/other"]


def test_defer_failed_idempotent(iso):
    state = {ART["url"]: {"fails": 2, "last_error": "该视频无可用字幕", "deferred": True}}
    with open(cmq.STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f)
    r = cmq.cmd_defer_failed()
    assert r["deferred"] == 0


def test_plan_counts_deferred(iso):
    """plan：deferred 计数单列，且从 to_fetch 中排除。"""
    scan = _write_scan(iso, [ART, dict(ART, url="https://mp.weixin.qq.com/s/def")])
    with open(cmq.STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({ART["url"]: {"deferred": True}}, f)
    r = cmq.cmd_plan(scan)
    assert r["deferred"] == 1
    assert r["to_fetch"] == 1


# ---------------------------------------------------------------------------
# deferred 三分类（t10）：真无CC确认 / 视频删除定档 / 风控误标回队
# ---------------------------------------------------------------------------

def _write_state_file(state):
    with open(cmq.STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f)


def test_classify_misclassified_requeues(iso, monkeypatch):
    """探测到字幕 + 视频在 → 风控/断裂误标：清 deferred + fails 清零回重抓队列。"""
    _write_state_file({BILI["url"]: {"fails": 2, "last_error": "该视频无可用字幕", "deferred": True}})
    _mock_transcript(monkeypatch, ("t", [], "a"))
    r = cmq.cmd_classify_deferred(apply=True)
    assert r["misclassified"] == 1
    assert r["requeued"] == 1
    st = _read(cmq.STATE_PATH)
    assert "deferred" not in st[BILI["url"]]
    assert st[BILI["url"]]["fails"] == 0


def test_classify_no_cc_confirmed_keeps_deferred(iso, monkeypatch):
    """探测 None + 视频在 → 真无CC确认：deferred 保留 + 打 classify 标签。"""
    _write_state_file({BILI["url"]: {"fails": 1, "last_error": "no_cc_transcript", "deferred": True}})
    _mock_transcript(monkeypatch, None)
    r = cmq.cmd_classify_deferred(apply=True)
    assert r["no_cc_confirmed"] == 1
    st = _read(cmq.STATE_PATH)
    assert st[BILI["url"]]["deferred"] is True
    assert st[BILI["url"]]["classify"] == "no_cc_confirmed"


def test_classify_removed_video_keeps_deferred(iso, monkeypatch):
    """view API 返回删除码（-404）→ 删除定档：deferred 保留 + 打 classify 标签。"""
    _write_state_file({BILI["url"]: {"fails": 1, "last_error": "x", "deferred": True}})
    _mock_transcript(monkeypatch, None)
    monkeypatch.setattr(cmq, "_bili_view_code", lambda url: -404)
    r = cmq.cmd_classify_deferred(apply=True)
    assert r["removed_video"] == 1
    st = _read(cmq.STATE_PATH)
    assert st[BILI["url"]]["deferred"] is True
    assert st[BILI["url"]]["classify"] == "removed_video"


def test_classify_probe_error_untouched(iso, monkeypatch):
    """探测异常（非412）→ probe_error：状态不动、不打标签、不计入任何定档类。"""
    def boom(url):
        raise RuntimeError("网络抖动")

    _write_state_file({BILI["url"]: {"fails": 1, "last_error": "x", "deferred": True}})
    monkeypatch.setattr(cmq, "_fetch_transcript_probe", boom)
    r = cmq.cmd_classify_deferred(apply=True)
    assert r["probe_error"] == 1
    st = _read(cmq.STATE_PATH)
    assert st[BILI["url"]]["deferred"] is True
    assert "classify" not in st[BILI["url"]]


def test_classify_412_aborts(iso, monkeypatch):
    """探测异常含 412 指纹 → 立即停轮，后续条目不处理。"""
    def boom(url):
        raise RuntimeError("HTTP 412 Precondition Failed")

    _write_state_file({BILI["url"]: {"deferred": True},
                       "https://www.bilibili.com/video/BVnext": {"deferred": True}})
    monkeypatch.setattr(cmq, "_fetch_transcript_probe", boom)
    r = cmq.cmd_classify_deferred(apply=True)
    assert r["aborted"] is True
    assert r["probe_error"] == 1


def test_classify_dry_run_default_no_write(iso, monkeypatch):
    """默认 dry-run：探测照跑出分类结果，但 state 落盘不变。"""
    _write_state_file({BILI["url"]: {"fails": 1, "last_error": "x", "deferred": True}})
    _mock_transcript(monkeypatch, ("t", [], "a"))
    r = cmq.cmd_classify_deferred()
    assert r["misclassified"] == 1
    st = _read(cmq.STATE_PATH)
    assert st[BILI["url"]]["deferred"] is True


def test_classify_apply_writes_state(iso, monkeypatch):
    """apply=True 才把分类结果落盘。"""
    _write_state_file({BILI["url"]: {"fails": 1, "last_error": "x", "deferred": True}})
    _mock_transcript(monkeypatch, None)
    cmq.cmd_classify_deferred(apply=True)
    st = _read(cmq.STATE_PATH)
    assert st[BILI["url"]]["classify"] == "no_cc_confirmed"


def test_classify_skips_already_labeled(iso, monkeypatch):
    """已打 classify 标签的条目跳过，不重复探测烧网络。"""
    calls = []

    def probe(url):
        calls.append(url)
        return None

    other = "https://www.bilibili.com/video/BVother"
    _write_state_file({BILI["url"]: {"deferred": True, "classify": "no_cc_confirmed"},
                       other: {"deferred": True}})
    monkeypatch.setattr(cmq, "_fetch_transcript_probe", probe)
    r = cmq.cmd_classify_deferred(apply=True)
    assert calls == [other]
    assert r["skipped"] == 1
    assert r["no_cc_confirmed"] == 1


def test_classify_misclassified_fails_reset_enables_refetch(iso, monkeypatch):
    """端到端：误标回队后（fails 清零、deferred 清除），fetch 轮能重新处理该条。"""
    _write_state_file({BILI["url"]: {"fails": 3, "last_error": "该视频无可用字幕", "deferred": True}})
    _mock_transcript(monkeypatch, ("t", [], "a"))
    cmq.cmd_classify_deferred(apply=True)
    scan = _write_scan(iso, [BILI])
    _mock_video(monkeypatch, res=_degrade(BILI["url"], title="小猪仔拆公司第3期", raw_file="r.md"))
    r = cmq.cmd_fetch(scan, limit=5)
    assert r["enqueued"] == 1


def test_classify_sleeps_between_probes(iso, monkeypatch):
    """限速：N 条分类只 sleep N-1 次，避免连发触发风控。"""
    sleeps = []
    monkeypatch.setattr(cmq, "_sleep", lambda s: sleeps.append(s))
    urls = {BILI["url"]: {"deferred": True},
            "https://www.bilibili.com/video/BVa": {"deferred": True},
            "https://www.bilibili.com/video/BVb": {"deferred": True}}
    _write_state_file(urls)
    _mock_transcript(monkeypatch, None)
    cmq.cmd_classify_deferred(apply=True)
    assert len(sleeps) == 2


def test_classify_view_api_error_twice_aborts(iso, monkeypatch):
    """v2 主熔断：view API 请求层异常（None）连续 2 次 → abort，且不烧字幕探测。"""
    calls = []

    def probe(url):
        calls.append(url)
        return None

    urls = {BILI["url"]: {"deferred": True},
            "https://www.bilibili.com/video/BV22p3t6nEPM": {"deferred": True},
            "https://www.bilibili.com/video/BV33p3t6nEPM": {"deferred": True}}
    _write_state_file(urls)
    monkeypatch.setattr(cmq, "_bili_view_code", lambda url: None)
    monkeypatch.setattr(cmq, "_fetch_transcript_probe", probe)
    r = cmq.cmd_classify_deferred(apply=True)
    assert r["aborted"] is True
    assert r["probe_error"] == 2
    assert r["risk_streak"] == 2
    assert calls == []  # view 异常时不触发字幕探测（省重请求）
    st = _read(cmq.STATE_PATH)
    assert "classify" not in st[BILI["url"]]  # 异常条目不打标签


def test_classify_view_api_error_once_skips_entry(iso, monkeypatch):
    """view 异常仅 1 次（未达熔断）→ 该条跳过继续，下一条环境健康正常分类。"""
    codes = {BILI["url"]: None,
             "https://www.bilibili.com/video/BV22p3t6nEPM": 0}
    _write_state_file({u: {"deferred": True} for u in codes})
    monkeypatch.setattr(cmq, "_bili_view_code", lambda url: codes[url])
    _mock_transcript(monkeypatch, None)
    r = cmq.cmd_classify_deferred(apply=True)
    assert r["aborted"] is False
    assert r["probe_error"] == 1
    assert r["no_cc_confirmed"] == 1
    st = _read(cmq.STATE_PATH)
    assert "classify" not in st[BILI["url"]]
    assert st["https://www.bilibili.com/video/BV22p3t6nEPM"]["classify"] == "no_cc_confirmed"


def test_classify_removed_video_skips_transcript_probe(iso, monkeypatch):
    """v2 提前定档：view 删除码直接 removed_video，不调字幕探测（省重请求）。"""
    calls = []

    def probe(url):
        calls.append(url)
        return None

    _write_state_file({BILI["url"]: {"deferred": True}})
    monkeypatch.setattr(cmq, "_bili_view_code", lambda url: 62002)
    monkeypatch.setattr(cmq, "_fetch_transcript_probe", probe)
    r = cmq.cmd_classify_deferred(apply=True)
    assert r["removed_video"] == 1
    assert calls == []  # 字幕探测未被触发
    st = _read(cmq.STATE_PATH)
    assert st[BILI["url"]]["classify"] == "removed_video"
