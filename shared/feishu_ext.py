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

# 滚动容器按优先级探测（第一个命中的即为飞书各类文档的正文承载容器）。
# 2026-10-26 修正：飞书妙记(minutes)的转写列表既不在 `.bear-web-x-container`、
# 也不是 window 滚动，而是 React 虚拟列表 `.rc-virtual-list-holder`（overflow:hidden）。
# 旧实现缺这一档 → 滚 window 无效，只拿到首屏几段（实测 1h27m 的会议仅 1093 字）。
_CONTAINER_JS = """() => {
    for (const sel of ['.bear-web-x-container', '.rc-virtual-list-holder']) {
        const el = document.querySelector(sel);
        if (el) return sel;
    }
    return null;
}"""


def is_feishu_doc_url(url: str) -> bool:
    """判定是否飞书「正文型」链接：wiki / docx / doc / 妙记 minutes / 短链 s。

    不含飞书首页、表单等非文档页。
    2026-10-26 补 `minutes`：妙记（会议录制+服务端转写）此前不匹配 →
    `articles/fetch.py` 的分流判定落空，退化成普通 HTTP 下载；而飞书是 SPA，
    HTTP 只能拿到空壳 HTML → 表现为「抓不到正文 / 疑似死源」。
    补上后妙记与 wiki/docx 一样走 CDP + `collect_full_text`。
    """
    import re
    # 末尾要求有 token：`/minutes/` 这类空路径是列表页不是文档。
    return bool(re.search(
        r"https?://[^\s)\]]*(?:feishu\.cn|larksuite\.com)"
        r"/(?:wiki|docx|doc|minutes|s)/[^\s/?]+",
        url or ""))


def collect_full_text(page) -> tuple[str, str]:
    """增量滚动收集整页文本；返回 (title, 合并正文)。

    飞书 wiki/docx 的滚动容器是 div.bear-web-x-container（不是 window），
    飞书妙记(minutes) 的转写列表是 div.rc-virtual-list-holder（overflow:hidden），
    两者均按视口虚拟化渲染（视口外内容卸载）。先按 `_CONTAINER_JS` 优先级定位容器，
    逐段滚动该容器，收集每视口 innerText 快照，按「首见顺序」去重合并；
    无容器命中的页面回退 window 滚动（同样逐快照收集）。
    """
    title = page.title()
    sel = page.evaluate(_CONTAINER_JS)
    lines: list[str] = []
    seen: set[str] = set()

    def snapshot() -> None:
        text = page.evaluate(
            """(sel) => {
                const c = sel ? document.querySelector(sel) : null;
                return (c || document.body).innerText;
            }""",
            sel,
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
            """([y, sel]) => {
                const c = sel ? document.querySelector(sel) : null;
                if (c) c.scrollTop = y; else window.scrollTo(0, y);
            }""",
            [y, sel],
        )
        page.wait_for_timeout(SETTLE_MS)
        snapshot()
        height = page.evaluate(
            """(sel) => {
                const c = sel ? document.querySelector(sel) : null;
                return c ? c.scrollHeight : document.body.scrollHeight;
            }""",
            sel,
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
