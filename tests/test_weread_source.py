"""tests/test_weread_source.py — weread 直连源单测（PLAN-20260919，全部 mock，不发真请求）。

覆盖：
  - 列表响应展平 / reviewId→token→mp 直链映射 / 错误码分类（-2010/-2012/-2041 → 观测上报路径）
  - 翻页累加逻辑：offset += len(reviews)、空列表即停（勿用 50 步长）
  - discover_weread：首跑基线语义、增量去重、auth/验证码停手（剩余号跳过、剩余号零请求）
  - run.py 挂载：开关默认关、开启时调用 weread 且条目进入 all_new
"""
import json
import os
import sys
import time
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from monitors import weread as wr  # noqa: E402
from monitors import run as run_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_quota(tmp_path, monkeypatch):
    """所有用例默认使用临时配额台账（与真实 .weread_quota.json 隔离）。"""
    monkeypatch.setattr(wr, "QUOTA_PATH", str(tmp_path / "quota.json"))


def _review(rid: str, title: str, ts: int) -> dict:
    return {"review": {"reviewId": rid, "createTime": ts,
                       "mpInfo": {"title": title, "originalId": rid.rsplit("_", 1)[-1]}}}


def _resp(rids, ts=1758000000):
    return {"reviews": [
        {"subReviews": [_review(rid, f"标题{rid[-4:]}", ts + i) for i, rid in enumerate(rids)]}
    ]}


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------

def test_parse_reviews_flattens_nested():
    resp = {"reviews": [
        {"subReviews": [_review("MP_WXS_1_aaa", "A", 1), _review("MP_WXS_2_bbb", "B", 2)]},
        {"subReviews": [_review("MP_WXS_3_ccc", "C", 3)]},
    ]}
    rids = [r["reviewId"] for r in wr.parse_reviews(resp)]
    assert rids == ["MP_WXS_1_aaa", "MP_WXS_2_bbb", "MP_WXS_3_ccc"]


def test_parse_reviews_defensive():
    assert wr.parse_reviews(None) == []
    assert wr.parse_reviews({"errCode": -2041}) == []
    assert wr.parse_reviews({"reviews": [None, {}, {"subReviews": None}]}) == []


def test_extract_token_and_reject_malformed():
    assert wr.extract_token("MP_WXS_2399233620_AbCdEf123") == "AbCdEf123"
    assert wr.extract_token("MP_WXS_2399233620") == ""      # 缺 token 段，拒绝
    assert wr.extract_token("") == ""
    assert wr.extract_token(None) == ""


def test_classify_error_codes():
    for code in (-2010, -2012, -2041):
        assert wr.classify_list_response({"errCode": code}) == "auth"
    assert wr.classify_list_response({"errCode": -9999}) == "unknown_err"
    assert wr.classify_list_response(_resp(["MP_WXS_1_a"])) == "ok"
    assert wr.classify_list_response({"reviews": []}) == "empty"
    assert wr.classify_list_response("not-a-dict") == "unknown_err"


def test_paginate_accumulates_offset_by_len():
    """翻页铁律：offset += len(reviews)，空列表即停（实测 50 步长会跳过约 30 条/页）。"""
    pages = {0: _resp([f"MP_WXS_1_{i:02d}" for i in range(20)], ts=3000),
             20: _resp([f"MP_WXS_1_{i:02d}" for i in range(20)], ts=2000),
             40: _resp([f"MP_WXS_1_{i:02d}" for i in range(20)], ts=1000),
             60: {"reviews": []}}
    calls = []

    def fetch_page(book_id, offset):
        calls.append(offset)
        return pages[offset]

    reviews, cat, _ = wr.paginate(fetch_page, "MP_WXS_1", max_pages=10)
    assert cat == "ok"
    assert calls == [0, 20, 40, 60]          # 每页 20 条精确偏移
    assert len(reviews) == 60                # 页间无缝衔接，无跳漏


def test_paginate_stops_on_auth_error():
    pages = {0: _resp([f"MP_WXS_1_{i:02d}" for i in range(20)]),
             20: {"errCode": -2041}}
    reviews, cat, last = wr.paginate(lambda b, o: pages[o], "MP_WXS_1", max_pages=5)
    assert cat == "auth" and last["errCode"] == -2041
    assert len(reviews) == 20                # 已拿到的第 1 页保留，中断上报


