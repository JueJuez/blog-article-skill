# -*- coding: utf-8 -*-
"""飞书 wiki/docx 懒加载全文抓取的单点实现（2026-10-25 自 scripts/feishu_ext_refetch.py 下沉）。

背景：飞书 wiki/docx 的滚动容器是 div.bear-web-x-container（不是 window），正文按
视口虚拟化渲染（视口外内容卸载）。旧 scys 批量抓取用 window.scrollTo 滚两轮取一次
innerText，实测一批 32 条外链全部只抓到目录+开头。本模块是唯一的正确实现，消费方：

- ``scripts/scys_batch_fetch.py``（外链抓取，永久修复）
- ``scripts/feishu_ext_refetch.py`` / ``scripts/refetch_feishu_ext_batch.py``（存量重抓）
- ``articles/fetch.py``（单篇飞书链接直接喂文章管线时的 CDP 分流）
"""
from __future__ import annotations

STEP_PX = 900
SETTLE_MS = 550
MAX_STEPS = 600


def is_feishu_doc_url(url: str) -> bool:
    """判定是否飞书 wiki/docx 文档链接（不含飞书首页/表单等非文档页）。"""
    import re
    return bool(re.search(
        r"https?://[^\s)\]]*(?:feishu\.cn|larksuite\.com)/(?:wiki|docx|doc|s)/", url or ""))


def collect_full_text(page) -> tuple[str, str]:
    """增量滚动收集整页文本；返回 (title, 合并正文)。

    飞书 wiki/docx 的滚动容器是 div.bear-web-x-container（不是 window），
    逐段滚动该容器，收集每视口 innerText 快照，按「首见顺序」去重合并；
    无 bear 容器的页面自动回退 window 滚动（同样逐快照收集）。
    """
    title = page.title()
    lines: list[str] = []
    seen: set[str] = set()

    def snapshot() -> None:
        text = page.evaluate(
            """() => {
                const c = document.querySelector('.bear-web-x-container');
                return (c || document.body).innerText;
            }"""
        ) or ""
        for ln in text.splitlines():
            s = ln.strip()
            if len(s) >= 2 and s not in seen:
                seen.add(s)
                lines.append(s)

    stable_rounds = 0
    last_height = -1
    y = 0
    for _ in range(MAX_STEPS):
        page.evaluate(
            """(y) => {
                const c = document.querySelector('.bear-web-x-container');
                if (c) c.scrollTop = y; else window.scrollTo(0, y);
            }""",
            y,
        )
        page.wait_for_timeout(SETTLE_MS)
        snapshot()
        height = page.evaluate(
            "() => { const c = document.querySelector('.bear-web-x-container');"
            " return c ? c.scrollHeight : document.body.scrollHeight; }"
        )
        if height == last_height and y >= height:
            stable_rounds += 1
            if stable_rounds >= 2:
                break
        else:
            stable_rounds = 0
        last_height = height
        y = min(y + STEP_PX, height) if y < height else height
    return title, "\n".join(lines)
