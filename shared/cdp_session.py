"""shared/cdp_session.py — 单一共享 CDP / 登录态浏览器会话（只走一条路径）。

用户 2026-08-26 明确：公众号 与 scys 共用一套 CDP 逻辑，应「只开关一次浏览器」。

唯一路径（2026-09-02 塌缩，删除路径1/路径3）：
  关掉用户的 Chrome（释放 Network/Cookies 独占锁）
  → 复制真实 profile 到非默认 ProfileClone 目录（ensure_profile_clone）
  → 以该目录 + --remote-debugging-port 启动系统 Chrome（Chrome 151+ 仅在非默认 dir 放行调试）
  → connect_over_cdp 接管（导航/DOM/请求可读，登录态+扩展天然保留）。

为什么要删另两条：
  - 路径1（接管活 Chrome）：Chrome 151+ 在默认 profile 上写了调试端口却不监听，本机不可行 → 删。
  - 路径3（headless 克隆无登录兜底）：会静默撞登录墙产出空壳 → 删。
ProfileClone 是非默认目录的真实副本，Chrome 151+ 放行调试且不触发扩展垃圾回收。

用法：
  A. 单次取标题（公众号）：
        with SharedCdpSession() as s:
            t = s.get_title("https://mp.weixin.qq.com/s/xxx")
  B. 批量 / 复杂交互（scys）：
        with SharedCdpSession() as s:
            page = s.new_page()
            page.goto(url); ...  # scys 自己的 collect_list / fetch_article 逻辑
            html = s.get_html(url)
"""
import os
import sys
import time
import random
import socket
import subprocess
from pathlib import Path

