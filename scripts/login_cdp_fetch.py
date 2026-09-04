# -*- coding: utf-8 -*-
"""login_cdp_fetch.py — 接管用户主 Chrome 抓需登录态的页面。

用法：
    python scripts/login_cdp_fetch.py "<URL>" [out.md]
    python scripts/login_cdp_fetch.py smoke         # 自检：仅验证 Chrome Debug 就绪

设计（用户 0 操作 · 模型全自动）：
    默认前提：用户主 Chrome 已经启过 debug 模式（一次性，浏览器正常用 DevTools 时大概率已经启过），
              + 用户已经在浏览器登录目标站点。满足前提 → 模型全自动接管（探活 → 抓 → 落盘）。
    兜底：探测所有 Chrome DevTools 端口仍找不到 → 给用户**一键可复制的命令**，不翻文档。

行为：
    1. 读 %LOCALAPPDATA%\\Google\\Chrome\\User Data\\DevToolsActivePort（如有）
    2. 若该端口不通 → 扫描本机所有 LISTEN 端口（Chrome DevTools 高频区），每个 HTTP /json/version 验证
    3. Playwright connect_over_cdp → 接 chrome → 新页 → 访问 URL → 抓正文 → 写文件
    4. 不关 chrome、不重启 chrome —— 完事后用户的标签、cookie、登录态原状

依赖：
    pip install playwright

详见 references/login-required-cdp-workflow.md（§11 用户 0 操作前置 + §1 一次性命令）。
"""
from __future__ import annotations

import os
import sys
import time
import json
import re
import socket
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

DEFAULT_USER_DIR = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\O1830\AppData\Local")) / r"Google\Chrome\User Data"

# 候选端口文件位置（Chrome 不同版本/不同 setup 会写在不同层）
PORT_FILE_CANDIDATES = [
    DEFAULT_USER_DIR / "DevToolsActivePort",
    DEFAULT_USER_DIR / "Default" / "DevToolsActivePort",
    DEFAULT_USER_DIR / "Profile 1" / "DevToolsActivePort",
]

# Chrome DevTools 「高频端口」（一次性启 debug 时常用的两个）
# 自定义端口一律走 DevToolsActivePort 文件（见 PORT_FILE_CANDIDATES），无需广扫。
# 不再扫描 9000-9999：1000 端口 × 0.3s 超时 = Chrome 未启 debug 时卡 ~5 分钟才失败。
COMMON_DEVTOOLS_PORTS = {9222, 5494}
EXTRA_SCAN_RANGE = range(0)

# Chrome 151+ 限制：远程调试只能用「非默认 user-data-dir」。
# 旧方案 junction（DebugUDD → User Data）会被检测并触发安全清理（删 22 扩展，2026-08-24 实测）；
# login_persistent_fetch.py（launch_persistent_context 真实目录 + pipe）同样被 151+ 拒绝。
# 现行方案：shared/cdp_session.py 的 SharedCdpSession 关 Chrome → 复制 profile 到非默认
# CdpAutomationProfile\Chrome → 该 dir 开调试端口启动 → connect_over_cdp 接管（登录态+扩展保留）。
# 本文件只提供 CDP 探测原语（discover_chrome_devtools / probe_chrome_devtools），
# 不再内嵌抓取兜底；可靠抓取统一走监控流水线 monitors/run.py。


def find_devtools_port_file() -> Path | None:
    """定位 DevToolsActivePort。"""
    for p in PORT_FILE_CANDIDATES:
        if p.exists():
            return p
    return None


def scan_listening_ports(timeout: float = 0.3) -> list[int]:
    """扫描本机 LISTEN 端口：扫描 Chrome DevTools 高频区（不假设 5494）。

    Returns:
        接受连接的端口列表（已按数字排序）。
    """
    targets = set(COMMON_DEVTOOLS_PORTS)
    targets.update(EXTRA_SCAN_RANGE)
    accepting = []
    for p in sorted(targets):
        try:
            s = socket.create_connection(("127.0.0.1", p), timeout=timeout)
            s.close()
            accepting.append(p)
        except (ConnectionRefusedError, socket.timeout, OSError):
            pass
    return accepting