def test_paginate_cutoff_stops_at_window_boundary():
    """时间窗语义：翻到「整页早于窗口起点」即停（窗口内全要，与更新频率无关）。"""
    now = int(time.time())
    pages = {
        0: {"reviews": [{"subReviews": [_review(f"MP_WXS_1_r{i}", "T", now - 3600 * (i + 1))
                                         for i in range(20)]}]},          # 最近 20h 内
        20: {"reviews": [{"subReviews": [_review(f"MP_WXS_1_o{i}", "T", now - 86400 * 40)
                                          for i in range(3)]}]},          # 40 天前
    }
    calls = []

    def fetch_page(book_id, offset):
        calls.append(offset)
        return pages[offset]

    reviews, cat, _ = wr.paginate(fetch_page, "MP_WXS_1", max_pages=10,
                                  cutoff_ts=now - 7 * 86400)
    assert calls == [0, 20]                  # 第 2 页整页早于 7 天窗 → 取完即停
    assert cat == "ok" and len(reviews) == 23


# ---------------------------------------------------------------------------
# 条目映射
# ---------------------------------------------------------------------------

def test_review_to_item_maps_mp_url():
    r = _review("MP_WXS_2399233620_AbCdEf123", "哥飞新文", 1758000000)["review"]
    it = wr.review_to_item(r, "哥飞", "独立开发出海")
    assert it["url"] == "https://mp.weixin.qq.com/s/AbCdEf123"
    assert it["title"] == "哥飞新文"
    assert it["publish_time"] == 1758000000
    assert it["route"] == "article" and it["source"] == "wechat"
    assert it["mp_name"] == "哥飞"


def test_review_to_item_rejects_bad_id():
    assert wr.review_to_item({"reviewId": "MP_WXS_2399233620"}, "哥飞") is None
    assert wr.review_to_item({}, "哥飞") is None


# ---------------------------------------------------------------------------
# resolve_book_ids
# ---------------------------------------------------------------------------

def test_resolve_book_ids_fallback_and_cache(tmp_path, monkeypatch):
    cache_file = tmp_path / ".mp_cache.json"
    cache_file.write_text(json.dumps({
        "https://mp.weixin.qq.com/s/xxx": {"name": "新号", "id": "MP_WXS_999"},
    }), encoding="utf-8")
    monkeypatch.setattr(wr, "MP_CACHE_PATH", str(cache_file))
    subs = {"wechat": [
        {"name": "哥飞"},                     # 硬编码表命中
        {"name": "新号", "share_url": "https://mp.weixin.qq.com/s/xxx"},  # cache 命中
        {"name": "无映射号"},                  # 跳过
    ]}
    entries = wr.resolve_book_ids(subs)
    assert {"name": "哥飞", "book_id": "MP_WXS_2399233620", "category": ""} in entries
    assert {"name": "新号", "book_id": "MP_WXS_999", "category": ""} in entries
    assert all(e["name"] != "无映射号" for e in entries)


# ---------------------------------------------------------------------------
# discover_weread（Fake CDP）
# ---------------------------------------------------------------------------

class FakePage:
    def __init__(self, responses=None, text=""):
        self.responses = list(responses or [])
        self.text = text
        self.fetch_apis = []
        self.shots = []

    def evaluate(self, js, args=None):
        if args is None:
            if "__WRPA__" in js:
                return "object"  # _ensure_wrpa_ready 的 typeof 探测：模拟签名器已就绪
            return self.text
        self.fetch_apis.append(args[0])
        resp = self.responses.pop(0) if self.responses else {}
        return {"ok": True, "status": 200, "body": json.dumps(resp), "len": 0}

    def screenshot(self, path=None):
        self.shots.append(path)
        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 25)
        return path

    def goto(self, *a, **k):
        pass

    def wait_for_timeout(self, *a, **k):
        pass