# 让本模块能 import scripts/ 下的 CDP 工具（与 scys_batch_fetch.py 同款做法）
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def _free_port() -> int:
    """挑一个当前空闲的本地 TCP 端口（bind 到 0 由 OS 分配），用于回退克隆浏览器的调试端口。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _probe_endpoint(port: int, retries: int = 20, delay: float = 0.25) -> str:
    """回退克隆浏览器启动后，探测其 DevTools websocket 端点（供并行 worker 复用）。"""
    from login_cdp_fetch import probe_chrome_devtools
    for _ in range(retries):
        try:
            ver = probe_chrome_devtools(port)
            if ver and ver.get("webSocketDebuggerUrl"):
                return ver["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(delay)
    # 兜底：直接拼浏览器级 ws（部分 Chrome 版本只在 /devtools/browser/<uuid> 暴露）
    return f"ws://127.0.0.1:{port}"


def _find_system_chrome() -> str | None:
    """定位系统 Chrome 可执行文件（用于以真实 profile 启动带调试的浏览器）。"""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    import shutil
    return shutil.which("chrome") or shutil.which("google-chrome")


def _chrome_running() -> bool:
    """本机是否有 chrome.exe 进程在跑。"""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                             capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=10)
        return "chrome.exe" in out.stdout
    except Exception:
        return False


def _ensure_chrome_closed() -> None:
    """确保用户的 Chrome 完全退出，从而释放 Network/Cookies 的独占锁。

    登录态复制的前提：源 profile 的 cookie db 未被占用。Chrome 136+ 对
    Default/Network/Cookies 用 FILE_SHARE_NONE 独占锁，运行期复制必失败（见
    references/login-required-cdp-workflow.md §13）。因此 fallback 必须先关 Chrome。
    先优雅结束（taskkill 不带 /F，给 Chrome 跑清理），超时再强杀。
    """
    if not _chrome_running():
        return
    subprocess.run(["taskkill", "/IM", "chrome.exe"],
                   capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=10)
    for _ in range(10):
        if not _chrome_running():
            return
        time.sleep(1)
    # 优雅结束超时 → 强杀兜底
    subprocess.run(["taskkill", "/F", "/IM", "chrome.exe", "/T"],
                   capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=10)
    for _ in range(10):
        if not _chrome_running():
            return
        time.sleep(1)


def _launch_cloned_logged_in_browser(p) -> tuple:
    """可靠兜底（带登录态）：关 Chrome → 复制真实 profile 到非默认 ProfileClone 目录 → 该目录开调试端口启动。

    为什么这条能同时保住「登录态 + CDP 控制」：
      - Chrome 151+ 在【默认 user-data-dir】上禁调试端口（实测 DevToolsActivePort 写了但不监听）；
        在【非默认目录】上放行（实测 5599 正常响应 /json/version）。
      - 故启动用 ProfileClone（非默认 dir）→ CDP 控得住。
      - 登录态靠复制真实 profile 的 cookie；但运行期 cookie 被独占锁，必须先关 Chrome 再复制
        （ensure_profile_clone 增量同步才能把 Network/Cookies 拷过去）。
    启动方式是「直接拉起系统 Chrome + --remote-debugging-port」再 connect_over_cdp，
    规避 Playwright 在默认 dir 上对 pipe 的限制；非默认 dir 下该方式已实测可用。

    Returns:
        (context, cdp_endpoint, proc) —— proc 为拉起的 Chrome 进程，close() 时杀掉进程树。
    """
    from profile_clone_fetch import ensure_profile_clone, CLONE_DIR
    _ensure_chrome_closed()                 # 先释放 cookie 锁
    clone_dir = ensure_profile_clone()       # 关了 Chrome 后，cookie 增量同步可成功
    chrome_exe = _find_system_chrome()
    if not chrome_exe:
        raise RuntimeError("找不到系统 Chrome 可执行文件")
    dbg_port = _free_port()
    # 直接拉起系统 Chrome（真实 profile 副本 + 非默认 dir + 调试端口）
    subprocess.Popen(
        [chrome_exe,
         f"--user-data-dir={clone_dir}",
         f"--remote-debugging-port={dbg_port}",
         "--no-first-run", "--no-default-browser-check",
         "--disable-blink-features=AutomationControlled"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    ws = _probe_endpoint(dbg_port)           # 等 DevTools 就绪，拿 webSocketDebuggerUrl
    browser = p.chromium.connect_over_cdp(ws)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    return ctx, ws, proc



class SharedCdpSession:
    def __init__(self, headless=True):
        from playwright.sync_api import sync_playwright

        self._p = sync_playwright().start()
        self._use_cdp = True
        self._live = True            # 路径2 由我们启动带登录的 Chrome，视作"活会话"
        self._own_browser = True     # 浏览器由我们启动（关Chrome→克隆→启动），退出时关闭
        self._ctx = None
        self._page = None
        self._browser = None
        self._headless = headless    # 路径2 始终可见（scys 登录墙需可见页）；此参数仅保留签名兼容
        self._proc = None            # 拉起的 Chrome 进程（close() 杀掉进程树）
        self.cdp_endpoint = None     # 供并行 worker 经 from_endpoint 复用的 ws 端点

        # 唯一路径（2026-09-02 塌缩，删路径1/路径3）：
        # 关 Chrome → 复制真实 profile 到非默认 ProfileClone → 该 dir 开调试端口启动
        # → connect_over_cdp 接管（CDP 控得住 + 登录态/扩展保留；Chrome 151+ 仅非默认 dir 放行调试）。
        self._ctx, self.cdp_endpoint, self._proc = _launch_cloned_logged_in_browser(self._p)
        print(f"[CDP] 关 Chrome→克隆 profile→非默认 dir 开调试端口启动（登录态+CDP 控制）")

        # 复用同一 page 取标题，避免泄漏
        self._page = self._ctx.pages[0] if getattr(self._ctx, "pages", None) else self._ctx.new_page()

    # ── 公众号：取单篇原文标题 ──
    @classmethod
    def from_endpoint(cls, endpoint: str) -> "SharedCdpSession":
        """经已有 CDP 端点接管同一浏览器（无 kill / 无克隆），供并行 worker 复用父进程会话。

        父进程已创建 SharedCdpSession（最多 kill 一次）；worker 用本方法 connect_over_cdp
        到同一浏览器，从而「一次 kill、多 worker 共享」，避免各自 kill+clone 的竞态与重复开销。
        调用方负责 close()（仅断开本 worker 的驱动连接，不关闭共享浏览器）。
        """
        self = cls.__new__(cls)
        from playwright.sync_api import sync_playwright
        self._p = sync_playwright().start()
        self._use_cdp = True
        self._live = True
        self._headless = True
        self._own_browser = False   # worker 不拥有共享浏览器，close() 不杀进程
        self._proc = None
        self.cdp_endpoint = endpoint
        self._browser = self._p.chromium.connect_over_cdp(endpoint)
        self._ctx = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self._page = self._ctx.pages[0] if getattr(self._ctx, "pages", None) else self._ctx.new_page()
        return self

    def get_title(self, url: str, wait_min: int = 6000, wait_max: int = 14000) -> str:
        """每篇等待 wait_min~wait_max 毫秒的随机区间（默认 6~14s），
        打破机械节拍，比固定 8s 更不易被识别为脚本，且仍属人的阅读节奏。"""
        from shared.fetch_title import _clean
        self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        self._page.wait_for_timeout(random.uniform(wait_min, wait_max))
        return _clean(self._page.title())

    # ── scys：取渲染后 HTML（复杂交互交给调用方用 new_page） ──
    def get_html(self, url: str, wait: int = 8000) -> str:
        page = self.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(wait)
            return page.content()
        finally:
            page.close()

    # ── 微信公众号：单篇 / 批量取正文（复用本会话 context，不重开浏览器） ──
    _WECHAT_LOGIN_MARKERS = ["立即登录", "登录后查看", "请登录", "扫码登录",
                             "您还未登录", "成为会员", "开通会员", "订阅后"]

    def _extract_body(self, page) -> str:
        """从已渲染页面抽取正文（与 articles/fetch.py / scys 同款选择器优先级）。"""
        body = ""
        for sel in [".article-content", ".article-detail", "#articleContent",
                    ".topic-content", ".post-content", ".markdown-body",
                    "article", "main", "body"]:
            try:
                el = page.query_selector(sel)
                if el:
                    t = el.inner_text().strip()
                    if len(t) > len(body):
                        body = t
            except Exception:
                continue
        if not body:
            try:
                body = page.evaluate("() => document.body.innerText")
            except Exception:
                body = ""
        return body or ""

    def fetch_wechat(self, url: str, wait_ms: int = 8000):
        """单篇微信正文 → (title, body, 0) 或 None（撞墙/过短/失败）。

        复用本会话已建立的 context（live 或 profile_clone），不另起浏览器。
        """
        page = self.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(wait_ms)
            title = page.title()
            body = self._extract_body(page)
            if any(m in body for m in self._WECHAT_LOGIN_MARKERS):
                return None
            if len(body.strip()) < 100:
                return None
            return (title, body, 0)
        except Exception:
            return None
        finally:
            page.close()

    def wechat_batch(self, urls: list, wait_ms: int = 8000) -> dict:
        """一次会话批量抓多篇微信文：复用本会话 context，避免重开/重 kill 浏览器。

        scys / 公众号 / 重试 三类抓取共用同一个 SharedCdpSession 时，
        微信撞墙篇收集后统一调本方法，与 scys 同一次会话破墙，Chrome 最多杀一次。

        Returns:
            {url: (title, body, 0) 抓取成功 | None 撞墙/失败/过短}
        """
        from login_cdp_fetch import write_output
        from profile_clone_fetch import slugify
        out_base = Path(__file__).resolve().parent.parent / "notes" / "_scraped" / "wechat_cdp_batch"
        out_base.mkdir(parents=True, exist_ok=True)
        results: dict = {u: None for u in urls}
        print(f"[session] 复用同一 CDP 会话批量抓 {len(urls)} 篇微信文（不重开浏览器）")
        for url in urls:
            try:
                r = self.fetch_wechat(url, wait_ms=wait_ms)
            except Exception as e:
                print(f"[session] {url} 失败: {e}")
                r = None
            if r:
                out = out_base / f"{slugify(url)}.md"
                write_output(out, url, r[0], r[1])
                results[url] = r
            else:
                results[url] = None
        return results

    def new_page(self):
        return self._ctx.new_page()

    @property
    def context(self):
        return self._ctx

    def close(self):
        try:
            if self._page:
                self._page.close()
        except Exception:
            pass
        # 仅当我们自己启动了浏览器（路径2：克隆+非默认 dir 启动）才关闭；
        # 复用共享浏览器（_own_browser=False）只断开 playwright，绝不关闭共享浏览器。
        if getattr(self, "_own_browser", False):
            try:
                if self._ctx:
                    self._ctx.close()
            except Exception:
                pass
            # 杀掉我们拉起的 Chrome 进程树（connect_over_cdp 的 ctx.close() 不终止外部进程）
            proc = getattr(self, "_proc", None)
            if proc is not None and proc.poll() is None:
                try:
                    subprocess.run(["taskkill", "/T", "/PID", str(proc.pid)],
                                   capture_output=True, text=True,
                                   encoding="utf-8", errors="ignore", timeout=10)
                except Exception:
                    pass
        try:
            self._p.stop()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
