"""重做批次元数据闸（2026-10-26 实测事故的回归）。

事故：为验证「修订 → 回审」路径手搓了单条重做队列，漏了 `url`、author 也按文件夹猜错。
后果：新稿把真实来源链接写成「【无来源链接】」、作者也改成错的；而 **T8 只比内容缺陷**，
于是这份元数据更差的稿被判 `improved` 并替换了旧稿（已从备份回滚）。

这道闸的语义：**旧稿有真实来源链接、批次条目却没带 url → 与源匹配存疑同级，单列待人工核对**。
不是新增门禁、不拦落盘，只在批次生成时把可疑条目挑出来。
"""
from scripts import build_resum_batches as B


def test_old_note_with_link_and_item_without_url_is_suspect():
    note = "#tag\n\n**来源链接**：[原文链接](https://www.bilibili.com/video/BV1xx)\n\n**作者**：某UP\n\n正文\n"
    assert B.meta_suspect(note, "") == "old_has_url_new_has_none"


def test_item_with_url_is_never_suspect():
    note = "**来源链接**：[原文链接](https://www.bilibili.com/video/BV1xx)\n\n正文\n"
    assert B.meta_suspect(note, "https://www.bilibili.com/video/BV1xx") == ""


def test_old_note_without_link_is_fine():
    note = "**来源链接**：【无来源链接】\n\n正文\n"
    assert B.meta_suspect(note, "") == ""


def test_empty_note_is_fine():
    assert B.meta_suspect("", "") == ""