class FakeSession:
    def __init__(self, page):
        self._page = page
        self.context = types.SimpleNamespace(pages=[])
        self.closed = False

    def new_page(self):
        self.context.pages.append(self._page)
        return self._page

    def close(self):
        self.closed = True


def _entries(n=2):
    return [{"name": f"号{i}", "book_id": f"MP_WXS_{100 + i}", "category": ""} for i in range(n)]


def test_discover_first_run_baseline_only(tmp_path, monkeypatch):
    """首跑语义：seen 为空的号只建基线（全部 reviewId 入 seen），不产总结条目。"""
    monkeypatch.setattr(wr, "LIST_GAP", 0)
    page = FakePage(responses=[_resp([f"MP_WXS_1_{c}" for c in "abc"])])
    state = {"sources": {}}
    items, health = wr.discover_weread(state, _entries(1), session=FakeSession(page))
    assert items == []
    assert health["ok"] == 1 and "号0" in health["baseline"]
    seen = state["sources"]["weread:MP_WXS_100"]["seen"]
    assert seen == ["MP_WXS_1_a", "MP_WXS_1_b", "MP_WXS_1_c"]


def test_discover_incremental_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "LIST_GAP", 0)
    monkeypatch.setattr(wr, "WEREAD_WINDOW_DAYS", 0)  # 关时间窗（测试用旧时间戳）
    state = {"sources": {"weread:MP_WXS_100": {"seen": ["MP_WXS_1_a"]}}}
    page = FakePage(responses=[_resp([f"MP_WXS_1_{c}" for c in "abc"])])
    items, health = wr.discover_weread(state, _entries(1), session=FakeSession(page))
    assert [it["id"] for it in items] == ["MP_WXS_1_b", "MP_WXS_1_c"]
    assert health["new"] == 2 and health["ok"] == 1


def test_discover_auth_error_stops_remaining_accounts(monkeypatch):
    """-2041 → 观测上报路径：记录错误码、停手、剩余号跳过且零请求。"""
    monkeypatch.setattr(wr, "LIST_GAP", 0)
    monkeypatch.setattr(wr, "WEREAD_AUTO_RELOGIN", False)  # 本测关闭自动弹码，验上报语义
    page = FakePage(responses=[{"errCode": -2041}])   # 只有 1 个响应：号1 若被请求会拿空
    state = {"sources": {"weread:MP_WXS_1": {"seen": ["x"]}}}
    items, health = wr.discover_weread(state, _entries(2), session=FakeSession(page))
    assert items == []
    assert len(page.fetch_apis) == 1                  # 停手：号1 没有发请求
    assert health["errors"][0][0] == "号0" and health["errors"][0][1] == "auth"
    assert "errCode=-2041" in health["errors"][0][2]
    assert health["skipped"] == ["号1"]


def test_discover_captcha_screenshots_and_stops(monkeypatch, tmp_path):
    monkeypatch.setattr(wr, "LIST_GAP", 0)
    monkeypatch.setattr(wr, "CAPTCHA_DIR", str(tmp_path))
    page = FakePage(responses=[_resp([f"MP_WXS_1_{c}" for c in "abc"])],
                    text="安全检测中，请完成验证后继续访问")
    state = {"sources": {}}                            # 轮次开头就检测到 → 零列表请求
    items, health = wr.discover_weread(state, _entries(1), session=FakeSession(page))
    assert items == [] and health["captcha"] is True
    assert page.fetch_apis == []
    assert len(page.shots) == 1 and os.path.exists(page.shots[0])


def test_discover_unreachable_token_skipped_and_not_seen(monkeypatch):
    """含 ~ 的非 /s/ 型 token（实测 mp 直链「参数错误」）：不入队、不标 seen，健康度暴露。"""
    monkeypatch.setattr(wr, "LIST_GAP", 0)
    monkeypatch.setattr(wr, "WEREAD_WINDOW_DAYS", 0)
    state = {"sources": {"weread:MP_WXS_100": {"seen": ["MP_WXS_100_dummy"]}}}
    page = FakePage(responses=[_resp(["MP_WXS_100_okToken1", "MP_WXS_100_bad~Token"])])
    items, health = wr.discover_weread(state, _entries(1), session=FakeSession(page))
    assert [it["id"] for it in items] == ["MP_WXS_100_okToken1"]
    seen_now = state["sources"]["weread:MP_WXS_100"]["seen"]
    assert "MP_WXS_100_okToken1" in seen_now and "MP_WXS_100_bad~Token" not in seen_now
    assert health["unreachable"] and "bad~Token" in health["unreachable"][0]


