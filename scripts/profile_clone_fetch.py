# -*- coding: utf-8 -*-
"""profile_clone_fetch.py — 复制用户 Chrome profile 到临时目录，启新 Chromium 实例带登录态访问。

设计（用户 0 操作 · 模型全自动）：
    用户的 Chrome 没启 debug、不动用户的 Chrome、不打 debug flag、不重启任何东西。
    把用户的 user-data-dir 复制到一个临时目录 → 用 Playwright 启动一个 headless Chromium 实例
    指定 user-data-dir=临时副本 → 访问 URL → 取正文 → 关闭实例 → 删临时目录。
    同 Windows 用户 + DPAPI ⇒ 复制后的 cookie db 自动可解 ⇒ 登录态自动生效。

依赖：
    pip install playwright
    playwright install chromium

用法：
    python scripts/profile_clone_fetch.py "<URL>" [out.md]
    python scripts/profile_clone_fetch.py smoke        # 自检：用公开 URL 验证整条机制能跑通

详见 references/login-required-cdp-workflow.md §12。
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


def pick_source_user_data_dir() -> Path:
    """选一个有 profile 的 user-data-dir：Chrome 优先（多数用户的登录态所在地）。"""
    for d in [DEFAULT_SRC_DIR, DEFAULT_SRC_DIR_EDGE]:
        if d.exists() and d.is_dir():
            return d
    raise FileNotFoundError(
        f"找不到 Chrome/Edge user-data-dir。已查：\n  - {DEFAULT_SRC_DIR}\n  - {DEFAULT_SRC_DIR_EDGE}"
    )


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
    src_size = sum(f.stat().st_size for f in src.rglob("*") if f.is_file())
    print(f"[1/6] src user-data-dir: {src}  ({src_size/1024/1024:.1f} MB)")

    tmpdir = Path(tempfile.mkdtemp(prefix="profile_clone_"))
    try:
        print(f"[2/6] copy → {tmpdir}  (这一步对用户 Chrome 无副作用)")
        copy_user_data_dir(src, tmpdir)
        copied = sum(f.stat().st_size for f in tmpdir.rglob("*") if f.is_file())
        src_n = sum(1 for _ in src.rglob("*") if _.is_file())
        dst_n = sum(1 for _ in tmpdir.rglob("*") if _.is_file())
        print(f"        copied {copied/1024/1024:.1f} MB  (src={src_n} dst={dst_n} 文件)")
        if dst_n < src_n:
            missing = list({p.relative_to(src) for p in src.rglob("*") if p.is_file()} -
                          {p.relative_to(tmpdir) for p in tmpdir.rglob("*") if p.is_file()})
            print(f"        ! 仍有 {len(missing)} 文件未复制：")
            for m in sorted(missing, key=str)[:20]:
                print(f"          - {m}")

        print(f"[3/6] launch headless chromium on user_data_dir={tmpdir}")
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
                print(f"[5/6] close browser ctx")
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
            "src_dir": str(src), "tmpdir": str(tmpdir),
            "copied_mb": round(copied/1024/1024, 1),
        }
    finally:
        # 14GB 大目录 rmtree 非常慢（分钟级）；用后台进程异步清理，不阻塞 main return
        try:
            subprocess.Popen(
                ["cmd", "/c", "rd", "/s", "/q", str(tmpdir)],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x00000008,  # DETACHED_PROCESS —— 即便父进程退出也跑
            )
            print(f"        cleanup scheduled: {tmpdir}")
        except Exception as e:
            print(f"        cleanup skipped: {e}")


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
