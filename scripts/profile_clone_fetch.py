# -*- coding: utf-8 -*-
"""profile_clone_fetch.py — Chrome profile 克隆原语（SharedCdpSession 路径2 的唯一依赖）。

本模块只提供「复制用户 profile 到非默认 CdpAutomationProfile\\Chrome 目录」的能力；启动浏览器 + CDP 接管
由 shared/cdp_session.py 的 _launch_cloned_logged_in_browser 负责。两层拆开，避免多路径混淆。

设计：
    唯一 CDP 自动化 profile 目录（%LOCALAPPDATA%\\CdpAutomationProfile\\Chrome）：
    - 每 N 天首跑：全量同步 user-data-dir（robocopy /E /PURGE + ctypes 兜底锁文件）
    - N 天内后续：直接复用，零复制（marker 记录上次全量日期，跨项目共享；N 默认 3，CDP_SYNC_INTERVAL_DAYS 可调）
    同 Windows 用户 + DPAPI ⇒ 复制后的 cookie db 自动可解 ⇒ 登录态自动生效。
    禁止增量同步（只拷部分文件会破坏 Secure Preferences，Chrome 152+ 丢扩展/登录态）。
    2026-09-04 末：ensure_profile_clone 已统一委托跨项目 SKILL（~/.workbuddy/skills/cdp-automation-profile/
    ensure_cdp_profile.py）——本文件只保留 SKILL 缺失时的本地回退，避免三处逻辑漂移。

⚠️ Chrome 151+ 限制（为何必须是非默认 dir）：
    - --remote-debugging-port 在默认 user-data-dir 上被拒（Chrome 136+ 起）
    - --remote-debugging-pipe 在默认 user-data-dir 上也被拒（Chrome 151+ 起）
    - junction 指向默认目录会被检测，触发 extension_garbage_collector 删扩展（2026-08-24 实测）
    → CdpAutomationProfile\\Chrome 是非默认目录的真实副本，Chrome 151+ 放行调试，不触发安全清理

依赖：
    pip install playwright
    playwright install chromium

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
from datetime import date

DEFAULT_SRC_DIR = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\O1830\AppData\Local")) / r"Google\Chrome\User Data"
DEFAULT_SRC_DIR_EDGE = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\O1830\AppData\Local")) / r"Microsoft\Edge\User Data"

DEFAULT_BROWSER = os.environ.get("LOGIN_CLONE_BROWSER", "chrome")  # 或 edge

# 唯一 CDP 自动化 profile 目录（2026-09-04 定：CdpAutomationProfile\Chrome，弃用 ProfileClone）。
# 可通过环境变量 CDP_PROFILE_DIR 覆盖。
_LOCAL_APPDATA = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\O1830\AppData\Local"))
CLONE_DIR = Path(os.environ.get("CDP_PROFILE_DIR", _LOCAL_APPDATA / "CdpAutomationProfile" / "Chrome"))

# 全量副本 marker：存「最近全量同步日期」（YYYY-MM-DD）。跨项目共享——
# 今天第一个项目触发全量，当天后续项目直接复用（见 ensure_profile_clone / cdp-automation-profile SKILL）。
MARKER_DATE = ".cdp_full_copy_date"

# [2026-09-04 删除] 旧的 SYNC_FILES 增量同步清单。实测增量同步只覆盖部分文件，会破坏
# Secure Preferences 一致性，导致 Chrome 152+ 在副本里丢失扩展/Google 登录态。
# 改为：首次/缺失/陈旧时一次性全量复制，日常直接复用完整副本。


def pick_source_user_data_dir() -> Path:
    """选一个有 profile 的 user-data-dir：Chrome 优先（多数用户的登录态所在地）。"""
    for d in [DEFAULT_SRC_DIR, DEFAULT_SRC_DIR_EDGE]:
        if d.exists() and d.is_dir():
            return d
    raise FileNotFoundError(
        f"找不到 Chrome/Edge user-data-dir。已查：\n  - {DEFAULT_SRC_DIR}\n  - {DEFAULT_SRC_DIR_EDGE}"
    )


def _touch_marker(path: Path) -> None:
    path.write_text(str(time.time()), encoding="utf-8")


def _resolve_skill_py() -> str | None:
    """定位跨项目共享 SKILL 的 ensure_cdp_profile.py（优先 CDP_SKILL_PY，否则标准路径）。"""
    cand_env = os.environ.get("CDP_SKILL_PY")
    if cand_env and os.path.exists(cand_env):
        return cand_env
    cand = Path.home() / ".workbuddy" / "skills" / "cdp-automation-profile" / "ensure_cdp_profile.py"
    if cand.exists():
        return str(cand)
    return None


def ensure_profile_clone(src: Path | None = None) -> Path:
    """确保 CLONE_DIR 副本可用：每 N 天首跑全量、期间复用（委托跨项目 SKILL，单一真源）。

    2026-09-04 末：改为委托 ~/.workbuddy/skills/cdp-automation-profile/ensure_cdp_profile.py
    （与 steam/buff 同一份代码，避免三处漂移、换机只维护一份）。SKILL 缺失时回退本地逻辑
    （保持 blog-article-skill 自包含）。

    返回 CLONE_DIR 路径。调用方（SharedCdpSession）负责 kill Chrome 释放 cookie 锁后再调用。
    """
    src = src or pick_source_user_data_dir()
    skill_py = _resolve_skill_py()
    if skill_py is not None:
        try:
            py = os.environ.get("CDP_PYTHON") or sys.executable
            r = subprocess.run(
                [py, skill_py, "--dir", str(CLONE_DIR), "--source", str(src)],
                capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=1800)
            if r.returncode == 0 and r.stdout.strip():
                line = r.stdout.strip().splitlines()[-1]
                if line:
                    return Path(line)
            print(f"[clone] SKILL 委托未返回目录（rc={r.returncode}），回退本地逻辑："
                  f"{(r.stderr or '').strip()[-300:]}")
        except Exception as e:
            print(f"[clone] 委托 SKILL 失败（{e}），回退本地逻辑")
    return _ensure_profile_clone_local(src)


def _ensure_profile_clone_local(src: Path) -> Path:
    """SKILL 缺失时的回退：与 SKILL 同约定（每 N 天首跑全量，期间复用）。"""
    marker = CLONE_DIR / MARKER_DATE
    cookie_check = CLONE_DIR / "Default" / "Network" / "Cookies"
    today = date.today()
    interval = int(os.environ.get("CDP_SYNC_INTERVAL_DAYS", "3"))
    need_full = not CLONE_DIR.exists() or not cookie_check.exists()
    if not need_full and marker.exists():
        try:
            md = date.fromisoformat(marker.read_text(encoding="utf-8").strip())
        except Exception:
            md = None
        if md is None:
            marker.write_text(today.isoformat(), encoding="utf-8")
            return CLONE_DIR
        days = (today - md).days
        if days >= interval:
            need_full = True
        else:
            print(f"[clone] 复用现有副本 {CLONE_DIR}（距上次全量 {days} 天 < {interval}）")
            return CLONE_DIR
    if not need_full and not marker.exists():
        marker.write_text(today.isoformat(), encoding="utf-8")
        print(f"[clone] 目录已完整且无 marker，补写今日 marker（跳过全量）")
        return CLONE_DIR
    print(f"[clone] 全量复制 {src.name} → {CLONE_DIR}（本地回退，"
          f"{'首次' if not CLONE_DIR.exists() else f'距上次≥{interval}天'}）")
    copy_user_data_dir(src, CLONE_DIR)
    marker.write_text(today.isoformat(), encoding="utf-8")
    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        for p in CLONE_DIR.rglob(lock_name):
            try:
                p.unlink()
            except Exception:
                pass
    print(f"[clone] 全量复制完成")
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


# [2026-09-02 删除] fetch_via_profile_clone（headless 克隆抓取 = 旧路径3，会静默撞登录墙）已移除；
# 可靠抓取统一走 shared/cdp_session.py 的 SharedCdpSession（关Chrome→克隆→非默认dir启动+CDP接管）。


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


# [2026-09-02 删除] main() / CLI（原 standalone headless 克隆抓取入口 = 旧路径3）已移除；
# 可靠抓取统一走监控流水线（monitors/run.py → SharedCdpSession）。

