"""D2 图片信息缺口台账（PLAN-20261026 §5）测试。

保护的四条约束：
1. 区分「有图」与「已读图」——alt 有字不算读过；
2. 取不到图片信息（CDP 文本通道）**不许**冒充「无图片」；
3. 缺口只进台账/派单/审核元数据，**不进笔记正文**；
4. 纯文本源零噪声（派单提示为空，生产 prompt 一个字节不变）。
"""
import json
import os

import pytest

from shared import image_inventory as IMG


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(IMG, "STORE_DIR", str(tmp_path / "img"))
    return str(tmp_path / "img")


def test_text_only_source_is_no_images_and_hint_is_empty(store):
    inv = IMG.record("https://example.com/a", html="<html><body><p>纯文字</p></body></html>")
    assert inv["state"] == IMG.STATE_NO_IMAGES
    assert inv["total"] == 0
    assert IMG.dispatch_hint(inv) == "", "纯文本源不许往 prompt 里塞东西"
    assert IMG.review_reasons(inv) == []


def test_images_found_but_not_read(store):
    html = ('<article><p>正文</p>'
            '<img src="/i/chart.png" alt="销售趋势">'
            '<figure><img src="https://cdn.example.com/xmind.png" alt=""><figcaption>图 3 业务结构</figcaption></figure>'
            '</article>')
    inv = IMG.record("https://example.com/a", html=html)
    assert inv["state"] == IMG.STATE_NOT_READ
    assert inv["total"] == 2 and inv["content_total"] == 2 and inv["read"] == 0
    assert inv["images"][0]["src"] == "https://example.com/i/chart.png", "相对地址要补全"
    assert inv["images"][0]["alt"] == "销售趋势"
    assert inv["images"][1]["caption"] == "图 3 业务结构"
    hint = IMG.dispatch_hint(inv)
    assert "未读取" in hint and "不得凭 alt" in hint
    assert IMG.review_reasons(inv) == ["image_unread:2"]


def test_decorative_images_only_do_not_raise_alarm(store):
    html = ('<header><img src="/logo.png" alt="站点 logo"></header>'
            '<p>正文</p><img src="/img/x.gif" width="16" height="16">')
    inv = IMG.record("https://example.com/a", html=html)
    assert inv["state"] == IMG.STATE_DECORATIVE_ONLY
    assert inv["total"] == 2 and inv["content_total"] == 0
    assert inv["suspect_decorative"] == 2
    assert IMG.dispatch_hint(inv) == ""
    assert IMG.review_reasons(inv) == []


def test_missing_src_is_a_gap_not_clean(store):
    """图链接缺失本身就是缺口：不能因为取不到地址就当成「无图」。"""
    inv = IMG.record("https://example.com/a", html='<p>x</p><img alt="流程图">')
    assert inv["missing_src"] == 1
    assert inv["state"] == IMG.STATE_NOT_READ


def test_alt_with_text_does_not_count_as_read(store):
    inv = IMG.record("https://example.com/a", html='<img src="/i/a.png" alt="这里有 30% 增长">')
    assert inv["read"] == 0 and inv["state"] == IMG.STATE_NOT_READ
    assert "30%" not in IMG.dispatch_hint(inv), "alt 内容不许被当成已读结果带进派单"


def test_text_channel_never_claims_no_images(store):
    """CDP/字幕通道拿不到 HTML：状态必须是 unknown，提示要说「无法判断」，不许报无图。"""
    inv = IMG.record("https://scys.com/xq_topic/1", html="", kind="cdp_text_only")
    assert inv["state"] == IMG.STATE_UNKNOWN
    assert inv["html_available"] is False
    hint = IMG.dispatch_hint(inv)
    assert "无法判断" in hint and "未读取" in hint
    assert "无图片" not in hint
    assert IMG.review_reasons(inv) == ["image_unknown"]