def test_format_health_segments():
    h = {"ok": 2, "new": 3, "captcha": True, "errors": [("哥飞", "auth", "errCode=-2041")],
         "skipped": ["生财有术"], "baseline": []}
    s = wr.format_health(h)
    assert "正常 2 号" in s and "新文章 3" in s
    assert "安全检测" in s and "errCode=-2041" in s and "生财有术" in s


# ---------------------------------------------------------------------------
# run.py 挂载
# ---------------------------------------------------------------------------

def test_run_discover_all_weread_disabled_by_default(monkeypatch):
    monkeypatch.setattr(run_mod, "WEREAD_SOURCE_ENABLED", False)
    called = []
    monkeypatch.setattr(wr, "discover_weread", lambda *a, **k: called.append(1) or ([], {}))
    all_new = run_mod.discover_all({"wechat": [{"name": "哥飞"}], "bilibili": []},
                                   {"sources": {}})
    assert called == [] and all_new == []


def test_run_discover_all_weread_enabled(monkeypatch):
    monkeypatch.setattr(run_mod, "WEREAD_SOURCE_ENABLED", True)
    item = {"source": "wechat", "route": "article", "url": "https://mp.weixin.qq.com/s/t",
            "title": "T", "mp_name": "哥飞"}
    captured = {}

    def fake_discover(state, entries, session=None, pages=None):
        captured["entries"] = entries
        return [item], {"ok": 1, "new": 1, "captcha": False, "errors": [], "skipped": [],
                        "baseline": []}

    monkeypatch.setattr(wr, "discover_weread", fake_discover)
    monkeypatch.setattr(wr, "resolve_book_ids", lambda subs: [
        {"name": "哥飞", "book_id": "MP_WXS_2399233620", "category": ""}])
    all_new = run_mod.discover_all({"wechat": [{"name": "哥飞"}], "bilibili": []},
                                   {"sources": {}})
    assert captured["entries"][0]["book_id"] == "MP_WXS_2399233620"
    assert all_new == [item]


def test_run_discover_all_weread_skipped_in_backfill(monkeypatch):
    monkeypatch.setattr(run_mod, "WEREAD_SOURCE_ENABLED", True)
    monkeypatch.setenv("WECHAT_BACKFILL", "1")
    called = []
    monkeypatch.setattr(wr, "discover_weread", lambda *a, **k: called.append(1) or ([], {}))
    run_mod.discover_all({"wechat": [{"name": "哥飞"}], "bilibili": []}, {"sources": {}})
    assert called == []


# ---------------------------------------------------------------------------
# 时间窗 / 扫码续期 / 历史补全
# ---------------------------------------------------------------------------

def test_discover_window_filters_by_publish_time(monkeypatch):
    """30 天窗语义：日更号窗口内全要、窗口外不翻；周更号窗口内几篇抓几篇。"""
    now = int(time.time())
    monkeypatch.setattr(wr, "LIST_GAP", 0)
    monkeypatch.setattr(wr, "WEREAD_WINDOW_DAYS", 7)
    state = {"sources": {"weread:MP_WXS_100": {"seen": ["MP_WXS_100_dummy"]}}}
    resp = {"reviews": [{"subReviews": [
        _review("MP_WXS_100_new1", "新文", now - 3600),
        _review("MP_WXS_100_old1", "一个月前", now - 86400 * 30),
    ]}]}
    page = FakePage(responses=[resp])       # 第 2 次取页拿 {} → empty → 翻页正常终止
    items, health = wr.discover_weread(state, _entries(1), session=FakeSession(page))
    assert [it["id"] for it in items] == ["MP_WXS_100_new1"]   # 窗口外被过滤
    assert health["new"] == 1 and health["ok"] == 1


