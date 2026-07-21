"""videos/cdp_launch.py — 确保本机带代理插件的 Chrome 以调试端口就绪。

为什么需要它：
    YouTube 字幕在本机（仅浏览器扩展代理、无本地端口）无法被 Python/curl 直接抓取。
    正解是驱动【用户正在用的那套 Chrome 配置（含代理插件）】经由 CDP 打开视频页，
    由播放器自己发字幕请求，我们再在 Network 层拦截响应体（详见
    references/youtube-cdp-workflow.md）。

    Chrome 136+ 出于安全，**禁止在默认 user-data-dir 上开调试端口**，因此必须用一份
    【非默认路径的配置副本】启动。本模块负责：① 副本不存在时从真实配置复制必要目录
    （含代理插件）；② 若 9222 未起，则从副本启动 Chrome 并等待就绪。

要点（踩过的坑，勿改）：
    - 用 subprocess 列表传参（非 shell），`--remote-allow-origins=*` 的 `*` 不会被 glob 展开。
    - 副本目录必须 ≠ 默认目录，否则 Chrome 拒绝开调试端口。
    - 绝不 kill 用户的真实 Chrome；本模块只用独立的副本目录，互不影响。
"""
import os
import sys
import time
import json
import shutil
import subprocess
import urllib.request

# --- 路径常量（Windows） ---
_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]
DEFAULT_PROFILE = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
CDP_PROFILE = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome-CDP")
CDP_PORT = 9222


def _chrome_exe() -> str:
    for p in _CHROME_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return _CHROME_CANDIDATES[0]


def is_port_up(port: int = CDP_PORT, timeout: float = 2) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_cdp_profile(verbose: bool = True) -> bool:
    """从默认 profile 复制/同步必要目录到 CDP 副本（含代理插件）。

    只复制让「代理插件生效 + YouTube 可访问」所需的最小集合，不复制缓存/历史。
    关键变更：副本已存在时也会**同步扩展目录**，防止源配置更新或首次复制不全
    导致代理插件缺失（这是此前 YouTube 空白/无法访问的直接原因）。
    """
    src = DEFAULT_PROFILE
    if not os.path.isdir(src):
        if verbose:
            print("   ⚠️ 未找到本机 Chrome 默认配置，无法创建 CDP 副本")
        return False

    # 始终要同步的目录（代理插件的代码和配置都在这些目录里）
    sync_items = [
        ("Local State", True),
        (os.path.join("Default", "Extensions"), True),
        (os.path.join("Default", "Local Extension Settings"), True),
        (os.path.join("Default", "Preferences"), True),
        (os.path.join("Default", "Secure Preferences"), True),
        (os.path.join("Default", "Network"), True),
        (os.path.join("Default", "Extension State"), True),
        (os.path.join("Default", "Extension Rules"), True),
        (os.path.join("Default", "Extension Scripts"), True),
        (os.path.join("Default", "Sync Extension Settings"), True),
        (os.path.join("Default", "Managed Extension Settings"), True),
    ]

    def _robust_copytree(s: str, d: str):
        """递归复制目录，跳过无法读取/被锁定的文件（如用户正在运行的 Chrome
        持有的 Cookies），不因单个锁定文件而中断整份配置同步。"""
        os.makedirs(d, exist_ok=True)
        for root, dirs, files in os.walk(s):
            rel_root = os.path.relpath(root, s)
            dst_root = os.path.join(d, rel_root)
            os.makedirs(dst_root, exist_ok=True)
            for f in files:
                sf = os.path.join(root, f)
                df = os.path.join(dst_root, f)
                try:
                    shutil.copy2(sf, df)
                except (PermissionError, OSError) as e:
                    # 跳过被锁定的文件（如 Network/Cookies），其余照常同步
                    if verbose:
                        print(f"   ⏭️ 跳过锁定文件 {os.path.join(rel_root, f)}: {e}")

    def _copy_item(rel: str, always_sync: bool) -> bool:
        s = os.path.join(src, rel)
        d = os.path.join(CDP_PROFILE, rel)
        if not os.path.exists(s):
            return True  # 源不存在则跳过
        if os.path.isdir(s):
            if not os.path.isdir(d):
                if verbose:
                    print(f"   复制 {rel} ...")
                _robust_copytree(s, d)
                return True
            # 已存在：若标记 always_sync 或目标目录明显过旧，则同步
            if always_sync:
                if verbose:
                    print(f"   同步 {rel} ...")
                _robust_copytree(s, d)
            return True
        elif os.path.isfile(s):
            if not os.path.isfile(d) or always_sync:
                if verbose:
                    print(f"   同步 {rel} ...")
                os.makedirs(os.path.dirname(d) or CDP_PROFILE, exist_ok=True)
                try:
                    shutil.copy2(s, d)
                except (PermissionError, OSError) as e:
                    print(f"   ⏭️ 跳过锁定文件 {rel}: {e}")
            return True
        return False

    try:
        os.makedirs(CDP_PROFILE, exist_ok=True)
        for rel, always_sync in sync_items:
            if not _copy_item(rel, always_sync):
                if verbose:
                    print(f"   ⚠️ 同步 {rel} 失败")
        if verbose:
            print(f"   ✅ CDP 副本已就绪: {CDP_PROFILE}")
        return True
    except Exception as e:
        if verbose:
            print(f"   ⚠️ 创建/同步 CDP 副本失败: {e}")
        return False


