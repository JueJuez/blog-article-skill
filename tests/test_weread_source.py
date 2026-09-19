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
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from monitors import weread as wr  # noqa: E402
from monitors import run as run_mod  # noqa: E402


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
    state = {"sources": {"weread:MP_WXS_100": {"seen": ["MP_WXS_1_a"]}}}
    page = FakePage(responses=[_resp([f"MP_WXS_1_{c}" for c in "abc"])])
    items, health = wr.discover_weread(state, _entries(1), session=FakeSession(page))
    assert [it["id"] for it in items] == ["MP_WXS_1_b", "MP_WXS_1_c"]
    assert health["new"] == 2 and health["ok"] == 1


def test_discover_auth_error_stops_remaining_accounts(monkeypatch):
    """-2041 → 观测上报路径：记录错误码、停手、剩余号跳过且零请求。"""
    monkeypatch.setattr(wr, "LIST_GAP", 0)
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
