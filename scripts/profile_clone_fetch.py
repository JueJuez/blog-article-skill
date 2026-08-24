# -*- coding: utf-8 -*-
"""profile_clone_fetch.py — 复制用户 Chrome profile 到持久化 ProfileClone 目录，启新 Chromium 实例带登录态访问。

设计（用户 0 操作 · 模型全自动）：
    用户的 Chrome 没启 debug → login_cdp_fetch.py 自动回退到本模块。
    持久化 ProfileClone 目录（%LOCALAPPDATA%\\Google\\Chrome\\ProfileClone）：
    - 首次：全量复制 user-data-dir（~16GB，robocopy + ctypes 兜底锁文件）
    - 后续：只同步 9 个 cookie/login 文件（秒级，ensure_profile_clone()）
    用 Playwright launch_persistent_context 指定 user-data-dir=ProfileClone（非默认 dir → Chrome 151+ 放行）
    → 访问 URL → 取正文 → 关闭实例（ProfileClone 保留供下次复用）。
    同 Windows 用户 + DPAPI ⇒ 复制后的 cookie db 自动可解 ⇒ 登录态自动生效。

⚠️ Chrome 151+ 限制：
    - --remote-debugging-port 在默认 user-data-dir 上被拒（Chrome 136+ 起）
    - --remote-debugging-pipe（Playwright 内部用的）在默认 user-data-dir 上也被拒（Chrome 151+ 起）
    - junction 指向默认目录会被检测，触发 extension_garbage_collector 删扩展（2026-08-24 实测）
    → ProfileClone 是非默认目录的真实副本，Chrome 151+ 放行调试，不触发安全清理

依赖：
    pip install playwright
    playwright install chromium

用法：
    python scripts/profile_clone_fetch.py "<URL>" [out.md]
    python scripts/profile_clone_fetch.py smoke        # 自检：用公开 URL 验证整条机制能跑通

详见 references/login-required-cdp-workflow.md §1.2。
"""
from __future__ import annotations

import os
import sys
import time
import json
import re
import shutil
import tempfile
import subprocess
import urllib.parse
from pathlib import Path

DEFAULT_SRC_DIR = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\O1830\AppData\Local")) / r"Google\Chrome\User Data"
DEFAULT_SRC_DIR_EDGE = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\O1830\AppData\Local")) / r"Microsoft\Edge\User Data"

DEFAULT_BROWSER = os.environ.get("LOGIN_CLONE_BROWSER", "chrome")  # 或 edge

# 持久化 profile 副本目录（非临时，首次全量复制后保留，后续只同步 cookie）
CLONE_DIR = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\O1830\AppData\Local")) / r"Google\Chrome\ProfileClone"

# 增量同步文件清单（只需这些文件保持最新即可继承登录态）
SYNC_FILES = [
    "Default/Cookies",
    "Default/Cookies-journal",
    "Default/Network/Cookies",
    "Default/Network/Cookies-journal",
    "Default/Login Data",
    "Default/Login Data-journal",
    "Default/Web Data",
    "Default/Web Data-journal",
    "Default/Preferences",
    "Default/Secure Preferences",
    "Local State",
]


def pick_source_user_data_dir() -> Path:
    """选一个有 profile 的 user-data-dir：Chrome 优先（多数用户的登录态所在地）。"""
    for d in [DEFAULT_SRC_DIR, DEFAULT_SRC_DIR_EDGE]:
        if d.exists() and d.is_dir():
            return d
    raise FileNotFoundError(
        f"找不到 Chrome/Edge user-data-dir。已查：\n  - {DEFAULT_SRC_DIR}\n  - {DEFAULT_SRC_DIR_EDGE}"
    )


def ensure_profile_clone(src: Path | None = None) -> Path:
    """确保持久化 ProfileClone 副本可用。首次全量复制(~16GB)，后续只同步 cookie 文件(~秒级)。

    返回 CLONE_DIR 路径。调用方负责 kill Chrome 释放 cookie 锁后再调用。
    """
    src = src or pick_source_user_data_dir()
    cookie_check = CLONE_DIR / "Default" / "Network" / "Cookies"
    if not CLONE_DIR.exists() or not cookie_check.exists():
        print(f"[clone] 首次全量复制 {src.name} → {CLONE_DIR} (约 16GB，一次性)")
        CLONE_DIR.mkdir(parents=True, exist_ok=True)
        copy_user_data_dir(src, CLONE_DIR)
        for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            for p in CLONE_DIR.rglob(lock_name):
                try: p.unlink()
                except Exception: pass
        print(f"[clone] 全量复制完成，后续运行只同步 cookie 文件")
    else:
        print(f"[clone] 增量同步 cookie 文件 → {CLONE_DIR}")
        synced = 0
        for rel in SYNC_FILES:
            src_file = src / rel
            dst_file = CLONE_DIR / rel
            if not src_file.exists():
                continue
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src_file, dst_file)
                synced += 1
            except PermissionError:
                ok, msg = _copy_locked_file(src_file, dst_file)
                if ok:
                    synced += 1
                else:
                    print(f"        [skip] {rel} ({msg})")
        for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            for p in CLONE_DIR.rglob(lock_name):
                try: p.unlink()
                except Exception: pass
        print(f"[clone] 同步 {synced} 个文件完成")
    return CLONE_DIR


