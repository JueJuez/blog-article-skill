"""videos/set_cookie.py — 更新/缓存 B 站 cookie（SESSDATA）的便捷入口。

用途：SESSDATA 约半年过期，过期后需要登录的功能（如下载会员/高清视频、
访问登录态 API）会失效。本模块提供一行命令刷新，并自动校验有效性。

用法：
    python -m videos.set_cookie "SESSDATA=xxxx"            # 仅给 SESSDATA 值（自动补前缀）
    python -m videos.set_cookie 'SESSDATA=xxx; DedeUserID=yyy; bili_jct=zzz'  # 完整 cookie
    python -m videos.set_cookie                           # 从剪贴板读取（Windows）

校验通过写入 .cache/bilibili_cookies.txt（已被 .gitignore 忽略，不会提交）。
"""
import os
import re
import sys

from videos.fetch import (
    _bili_cache_cookies,
    _bili_cache_path,
    _bili_load_cached_cookies,
    validate_bilibili_cookies,
)


def normalize(raw: str) -> str:
    raw = (raw or "").strip().strip('"').strip("'").strip()
    if not raw:
        return ""
    # 仅给了 SESSDATA 裸值（无前缀）时自动补前缀
    if not re.match(r"^SESSDATA=", raw, re.IGNORECASE):
        raw = "SESSDATA=" + raw
    return raw


def set_bilibili_cookie(raw: str) -> bool:
    cookie = normalize(raw)
    if not cookie:
        print("❌ 未提供 cookie 内容")
        return False
    print("→ 正在校验 cookie 有效性（调用 B站 nav API）...")
    if validate_bilibili_cookies(cookie):
        _bili_cache_cookies(cookie)
        print(f"✅ 校验通过，已缓存到 {_bili_cache_path()}")
        print("   之后运行会自动加载，无需再次提供。")
        return True
    print("❌ 校验失败：该 cookie 无效或已过期。")
    print("   请确认复制的是最新 SESSDATA，且当前 B站处于登录态。")
    return False


def _read_clipboard() -> str:
    try:
        import subprocess
        if sys.platform == "win32":
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-command", "Get-Clipboard"],
                stderr=subprocess.DEVNULL,
            ).decode("utf-8", "ignore")
            return out.strip()
    except Exception:
        pass
    return ""


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if not arg:
        cb = _read_clipboard()
        if cb:
            print("→ 从剪贴板读取到内容")
            arg = cb
    if not arg:
        print(__doc__)
        sys.exit(1)
    ok = set_bilibili_cookie(arg)
    sys.exit(0 if ok else 1)
