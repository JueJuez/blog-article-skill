"""3.2 scys 切换：filter_todo 之后追加登记表预查。

URL 键与 articles.dedup 同源：sbf.ARTICLE_URL.format(topic_id)；
已总结帖在「待抓列表」层剔除，不发请求。
"""
from articles import dedup
import scys_batch_fetch as sbf


def _item(tid: int, title: str) -> dict:
    return {"topicId": tid, "title": title}


def test_registry_filter_empty_returns_empty():
    kept, skipped = sbf.registry_filter([])
    assert kept == []
    assert skipped == []


def test_registry_filter_mixed():
    dedup.mark_summarized(
        url=sbf.ARTICLE_URL.format(topic_id="9001"),
        title="已总结",
        filename="a.md",
    )
    items = [_item(9001, "旧帖"), _item(9002, "新帖")]

    kept, skipped = sbf.registry_filter(items)

    assert [it["topicId"] for it in kept] == [9002]
    assert [it["topicId"] for it in skipped] == [9001]


def test_registry_filter_no_hits():
    items = [_item(9101, "甲"), _item(9102, "乙")]

    kept, skipped = sbf.registry_filter(items)

    assert [it["topicId"] for it in kept] == [9101, 9102]
    assert skipped == []
