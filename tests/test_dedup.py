"""dedup URL 归一化与短链展开（1.4，PLAN-20260906 P1-1 顺带落地）。

P1-1：归一化只排序不剥离可变 query → 同源多 key（公众号 chksm/encKey、
B站 vd_source、b23.tv 短链与长链并存）。修复：站点参数白名单只保留稳定
键参数（分P p= 是不同内容必须保留）；b23.tv 先展开再归一化（失败降级原样）。
"""
import requests

from articles import dedup


def test_normalize_url_strips_tracking_query():
    """B站：vd_source/spm 等追踪参数剥离（同视频同键）；分P p= 保留为不同键。"""
    base = "https://www.bilibili.com/video/BV1xx411c7mD/"
    a = dedup._normalize_url(base + "?vd_source=aaa&spm_id_from=333")
    assert a == dedup._normalize_url(base)
    p1 = dedup._normalize_url(base + "?p=1")
    p3 = dedup._normalize_url(base + "?p=3&vd_source=aaa")
    assert p1 != p3
    assert p1 != a


def test_normalize_url_wechat_keeps_stable_keys():
    """公众号：chksm/signature 等可变参数剥离；__biz/mid/idx/sn 差异仍不同键。"""
    u1 = ("https://mp.weixin.qq.com/s?__biz=MzA=&mid=1&idx=1&sn=x"
          "&chksm=aaa&signature=bbb")
    u2 = ("https://mp.weixin.qq.com/s?__biz=MzA=&mid=1&idx=1&sn=x"
          "&chksm=ccc&signature=ddd")
    assert dedup._normalize_url(u1) == dedup._normalize_url(u2)
    u3 = u1.replace("idx=1", "idx=2")
    assert dedup._normalize_url(u1) != dedup._normalize_url(u3)


def test_expand_url_b23tv(monkeypatch):
    """b23.tv 短链：展开为长链入键；失败降级原样；查询侧自动展开命中长链记录。"""
    long_url = "https://www.bilibili.com/video/BV1xx411c7mD?p=3"

    class FakeResp:
        url = long_url

    monkeypatch.setattr(dedup, "_B23_CACHE", {}, raising=False)
    monkeypatch.setattr(requests, "head", lambda *a, **k: FakeResp(),
                        raising=False)
    assert dedup.expand_url("https://b23.tv/abc") == long_url

    def boom(*a, **k):
        raise OSError("net down")

    monkeypatch.setattr(requests, "head", boom, raising=False)
    assert dedup.expand_url("https://b23.tv/xyz") == "https://b23.tv/xyz"

    dedup.mark_summarized(url=long_url, title="t", filename="f.md")
    rec = dedup.is_summarized(url="https://b23.tv/abc")
    assert rec and rec["source_url"] == long_url