def test_mark_read_moves_state_and_keeps_scope(store):
    url = "https://example.com/a"
    inv = IMG.record(url, html='<img src="/i/1.png"><img src="/i/2.png">')
    assert inv["state"] == IMG.STATE_NOT_READ

    inv = IMG.mark_read(url, "https://example.com/i/1.png", text="流程图文字", structure="",
                        method="multimodal_read")
    assert inv["state"] == IMG.STATE_PARTIAL
    assert inv["reads"][0]["method"] == "multimodal_read"
    assert inv["reads"][0]["ok"] is True

    inv = IMG.mark_read(url, "https://example.com/i/2.png", ok=False, error="image_unreachable")
    assert inv["state"] == IMG.STATE_PARTIAL, "失败的那张不算读到"
    assert inv["reads"][1]["ok"] is False


def test_all_read_and_failure_only(store):
    url = "https://example.com/b"
    IMG.record(url, html='<img src="/i/1.png">')
    inv = IMG.mark_read(url, "https://example.com/i/1.png", text="x", method="read")
    assert inv["state"] == IMG.STATE_READ
    assert "已读" in IMG.dispatch_hint(inv)
    assert IMG.review_reasons(inv) == []


def test_store_is_meta_only_and_never_touches_notes(store):
    """台账只落 notes/_meta/；返回值不携带任何「写正文」的副作用。"""
    inv = IMG.record("https://example.com/c", html='<img src="/i/a.png">')
    path = inv["store_path"]
    assert os.path.dirname(path) == store
    assert os.path.basename(path).endswith(".json")
    on_disk = json.load(open(path, encoding="utf-8"))
    assert on_disk["url"] == "https://example.com/c"
    assert on_disk["state"] == IMG.STATE_NOT_READ
    # 关键：模块不产出任何 markdown 片段给正文
    assert "markdown" not in inv and "note" not in inv


def test_record_never_throws_on_garbage(store):
    inv = IMG.record("https://example.com/d", html="<<<not html>>>")
    assert isinstance(inv, dict)
    assert inv.get("state")


# ---------------------------------------------------------------- 接线：派单提示 / 审核元数据

def test_summarize_content_appends_hint_only_when_gap(monkeypatch, tmp_path):
    from articles import main as am
    monkeypatch.setattr(IMG, "STORE_DIR", str(tmp_path / "img"))
    monkeypatch.setattr(am, "verify_note", lambda *a, **k: None)
    seen = {}

    def fake_ai(prompt, text):
        seen["prompt"] = prompt
        return {"content": "总结正文"}

    monkeypatch.setattr(am, "call_ai_summary_with_meta", fake_ai)

    gap_url = "https://example.com/gap"
    IMG.record(gap_url, html='<img src="/i/chart.png">')
    am.summarize_content("完整原文" * 50, url=gap_url, note_type="general")
    assert "【图片信息】" in seen["prompt"], "有图未读必须在派单里明说"

    clean_url = "https://example.com/clean"
    IMG.record(clean_url, html="<p>纯文字</p>")
    am.summarize_content("完整原文" * 50, url=clean_url, note_type="general")
    assert "【图片信息】" not in seen["prompt"], "纯文本源不许改 prompt"


def test_async_review_metadata_has_image_state_but_reasons_untouched(monkeypatch, tmp_path):
    """image_state 只进 extra：一旦塞进 reasons 就会触发入队、把抽样率放大。"""
    from articles import main as am
    from shared import async_review as AR
    monkeypatch.setattr(IMG, "STORE_DIR", str(tmp_path / "img"))
    monkeypatch.setattr(AR, "QUEUE", str(tmp_path / "q.jsonl"))
    monkeypatch.setattr(AR, "RESULT_DIR", str(tmp_path / "rev"))
    monkeypatch.setenv("NOTE_ASYNC_REVIEW", "all")

    url = "https://example.com/review"
    IMG.record(url, html='<img src="/i/a.png">')
    note = tmp_path / "n.md"
    note.write_text("# 标题\n\n正文\n", encoding="utf-8")
    am._enqueue_async_review(str(note), content=note.read_text(encoding="utf-8"),
                             source_text="源正文", note_type="general", url=url, title="标题")

    rows = AR.load(str(tmp_path / "q.jsonl"))
    assert rows, "all 模式下应已入队"
    rec = rows[0]
    assert rec["reasons"] == [], "图片状态不许变成入队理由"
    assert rec["image_state"] == IMG.STATE_NOT_READ
    assert rec["image_total"] == 1