def probe_chrome_devtools(port: int, timeout: float = 2.0) -> dict | None:
    """HTTP /json/version：返 dict（是 Chrome）或 None（非 Chrome）。"""
    try:
        r = urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout)
        body = r.read().decode()
        if "Chrome" in body or "HeadlessChrome" in body:
            return json.loads(body)
    except Exception:
        return None
    return None


def discover_chrome_devtools() -> tuple[int, str]:
    """找本机任一 Chrome DevTools：先读文件 → 失败则扫高频端口 → 全失败抛错。

    设计（用户 0 操作）：不假设 5494；任一 Chrome 实例启了 debug 就接管。

    Returns:
        (port, ws_path) — 已通过 /json/version 验证是 Chrome（返回 JSON 含浏览器版本）。

    Raises:
        RuntimeError — 没找到时，错误内含一键可复制的启 Chrome debug 命令；
            若文件指向的端口被占位但响应不是真 DevTools（404 / 非 JSON），
            报错会明确提示「文件过期 / 非调试实例」，避免与「端口真没开」混淆。
    """
    stale_hint = False

    # 路径 1: DevToolsActivePort 文件（用户主 Chrome 默认行为）
    pf = find_devtools_port_file()
    if pf:
        try:
            lines = pf.read_text(encoding="utf-8").splitlines()
            if lines:
                port = int(lines[0].strip())
                ver = probe_chrome_devtools(port)
                if ver:
                    # 关键：ws 路径永远以 /json/version 实时返回的 webSocketDebuggerUrl 为准，
                    # 不信任文件第二行的 ws uuid。文件 uuid 在多轮 kill/重启或 Chrome 写入时机下
                    # 可能过期（连 404），而实时响应才是当前活着的调试实例。
                    ws_url = ver.get("webSocketDebuggerUrl", "")
                    if ws_url.count("/") >= 3:
                        ws_path = "/" + ws_url.split("/", 3)[3]
                    else:
                        ws_path = lines[1].strip() if len(lines) >= 2 else f"/devtools/browser/{port}"
                    return port, ws_path
                # 文件存在但端口没响应成 Chrome DevTools（404 / 非 JSON）= 过期或占位
                stale_hint = True
        except Exception:
            pass  # 文件存在但端口不对 = stale；跳过路径 1

    # 路径 2: 高频端口扫描（仅 Chrome 默认 9222 + 本项目约定 5494；不再广扫 9000-9999）
    accepting = scan_listening_ports()
    for p in accepting:
        ver = probe_chrome_devtools(p)
        if ver:
            # 从 /json/version 的 webSocketDebuggerUrl 拆 ws_path
            ws_url = ver.get("webSocketDebuggerUrl", "")
            if ws_url.count("/") >= 3:
                ws_path = "/" + ws_url.split("/", 3)[3]
            else:
                ws_path = f"/devtools/browser/{p}"
            return p, ws_path

    # 探测失败 → 抛错（区分两种根因）；可靠抓取改走监控流水线
    if stale_hint:
        raise RuntimeError(
            "本机找到 DevToolsActivePort 文件（声称端口已开），但该端口响应不是 Chrome DevTools"
            "（探测 /json/version 返回非 JSON 或被 404）。\n"
            "    根因通常是：① 文件已过期（当初带调试标志启动的 Chrome 已退出 / 被自动重启，"
            "新进程没带 --remote-debugging-port）；或 ② 该端口被非调试进程占用。\n"
            "    ⚠️ Chrome 151+ 已禁止在默认 user-data-dir 上开远程调试，旧的 junction 方案已废弃"
            "（会触发扩展垃圾回收删除全部扩展）。\n"
            "    可靠抓取请走监控流水线：monitors/run.py（SharedCdpSession 会自动关 Chrome → "
            "克隆 profile 到非默认 dir → 该 dir 开调试端口启动带登录态的 Chrome）。\n"
            "    详细：references/login-required-cdp-workflow.md §11。"
        )
    raise RuntimeError(
        "本机没找到任何 Chrome DevTools 监听端口（已检查 DevToolsActivePort 文件 + 9222 + 5494）。\n"
        "    ⚠️ Chrome 151+ 已禁止在默认 user-data-dir 上开远程调试，旧的 junction 方案已废弃"
        "（会触发扩展垃圾回收删除全部扩展）。\n"
        "    可靠抓取请走监控流水线：monitors/run.py（SharedCdpSession 会自动关 Chrome → "
        "克隆 profile 到非默认 dir → 该 dir 开调试端口启动带登录态的 Chrome）。\n"
        "    详细：references/login-required-cdp-workflow.md §11。"
    )