def test_discover_relogin_retries_account(monkeypatch):
    """登录态错误 → 自动弹码 → 扫码成功 → 重试本号并产出条目（页面内 cookie 自动生效）。"""
    now = int(time.time())
    monkeypatch.setattr(wr, "LIST_GAP", 0)
    monkeypatch.setattr(wr, "WEREAD_WINDOW_DAYS", 0)
    monkeypatch.setattr(wr, "WEREAD_AUTO_RELOGIN", True)
    monkeypatch.setattr(wr, "WEREAD_RELOGIN_WAIT", 1)
    calls = []

    def fake_relogin(session, timeout, qr_path=None):
        calls.append(timeout)
        return True

    monkeypatch.setattr(wr, "trigger_weread_relogin", fake_relogin)
    state = {"sources": {"weread:MP_WXS_100": {"seen": ["MP_WXS_100_dummy"]}}}
    page = FakePage(responses=[{"errCode": -2012},                # 第 1 次：登录失效
                               _resp(["MP_WXS_100_fresh"], ts=now)])  # 重试：成功
    items, health = wr.discover_weread(state, _entries(1), session=FakeSession(page))
    assert calls == [1] and health["relogin"] is True
    assert [it["id"] for it in items] == ["MP_WXS_100_fresh"]
    assert health["ok"] == 1 and health["errors"] == []


def test_discover_relogin_failure_reports(monkeypatch):
    """扫码超时/失败：记错误停手，剩余号跳过（不硬闯）。"""
    monkeypatch.setattr(wr, "LIST_GAP", 0)
    monkeypatch.setattr(wr, "WEREAD_WINDOW_DAYS", 0)
    monkeypatch.setattr(wr, "WEREAD_AUTO_RELOGIN", True)
    monkeypatch.setattr(wr, "WEREAD_RELOGIN_WAIT", 1)
    monkeypatch.setattr(wr, "trigger_weread_relogin", lambda *a, **k: False)
    state = {"sources": {"weread:MP_WXS_100": {"seen": ["MP_WXS_100_dummy"]}}}
    page = FakePage(responses=[{"errCode": -2041}])
    items, health = wr.discover_weread(state, _entries(2), session=FakeSession(page))
    assert items == [] and len(page.fetch_apis) == 1
    assert health["relogin"] is False and health["errors"][0][1] == "auth"
    assert health["skipped"] == ["号1"]


def test_backfill_collects_until_since(monkeypatch):
    """补全语义：since 之内未抓的全入队；翻到早于 since 的页即停（reached_since）。"""
    now = int(time.time())
    monkeypatch.setattr(wr, "LIST_GAP", 0)
    monkeypatch.setattr(wr, "WEREAD_WINDOW_DAYS", 0)
    state = {"sources": {"weread:MP_WXS_100": {"seen": ["MP_WXS_100_dummy"]}}}
    resp = {"reviews": [{"subReviews": [
        _review("MP_WXS_100_bf1", "十天内", now - 86400 * 10),
        _review("MP_WXS_100_bf2", "两个月前", now - 86400 * 60),
    ]}]}
    page = FakePage(responses=[resp])
    since_ts = now - 86400 * 30
    items, health = wr.discover_weread_backfill(state, _entries(1), since_ts,
                                                max_pages=5, session=FakeSession(page))
    assert [it["id"] for it in items] == ["MP_WXS_100_bf1"]   # 早于 since 的不入队
    assert health["reached_since"] is True and health["pages"]["号0"] == 1
    seen_now = state["sources"]["weread:MP_WXS_100"]["seen"]
    assert "MP_WXS_100_bf2" in seen_now     # 已翻到的旧文标 seen 防重扫


def test_backfill_page_cap_reports_continue(monkeypatch):
    """页数上限截断未到 since：reached_since=False，再跑一次即续批。"""
    now = int(time.time())
    monkeypatch.setattr(wr, "LIST_GAP", 0)
    state = {"sources": {"weread:MP_WXS_100": {"seen": []}}}
    # 每次取页都返回同一批 20 条（FakePage 弹尽后返回 {} → 但这里让 responses 循环）
    resp = {"reviews": [{"subReviews": [
        _review(f"MP_WXS_100_p{i}", "T", now - 86400 * (i + 1)) for i in range(20)]}]}
    page = FakePage(responses=[resp, resp])
    items, health = wr.discover_weread_backfill(state, _entries(1), now - 86400 * 90,
                                                max_pages=2, session=FakeSession(page))
    assert health["reached_since"] is False
    assert health["pages"]["号0"] == 2 and health["new"] == 20  # 第 2 页同批 rid 被 seen 去重


