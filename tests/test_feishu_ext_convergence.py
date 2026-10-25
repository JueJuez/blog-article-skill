# -*- coding: utf-8 -*-
"""飞书外链抓取收敛（2026-10-25）。

守住两件事：
1. collect_full_text 单点实现在 shared.feishu_ext，三个消费方（scys 批量外链、
   存量重抓、articles 单篇飞书分流）都从它导入，不存在各自的 window.scrollTo 副本；
2. 飞书文档 URL 判定正确（wiki/docx 命中，首页/其它站不命中）。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from shared.feishu_ext import collect_full_text, is_feishu_doc_url  # noqa: E402


def test_feishu_doc_url_matching():
    assert is_feishu_doc_url("https://zhizhe888.feishu.cn/docx/Z40jdqs7woO8sxxDtgFcG6sDnVb")
    assert is_feishu_doc_url("https://xxx.feishu.cn/wiki/AbC123")
    assert is_feishu_doc_url("http://a.larksuite.com/docx/x1")
    assert not is_feishu_doc_url("https://www.feishu.cn/")          # 首页非文档
    assert not is_feishu_doc_url("https://scys.com/articleDetail/xq_topic/123")
    assert not is_feishu_doc_url("")
    assert not is_feishu_doc_url(None)


def test_collect_full_text_is_single_source():
    # 三个消费方都应从 shared.feishu_ext 导入同一实现（函数对象一致）
    import importlib
    scys = importlib.import_module("scys_batch_fetch")
    refetch = importlib.import_module("feishu_ext_refetch")
    assert scys.collect_full_text is collect_full_text
    assert refetch.collect_full_text is collect_full_text
    import articles.fetch as af
    assert af.is_feishu_doc_url is not None  # 分流判定已接线


def test_articles_fetch_wired():
    from articles import fetch as af
    assert af.is_feishu_doc_url("https://a.feishu.cn/docx/x")
    # fetch_web_content 里有飞书分流分支（防静默退化成普通 HTTP 下载）
    src = Path(af.__file__).read_text(encoding="utf-8")
    assert "is_feishu_doc_url(url)" in src
    assert "_feishu_cdp_fetch" in src


# ---- 飞书妙记(minutes) 虚拟列表容器收敛（2026-10-26 修复）----
# 背景：妙记的转写正文不在 `.bear-web-x-container`，而是 React 虚拟列表
# `.rc-virtual-list-holder`（overflow:hidden）。旧实现只认 bear 容器，缺这一档就
# 退回 window.scrollTo —— 窗口压根不滚，只拿到首屏几段。
# 实测：1h27m 的会议转写只抓到 1093 字（应约 2 万字），补这一档后 19623 字。
BEAR = ".bear-web-x-container"
VLIST = ".rc-virtual-list-holder"


class _FakePage:
    """最小 Page 替身：按 JS 片段特征分派返回值，并记录滚动/快照调用。"""

    def __init__(self, containers, snapshots, max_h=1500):
        self.containers = containers          # 页面里存在的容器选择器（按优先级测试）
        self.snapshots = snapshots            # 每次 innerText 快照返回的内容
        self.max_h = max_h
        self.scroll_sels = []                 # 每次滚动传进去的 sel
        self.snapshot_sels = []               # 每次取文本传进去的 sel
        self._i = 0

    def title(self):
        return "fake-title"

    def wait_for_timeout(self, _ms):
        return None

    def evaluate(self, js, arg=None):
        if "for (const sel of" in js:                      # 容器探测
            for sel in self.containers:
                if sel in js:
                    return sel
            return None
        if "scrollTop = y" in js or "window.scrollTo" in js:
            self.scroll_sels.append(arg[1] if isinstance(arg, list) else arg)
            return None
        if "scrollHeight" in js:                           # 高度不变即到底
            return self.max_h
        if "innerText" in js:                              # 快照
            self.snapshot_sels.append(arg)
            i = min(self._i, len(self.snapshots) - 1)
            self._i += 1
            return self.snapshots[i]
        return None


def _run(containers, snapshots, max_h=900):
    page = _FakePage(containers, snapshots, max_h)
    title, body = collect_full_text(page)
    return page, title, body


def test_minutes_virtual_list_is_scrolled():
    """只有 rc-virtual-list-holder（妙记）时，必须滚它自身而不是 window。"""
    page, title, body = _run([VLIST], ["第一段。", "第二段。", "第三段。"])
    assert title == "fake-title"
    # 滚动与取文本都带着该容器 → 没有退化成 window.scrollTo
    assert VLIST in page.scroll_sels
    assert VLIST in page.snapshot_sels
    # 三张快照按首见顺序合并
    assert body.splitlines() == ["第一段。", "第二段。", "第三段。"]


def test_bear_container_still_wins():
    """wiki/docx 场景：两个容器都在时，仍优先 .bear-web-x-container（向后兼容）。"""
    page, _t, body = _run([BEAR, VLIST], ["A段", "A段", "B段"])
    assert set(page.scroll_sels) == {BEAR}
    assert set(page.snapshot_sels) == {BEAR}
    assert body.splitlines() == ["A段", "B段"]          # 重复行只留首次


def test_no_container_falls_back_to_window():
    """两个容器都不在时回退 window 滚动（sel 为 None），历史行为不变。"""
    page, _t, _body = _run([], [" X 段 "])
    assert page.scroll_sels and page.scroll_sels[0] is None
    assert page.snapshot_sels[0] is None


def test_feishu_minutes_is_treated_as_doc():
    """妙记(minutes) 必须命中判定（2026-10-26 修复）。

    否则 `articles/fetch.py` 的分流判定落空 → 退化成普通 HTTP 下载；
    飞书是 SPA，HTTP 只拿得到空壳 HTML → 表现为「抓不到正文 / 疑似死源」，
    即使事实上转写全文就在页面上。
    """
    assert is_feishu_doc_url(
        "https://shengcaiyoushu01.feishu.cn/minutes/obcnadk7rh7gfper83f9d7i5?from=from_copylink")
    assert is_feishu_doc_url("https://x.feishu.cn/minutes/abc123")
    assert is_feishu_doc_url("http://a.larksuite.com/minutes/x1")
    # 列表页 / 空路径不属于正文文档
    assert not is_feishu_doc_url("https://www.feishu.cn/minutes/")
    assert not is_feishu_doc_url("https://www.feishu.cn/docx/")


def test_container_selector_list_contains_minutes_fallback():
    """防 haunting 回归：容器优先级表里必须同时有 bear 与 minutes 虚拟列表。"""
    import inspect
    from shared import feishu_ext as m
    src = inspect.getsource(m)
    assert BEAR in src and VLIST in src
    # 两者必须在同一个优先级表达式里，而不是散落两处各滚各的
    assert "for (const sel of" in src