def verify_chrome_devtools(port: int, timeout: float = 3.0) -> str:
    """HTTP GET /json/version，确认端口上是 Chrome DevTools。"""
    url = f"http://127.0.0.1:{port}/json/version"
    req = Request(url)
    try:
        body = urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    except HTTPError as e:
        raise RuntimeError(
            f"port {port} HTTP {e.code} —— 不是 Chrome DevTools。"
            f"详见 references/login-required-cdp-workflow.md §1。"
        ) from None
    except URLError as e:
        raise RuntimeError(
            f"port {port} 连不上 ({e.reason}) —— Chrome 没启 debug 端口或被占用。"
            f"详见 §1。"
        ) from None
    if "Chrome" not in body and "HeadlessChrome" not in body:
        raise RuntimeError(
            f"port {port} 不是 Chrome DevTools（响应不含 'Chrome'）：\n{body[:200]}"
        )
    return body


def write_output(out_path: Path, url: str, title: str, body: str) -> None:
    """正文落 markdown。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = body.replace("\r\n", "\n").strip()
    out_path.write_text(
        f"# {title}\n\n"
        f"> 来源：{url}\n"
        f"> 抓取时间：{ts}\n"
        f"> 渠道：login_cdp_fetch（接管用户主 Chrome · 登录态继承）\n\n"
        f"---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )


def fetch(url: str, out_path: Path, *, selector: str | None = None,
          wait_ms: int = 8000) -> dict:
    """主流程：探活（文件 + 全端口扫）→ 接 chrome → 抓正文 → 写文件。"""
    from playwright.sync_api import sync_playwright  # type: ignore

    port, ws_path = discover_chrome_devtools()
    ws = f"ws://127.0.0.1:{port}{ws_path}"
    print(f"[1/3] ws endpoint = {ws}  port {port} (已通过 /json/version 验证)")

    print(f"[2/3] connect_over_cdp …")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        print(f"        goto {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        if wait_ms:
            page.wait_for_timeout(wait_ms)
        title = page.title()
        if selector:
            try:
                page.wait_for_selector(selector, timeout=10_000)
            except Exception:
                pass
        # 优先 selectors 取最长一段
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
        page.close()

    # 登录态标记
    login_markers = ["立即登录", "登录后查看", "请登录", "扫码登录",
                     "您还未登录", "成为会员", "开通会员", "订阅后"]
    hit = [m for m in login_markers if m in body]

    print(f"[3/3] title = {title!r}  body_chars = {len(body)}  "
          f"login_wall = {hit or '无'}")
    write_output(out_path, url, title, body)
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

    cmd = argv[1]
    project_root = Path(__file__).resolve().parent.parent

    if cmd == "smoke":
        # 仅自检 chrome debug 是否就绪（同样走全端口探测）
        try:
            port, ws_path = discover_chrome_devtools()
            ver = probe_chrome_devtools(port)
            print(f"[OK] port {port} ws {ws_path} devtools-bridge alive")
            print(f"     {ver.get('Browser','?')}")
            return 0
        except Exception as e:
            print(f"[FAIL] {e}")
            return 2

    url = cmd
    if len(argv) >= 3:
        out = Path(argv[2])
    else:
        notes_dir = project_root / "notes" / "_scraped"
        out = notes_dir / f"{slugify(url)}.md"

    try:
        info = fetch(url, out)
    except RuntimeError as e:
        print(f"\n[FAIL] CDP 不可用（{e}）")
        print("        本机 Chrome 151+ 默认 profile 无调试端口；可靠抓取请走监控流水线"
              "（monitors/run.py → SharedCdpSession 会自动克隆 profile 到非默认 dir 启动带登录的 Chrome）。")
        return 2

    # 若撞登录墙，告警但仍落盘（用户可手动登录后重抓）
    if info["login_wall_hit"]:
        print(f"\n[!] 命中登录墙标记 {info['login_wall_hit']}")
        print("    用户须先在该域名登录，再重跑此脚本。文件已落，等登录态就绪后重抓覆盖。")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