"""复制 user-data-dir 到临时目录。用 robocopy 处理可读文件，用 ctypes 兜底被锁文件。

设计（**用户 0 操作 · 模型全自动**）：
    - robocopy /E /COPY:DAT /R:1 /W:1：复制绝大部分目录；锁文件（Chrome 持句柄）会跳过
    - 二次扫描：找出源 user-data-dir 里所有 robocopy 漏下的锁文件，**用 ctypes CreateFileW
      + FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE 全共享模式**读出（Chrome 即便
      独占锁，对全共享读取仍允许），写到副本
    - 最关键的 `Network/Cookies` 和 `Network/Cookies-journal` 即便被 Chrome 持互斥锁，
      也能拿到完整数据
    - 复制完再清 Singleton* 锁文件 → 新 Chromium 实例可启动 → 解密的 cookie 由 Chromium 自动读
"""
import ctypes
from ctypes import wintypes


def _copy_locked_file(src_path: Path, dst_path: Path) -> tuple[bool, str]:
    """用 ctypes 全共享模式读被锁文件。返回 (ok, msg)。"""
    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x1
    FILE_SHARE_WRITE = 0x2
    FILE_SHARE_DELETE = 0x4
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE_VALUE = 0xFFFFFFFFFFFFFFFF

    kernel32 = ctypes.windll.kernel32
    CreateFileW = kernel32.CreateFileW
    CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            wintypes.LPCVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    CreateFileW.restype = wintypes.HANDLE

    ReadFile = kernel32.ReadFile
    ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                         ctypes.POINTER(wintypes.DWORD), wintypes.LPCVOID]

    GetFileSizeEx = kernel32.GetFileSizeEx
    GetFileSizeEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.LARGE_INTEGER)]

    h = CreateFileW(str(src_path), GENERIC_READ,
                    FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                    None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
    if h in (INVALID_HANDLE_VALUE, -1, 0):
        return False, f"CreateFileW err={kernel32.GetLastError()}"

    try:
        size = wintypes.LARGE_INTEGER()
        GetFileSizeEx(h, ctypes.byref(size))
        file_size = size.value
        if file_size <= 0:
            return True, "empty"

        buf = ctypes.create_string_buffer(file_size)
        bytes_read = wintypes.DWORD(0)
        ok = ReadFile(h, buf, file_size, ctypes.byref(bytes_read), None)
        if not ok:
            return False, f"ReadFile err={kernel32.GetLastError()}"

        dst_path.parent.mkdir(parents=True, exist_ok=True)
        dst_path.write_bytes(buf.raw[:bytes_read.value])
        return True, f"{bytes_read.value} bytes"
    finally:
        kernel32.CloseHandle(h)


def copy_user_data_dir(src: Path, dst: Path) -> dict:
    """复制 user-data-dir。返回 dict 含 {copied_files, copied_mb, fixed_locked_files}。"""
    import subprocess
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)

    log = dst.parent / f"{dst.name}.robocopy.log"
    cmd = [
        "robocopy", str(src), str(dst),
        "/E", "/COPY:DAT",
        "/R:1", "/W:1",
        "/NFL", "/NDL", "/NJH", "/NJS",
        f"/LOG+:{log}",
    ]
    rc = subprocess.run(cmd, shell=False).returncode
    # robocopy 退出码位：
    #   bit 0 (1) = files copied   bit 1 (2) = extra
    #   bit 2 (4) = mismatched      bit 3 (8) = 部分文件 copy 错误（被锁等）
    #   bit 4+ (16+) = 严重错误     bit 5 = 计划禁止 / 全部失败
    # 我们只把 bit 4+ 视为致命 —— bit 3 表示「锁文件失败」，由后面 ctypes 兜底。
    if rc >= 16:
        raise RuntimeError(f"robocopy 严重失败（退出码 = {rc}），日志：{log}")

    # 二次扫描兜底：找出源里有但副本里没有的文件（被锁跳过），用 ctypes 强行复制
    src_files = {p.relative_to(src) for p in src.rglob("*") if p.is_file()}
    dst_files = {p.relative_to(dst) for p in dst.rglob("*") if p.is_file()}
    missing = src_files - dst_files
    fixed = []
    failed = []
    for rel in missing:
        sp = src / rel
        dp = dst / rel
        ok, msg = _copy_locked_file(sp, dp)
        (fixed if ok else failed).append((str(rel), msg))
        print(f"          {'[FIXED]' if ok else '[STILL]'} {rel} ({msg})")

    # 删 Singleton* 锁文件（即便在副本里 Chrome 也会拒绝启动）
    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        for p in dst.rglob(lock_name):
            try:
                if p.is_file() or p.is_symlink():
                    p.unlink()
            except Exception:
                pass

    copied_mb = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file()) / 1024 / 1024
    return {
        "copied_files": len(dst_files) + len(fixed),
        "copied_mb": round(copied_mb, 1),
        "fixed_locked": len(fixed),
        "still_locked": [n for n, _ in failed],
    }