def test_mark_read_matches_src_with_query_string(store):
    """CDN 的 src 常带 ?utm_source=；报告里回填时往往只给裸地址，仍要能对上。"""
    url = "https://e.com/post"
    IMG.record(url, html='<img src="https://cdn.e.com/a/chart.png?utm_source=feed">')
    inv = IMG.mark_read(url, "https://cdn.e.com/a/chart.png", text="图内文字", method="multimodal_read")
    assert inv["read"] == 1
    assert inv["state"] == IMG.STATE_READ


# ------------------------------------------------- CDP 通道：从 unknown 变成「能数图」（§5.3 第 2 步）

class _FakePage:
    """最小 CDP page 桩：只实现抓取路径实际用到的方法。"""

    def __init__(self, html="<html></html>", title="标题", content_error=None):
        self._html, self._title, self._content_error = html, title, content_error
        self.closed = False

    def goto(self, *a, **k):
        return None

    def wait_for_timeout(self, *_a):
        return None

    def title(self):
        return self._title

    def content(self):
        if self._content_error:
            raise self._content_error
        return self._html

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page

    def close(self):
        return None


def test_page_html_helper_returns_empty_on_failure():
    """DOM 快照是观测零件：取不到就返回空串，绝不能把抓取拖崩。"""
    from shared.cdp_session import page_html
    assert page_html(_FakePage(html="<img src='/a.png'>")) == "<img src='/a.png'>"
    assert page_html(_FakePage(content_error=RuntimeError("Target closed"))) == ""


def test_scys_cdp_channel_counts_images_instead_of_unknown(monkeypatch, tmp_path):
    """主源 scys 此前只能记 unknown_no_html（连有没有图都不知道）；补 DOM 快照后必须能数图。"""
    from articles import fetch as AF
    monkeypatch.setattr(IMG, "STORE_DIR", str(tmp_path / "img"))
    page = _FakePage(html='<article><p>正文</p><img src="/i/xmind.png" alt=""></article>')
    monkeypatch.setattr(AF, "_get_scys_session", lambda: _FakeSession(page))
    monkeypatch.setattr(AF, "_extract_body_scys", lambda p: "正文内容足够长" * 10)

    out = tmp_path / "scys_latest.md"
    AF._scys_cdp_fetch("https://scys.com/articleDetail/xq_topic/1", out_path=out)

    inv = IMG.load_for_url("https://scys.com/articleDetail/xq_topic/1", str(tmp_path / "img"))
    assert inv["state"] == IMG.STATE_NOT_READ, "拿到 DOM 就不能再报 unknown"
    assert inv["total"] == 1 and inv["content_total"] == 1
    assert out.read_text(encoding="utf-8").startswith("正文内容"), "记账不得改变正文产物"


def test_scys_cdp_channel_falls_back_to_unknown_when_dom_unavailable(monkeypatch, tmp_path):
    """DOM 取不到时退回 unknown（不冒充无图），且正文抓取照常成功。"""
    from articles import fetch as AF
    monkeypatch.setattr(IMG, "STORE_DIR", str(tmp_path / "img"))
    page = _FakePage(content_error=RuntimeError("Target page has been closed"))
    monkeypatch.setattr(AF, "_get_scys_session", lambda: _FakeSession(page))
    monkeypatch.setattr(AF, "_extract_body_scys", lambda p: "正文内容足够长" * 10)

    out = tmp_path / "scys_latest.md"
    res = AF._scys_cdp_fetch("https://scys.com/articleDetail/xq_topic/2", out_path=out)

    assert res["chars"] > 0, "图片台账失败不许影响抓取"
    inv = IMG.load_for_url("https://scys.com/articleDetail/xq_topic/2", str(tmp_path / "img"))
    assert inv["state"] == IMG.STATE_UNKNOWN
    assert inv["html_available"] is False