# ---------------------------------------------------------------------------
# 单日配额熔断
# ---------------------------------------------------------------------------

def test_quota_record_and_daily_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "QUOTA_PATH", str(tmp_path / "quota.json"))
    monkeypatch.setattr(wr, "WEREAD_DAILY_QUOTA", 25)
    monkeypatch.setattr(wr, "WEREAD_HOURLY_QUOTA", 6)
    assert wr.quota_remaining() == (25, 6) and wr.quota_remaining_n() == 6  # 起始瓶颈=小时层
    wr.quota_record(3)
    assert wr.quota_today() == 3
    assert wr.quota_remaining() == (22, 3)          # 日层 25-3，小时层 6-3
    assert wr.quota_remaining_n() == 3              # 瓶颈在小时层
    # 跨天重置：把台账日期改成昨天
    q = json.load(open(tmp_path / "quota.json", encoding="utf-8"))
    q["date"] = "2000-01-01"
    json.dump(q, open(tmp_path / "quota.json", "w", encoding="utf-8"))
    # 跨天只清日层；小时层独立按自然小时清零（当前小时计数 3 仍在）
    assert wr.quota_remaining() == (25, 3) and wr.quota_remaining_n() == 3


def test_quota_hourly_layer(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "QUOTA_PATH", str(tmp_path / "quota.json"))
    monkeypatch.setattr(wr, "WEREAD_DAILY_QUOTA", 25)
    monkeypatch.setattr(wr, "WEREAD_HOURLY_QUOTA", 6)
    wr.quota_record(6)
    assert wr.quota_remaining_n() == 0 and wr.quota_today() == 6   # 日层未满，小时层到线
    msg = wr.quota_block_message()
    assert "小时上限" in msg and "整点" in msg
    # 跨小时重置：小时串改成上一小时 → 小时层恢复，日层仍计数
    q = json.load(open(tmp_path / "quota.json", encoding="utf-8"))
    q["hour"] = "2000-01-01 00"
    json.dump(q, open(tmp_path / "quota.json", "w", encoding="utf-8"))
    assert wr.quota_this_hour() == 0 and wr.quota_remaining_n() == 6
    assert wr.quota_today() == 6                    # 日层不受跨小时影响


def test_fetch_raises_when_quota_exhausted(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "QUOTA_PATH", str(tmp_path / "quota.json"))
    monkeypatch.setattr(wr, "WEREAD_DAILY_QUOTA", 5)
    wr.quota_record(5)
    lister = wr.WereadLister(FakeSession(FakePage()))
    with pytest.raises(wr.WereadQuotaExhausted):
        lister.fetch_list_raw("MP_WXS_100", 0)


def test_discover_skips_when_quota_exhausted(tmp_path, monkeypatch):
    """多源场景熔断：公众号源整体跳过、零请求、健康度带任务消息。"""
    monkeypatch.setattr(wr, "QUOTA_PATH", str(tmp_path / "quota.json"))
    monkeypatch.setattr(wr, "WEREAD_DAILY_QUOTA", 16)
    monkeypatch.setattr(wr, "WEREAD_HOURLY_QUOTA", 100)   # 隔离小时层，验日层消息
    wr.quota_record(16)
    page = FakePage()
    items, health = wr.discover_weread({"sources": {}}, _entries(2),
                                       session=FakeSession(page))
    assert items == [] and health["quota_exhausted"] is True
    assert page.fetch_apis == [] and health["skipped"] == ["号0", "号1"]
    assert "单日上限" in wr.format_health(health)