def fetch_via_profile_clone(url: str, out_path: Path, *,
                            src_dir: Path | None = None,
                            headless: bool = True,
                            wait_ms: int = 8000,
                            selector: str | None = None) -> dict:
    """主流程：复制 profile → 启 headless Chromium → 访问 URL → 抓正文 → 写文件 → 清理。"""
    from playwright.sync_api import sync_playwright  # type: ignore

    src = src_dir or pick_source_user_data_dir()

    # 持久化 ProfileClone：首次全量复制，后续只同步 cookie 文件
    tmpdir = ensure_profile_clone(src)
    copied = sum(f.stat().st_size for f in tmpdir.rglob("*") if f.is_file())

    with sync_playwright() as p:
        browser_ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(tmpdir),
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            viewport={"width": 1280, "height": 800},
            ignore_https_errors=True,
        )
        try:
            print(f"[4/6] new page + goto {url}")
            page = browser_ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            if wait_ms:
                page.wait_for_timeout(wait_ms)
            title = page.title()
            body = ""
            if selector:
                try:
                    page.wait_for_selector(selector, timeout=10_000)
                except Exception:
                    pass
            for sel in [
                selector,
                ".article-content", ".article-detail", "#articleContent",
                ".topic-content", ".post-content", ".markdown-body",
                "article", "main", "body",
            ]:
                if not sel: continue
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
        finally:
            browser_ctx.close()

    login_markers = ["立即登录", "登录后查看", "请登录", "扫码登录",
                     "您还未登录", "成为会员", "开通会员", "订阅后"]
    hit = [m for m in login_markers if m in body]
    print(f"[6/6] title = {title!r}  body_chars = {len(body)}  login_wall = {hit or '无'}")
    write_output(out_path, url, title, body)
    print(f"        saved → {out_path}")
    return {
        "title": title, "url": url, "chars": len(body),
        "login_wall_hit": hit, "output": str(out_path),
        "src_dir": str(src), "clone_dir": str(tmpdir),
        "copied_mb": round(copied/1024/1024, 1),
    }


def write_output(out_path: Path, url: str, title: str, body: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = body.replace("\r\n", "\n").strip()
    out_path.write_text(
        f"# {title}\n\n"
        f"> 来源：{url}\n"
        f"> 抓取时间：{ts}\n"
        f"> 渠道：profile_clone_fetch（复制用户 Chrome profile · 头less 新实例 · 登录态继承）\n\n"
        f"---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )


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
        url = "https://example.com"
        out = project_root / "notes" / "_scraped" / "_smoke_profile_clone.md"
        try:
            info = fetch_via_profile_clone(url, out)
            print(f"\n[OK] smoke 跑通 → {info['output']}")
            return 0
        except Exception as e:
            print(f"\n[FAIL] smoke: {e}")
            return 2
    url = cmd
    if len(argv) >= 3:
        out = Path(argv[2])
    else:
        out = project_root / "notes" / "_scraped" / f"{slugify(url)}.md"
    try:
        info = fetch_via_profile_clone(url, out)
        if info["login_wall_hit"]:
            print(f"\n[!] 命中登录墙标记 {info['login_wall_hit']}")
            print("    profile 复制完整 / cookie 同步了，但当前用户在该域名仍无登录态（已过期或未登录）")
            print("    让用户先在该站登录一次再重跑。文件已落，重抓可覆盖。")
            return 3
        return 0
    except Exception as e:
        print(f"\n[FAIL] {type(e).__name__}: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