def _proxy_extension_path() -> str | None:
    """发现用户用来访问 YouTube 的代理扩展（当前是 iGuge）。

    Chrome 复制 profile 到 CDP 目录后，Secure Preferences 会因路径/签名校验
    被重置，导致扩展注册信息丢失。通过 `--load-extension` 直接加载扩展目录，
    可以绕过 Secure Preferences 的注册问题。
    """
    # 已知代理扩展 ID（按优先级）
    known_proxy_ids = ["ncldcbhpeplkfijdhnoepdgdnmjkckij"]  # iGuge
    ext_root = os.path.join(CDP_PROFILE, "Default", "Extensions")
    for ext_id in known_proxy_ids:
        ext_dir = os.path.join(ext_root, ext_id)
        if os.path.isdir(ext_dir):
            versions = [d for d in os.listdir(ext_dir) if os.path.isdir(os.path.join(ext_dir, d))]
            if versions:
                # 取最新版本（通常只有一个）
                return os.path.join(ext_dir, sorted(versions)[-1])
    return None


def launch_chrome(port: int = CDP_PORT, verbose: bool = True) -> bool:
    """从 CDP 副本目录启动带调试端口的 Chrome（后台独立进程）。"""
    exe = _chrome_exe()
    # 必须用列表传参，避免 shell 把 '*' 当通配符展开
    args = [
        exe,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={CDP_PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    # 直接加载代理扩展，绕过 Secure Preferences 重置问题
    proxy_ext = _proxy_extension_path()
    if proxy_ext:
        args.append(f"--load-extension={proxy_ext}")
        if verbose:
            print(f"   🔌 将加载代理扩展: {proxy_ext}")
    args.append("about:blank")
    log_path = os.path.join(CDP_PROFILE, "cdp_launch.log")
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        with open(log_path, "a", encoding="utf-8") as log:
            subprocess.Popen(args, stdout=log, stderr=log, creationflags=flags, close_fds=True)
        if verbose:
            print(f"   🚀 已后台启动 Chrome（CDP 副本，端口 {port}）")
        return True
    except Exception as e:
        if verbose:
            print(f"   ⚠️ 启动 Chrome 失败: {e}")
        return False


def ensure_chrome_running(port: int = CDP_PORT, timeout: int = 25, verbose: bool = True) -> bool:
    """保证 9222 调试端口可用：已起则直接复用，否则建副本并启动。"""
    if is_port_up(port):
        if verbose:
            print(f"   ✅ Chrome 调试端口 {port} 已就绪")
        return True
    if not ensure_cdp_profile(verbose=verbose):
        return False
    if not launch_chrome(port, verbose=verbose):
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1)
        if is_port_up(port):
            if verbose:
                print(f"   ✅ Chrome 调试端口 {port} 已就绪")
            return True
    if verbose:
        print(f"   ⚠️ Chrome 启动超时（{timeout}s 内 {port} 未响应），查看日志: {os.path.join(CDP_PROFILE, 'cdp_launch.log')}")
    return False


if __name__ == "__main__":
    ok = ensure_chrome_running()
    print("OK" if ok else "FAILED")
