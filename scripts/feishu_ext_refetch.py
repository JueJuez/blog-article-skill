"""飞书 wiki/docx 懒加载全文重抓：增量滚动逐步收集 innerText，按行去重合并。

背景：scys 批量抓取的 _fetch_external 只滚两次到底再取一次 innerText，飞书长文
虚拟化渲染（视口外内容卸载）导致只抓到目录+开头。本脚本逐步滚动，收集每个视口
快照的行，按「首见顺序」去重合并，逼近完整正文。

用法：
    python scripts/feishu_ext_refetch.py "<飞书URL>" <out.md>

前提：用户主 Chrome 已启 --remote-debugging-port（与 scys 抓取一致）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.feishu_ext import collect_full_text, STEP_PX, SETTLE_MS, MAX_STEPS  # noqa: E402,F401
from login_cdp_fetch import discover_chrome_devtools  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    url, out = argv[1], Path(argv[2])
    port, ws_path = discover_chrome_devtools()
    ws = f"ws://127.0.0.1:{port}{ws_path}"
    print(f"[1/3] ws endpoint = {ws}")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        print(f"[2/3] 打开 {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(4_000)
        title, body = collect_full_text(page)
        page.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    out.write_text(f"> 来源：{url}\n> 抓取时间：{stamp}（懒加载全文重抓）\n\n---\n\n{body}\n",
                   encoding="utf-8")
    print(f"[3/3] 写出 {out}（正文 {len(body)} 字符 / {len(body.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
