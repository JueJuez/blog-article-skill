# -*- coding: utf-8 -*-
"""login_persistent_fetch.py — 用 Playwright launch_persistent_context 接管用户主 Chrome 配置，
抓取需登录态的页面，**不需要 Chrome debug flag**。

与 scripts/login_cdp_fetch.py 的关系：
- login_cdp_fetch.py   ：connect_over_cdp（接管用户**已运行**的 Chrome，要求 debug flag）
- login_persistent_fetch.py（本文件）：launch_persistent_context（自己启**新 Chrome 用主 profile**，不要求 debug flag）

使用流程：
1. 完全退出 Chrome（任务栏右键→退出 / 任务管理器 kill chrome.exe）—— 否则 user-data-dir 被锁
2. 跑：python scripts/login_persistent_fetch.py "<URL>" [out.md]
3. Playwright 启新 headless Chrome 加载用户 profile → 抓页面 → 退出
4. 用户**重新打开**自己的 Chrome，所有标签/cookies/登录态原样保留（我们没动用户的 chrome.exe）

参考：videos/fetch.py::_bili_auto_extract_cookies 是同一思路（拿 cookie），本脚本扩展为抓页面正文。
"""
from __future__ import annotations

import os
import sys
import time
import json
import re
import subprocess
from pathlib import Path
from urllib.request import urlopen


CHROME_USER_DATA = os.path.join(
    os.environ.get("LOCALAPPDATA", r"C:\Users\O1830\AppData\Local"),
    "Google", "Chrome", "User Data",
)


def chrome_running() -> bool:
    """检查 chrome.exe 是否在跑（user-data-dir 是否被锁）。"""
    try:
        out = subprocess.run(
            ["tasklist"], capture_output=True, text=True, timeout=5,
            ).stdout.lower()
        return "chrome.exe" in out
    except Exception:
        return False


def kill_chrome() -> int:
    """强杀所有 chrome.exe，返回被杀的进程数（粗略）。"""
    r = subprocess.run(
        ["taskkill", "/F", "/IM", "chrome.exe", "/T"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = (r.stdout or "") + (r.stderr or "")
    return out.lower().count("成功")


def fetch(url: str, out_path: Path, *, headless: bool = True,
          selector: str | None = None, wait_ms: int = 8000) -> dict:
    """主流程：接管主 Chrome profile → 抓 URL 正文 → 落文件。"""
    from playwright.sync_api import sync_playwright  # type: ignore

    if not Path(CHROME_USER_DATA).is_dir():
        raise FileNotFoundError(
            f"未检测到 Chrome 用户目录 {CHROME_USER_DATA}；本脚本仅适用于本机已安装 Chrome 的 Windows。"
        )

    if chrome_running():
        raise RuntimeError(
            "Chrome 还在跑，user-data-dir 被锁。请先完全退出 Chrome：\n"
            "  - 任务栏右键 Chrome 图标 → 退出；或\n"
            "  - 任务管理器 → 结束所有 chrome.exe 进程；或\n"
            "  - 跑脚本时加 --kill-chrome（脚本会替你 taskkill）\n"
            "退出后再跑本脚本。"
        )

    print(f"[1/4] using profile {CHROME_USER_DATA}")
    print(f"[2/4] launching chromium (channel='chrome', headless={headless}) …")

    args_extra = ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=CHROME_USER_DATA,
            headless=headless,
            channel="chrome",
            args=args_extra,
            timeout=30_000,
        )
        try:
            page = ctx.new_page() if not ctx.pages else ctx.pages[0]
            print(f"[3/4] goto {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            if wait_ms:
                page.wait_for_timeout(wait_ms)
            title = page.title()
            if selector:
                try:
                    page.wait_for_selector(selector, timeout=10_000)
                except Exception:
                    pass
            body = ""
            for sel in [
                selector,
                ".article-content", ".article-detail", "#articleContent",
                ".topic-content", ".post-content", ".markdown-body",
                "article", "main", "body",
            ]:
                if not sel:
                    continue
                try:
                    el = page.query_selector(sel)
                    if el:
                        t = el.inner_text().strip()
                        if len(t) > len(body):
                            body = t
                except Exception:
                    continue
            if not body:
                body = page.evaluate("() => document.body.innerText")
        finally:
            ctx.close()

    login_markers = ["立即登录", "登录后查看", "请登录", "扫码登录",
                     "您还未登录", "成为会员", "开通会员", "订阅后"]
    hit = [m for m in login_markers if m in body]

    print(f"[4/4] title = {title!r}  body_chars = {len(body)}  "
          f"login_wall = {hit or '无'}")

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        f"# {title}\n\n"
        f"> 来源：{url}\n"
        f"> 抓取时间：{ts}\n"
        f"> 渠道：login_persistent_fetch（接管主 Chrome profile · 登录态继承）\n\n"
        f"---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    print(f"        saved → {out_path}")
    return {
        "title": title, "url": url, "chars": len(body),
        "login_wall_hit": hit, "output": str(out_path),
    }


def slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", s).strip("-")
    return s[:80] or "scraped"


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0

    kill_first = "--kill-chrome" in argv
    argv = [a for a in argv if a != "--kill-chrome"]

    if len(argv) < 2:
        print(__doc__)
        return 0

    url = argv[1]
    project_root = Path(__file__).resolve().parent.parent

    if len(argv) >= 3:
        out = Path(argv[2])
    else:
        notes_dir = project_root / "notes" / "_scraped"
        out = notes_dir / f"{slugify(url)}.md"

    if kill_first and chrome_running():
        n = kill_chrome()
        print(f"[pre] killed {n} chrome.exe")
        time.sleep(2)  # 等 chrome 完全释放 profile 锁

    try:
        info = fetch(url, out)
    except RuntimeError as e:
        print(f"\n[FAIL] {e}")
        return 2
    except Exception as e:
        print(f"\n[FAIL] {type(e).__name__}: {e}")
        return 1

    if info["login_wall_hit"]:
        print(f"\n[!] 命中登录墙标记 {info['login_wall_hit']}")
        print("    用户须先在该域名登录，再重跑此脚本。")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))