def test_backfill_stops_when_quota_exhausted_midway(tmp_path, monkeypatch):
    """补全场景熔断：中途到线停止续批，reached_since=False（明天再跑续批）。"""
    monkeypatch.setattr(wr, "QUOTA_PATH", str(tmp_path / "quota.json"))
    monkeypatch.setattr(wr, "WEREAD_DAILY_QUOTA", 16)
    monkeypatch.setattr(wr, "WEREAD_HOURLY_QUOTA", 6)
    monkeypatch.setattr(wr, "LIST_GAP", 0)
    wr.quota_record(5)                       # 小时层剩 1：第 1 页成功，第 2 页熔断
    state = {"sources": {"weread:MP_WXS_100": {"seen": []}}}
    now = int(time.time())
    r1 = {"reviews": [{"subReviews": [_review(f"MP_WXS_100_q{i}", "T", now - 86400)
                                       for i in range(20)]}]}
    page = FakePage(responses=[r1])          # 第 2 页时 FakePage 返回 {}，但配额已尽先熔断
    items, health = wr.discover_weread_backfill(state, _entries(1), now - 86400 * 30,
                                                max_pages=5, session=FakeSession(page))
    assert health["quota_exhausted"] is True and health["reached_since"] is False
    assert len(items) == 20 and len(page.fetch_apis) == 1
    assert "上限" in wr.format_health(health) and "熔断" in wr.format_health(health)


# ---------------------------------------------------------------------------
# 连环码高危熔断
# ---------------------------------------------------------------------------

def test_captcha_serial_breaker(tmp_path, monkeypatch):
    """同日第 2 次验证码提交 = 高危：置熔断、请求闸门拒发；跨天事件清零。"""
    monkeypatch.setattr(wr, "WEREAD_CAPTCHA_SERIAL_LIMIT", 2)
    monkeypatch.setattr(wr, "WEREAD_RISK_COOLDOWN_HOURS", 12)
    assert wr.record_captcha_event() is None            # 第 1 次：正常放行
    assert wr.weread_block_message() is None
    warn = wr.record_captcha_event()                     # 第 2 次：连环码 → 熔断
    assert warn and "高危风控" in warn
    assert wr.risk_blocked_seconds() > 0
    lister = wr.WereadLister(FakeSession(FakePage()))
    with pytest.raises(wr.WereadQuotaExhausted, match="高危风控"):
        lister.fetch_list_raw("MP_WXS_100", 0)
    # 跨天：事件清零，熔断与配额一并复位
    q = json.load(open(tmp_path / "quota.json", encoding="utf-8"))
    q["date"] = "2000-01-01"
    q["hour"] = "2000-01-01 00"
    q.pop("risk_until")
    json.dump(q, open(tmp_path / "quota.json", "w", encoding="utf-8"))
    assert wr.weread_block_message() is None


def test_captcha_breaker_expires(tmp_path, monkeypatch):
    """熔断到期自动恢复（risk_until 过期后闸门放行）。"""
    monkeypatch.setattr(wr, "WEREAD_CAPTCHA_SERIAL_LIMIT", 2)
    monkeypatch.setattr(wr, "WEREAD_RISK_COOLDOWN_HOURS", 12)
    wr.record_captcha_event()
    wr.record_captcha_event()
    assert wr.weread_block_message() is not None
    q = json.load(open(tmp_path / "quota.json", encoding="utf-8"))
    q["risk_until"] = time.time() - 1
    json.dump(q, open(tmp_path / "quota.json", "w", encoding="utf-8"))
    assert wr.weread_block_message() is None


def test_discover_blocked_by_risk_breaker(tmp_path, monkeypatch):
    """熔断期内发现轮次：零请求、全部跳过、健康度带高危消息。"""
    monkeypatch.setattr(wr, "WEREAD_CAPTCHA_SERIAL_LIMIT", 2)
    wr.record_captcha_event()
    monkeypatch.setattr(wr, "WEREAD_CAPTCHA_SERIAL_LIMIT", 2)
    wr.record_captcha_event()
    page = FakePage()
    items, health = wr.discover_weread({"sources": {}}, _entries(1),
                                       session=FakeSession(page))
    assert items == [] and page.fetch_apis == []
    assert "高危风控" in wr.format_health(health)
