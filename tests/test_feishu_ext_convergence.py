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
