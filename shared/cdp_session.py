"""shared/cdp_session.py — 单一共享 CDP / 登录态浏览器会话。

用户 2026-08-26 明确：公众号 与 scys 共用一套 CDP 逻辑，应「只开关一次浏览器」。
本模块把「开 / 关浏览器」从各自脚本里抽出来，提升为**单一生命周期**：

  - 优先 connect_over_cdp 接管用户【活 Chrome】（继承登录态，浏览器保持打开，不 kill、不克隆）；
  - CDP 不可用（活 Chrome 没开调试端口）→ 回退 kill chrome → profile_clone → launch_persistent_context
    （与 scripts/scys_batch_fetch.py 原 _run_once 同款回退，仅此路径才会短暂关闭你的 Chrome）。

两种用法：
  A. 单次取标题（公众号）：
        with SharedCdpSession() as s:
            t = s.get_title("https://mp.weixin.qq.com/s/xxx")
  B. 批量 / 复杂交互（scys）：
        with SharedCdpSession() as s:
            page = s.new_page()
            page.goto(url); ...  # scys 自己的 collect_list / fetch_article 逻辑
            html = s.get_html(url)

活 Chrome 路径下，多次独立运行（先 scys、后 gzh）都只是「连接」同一个已开浏览器，
天然零重开；回退路径因每次是新进程才重开，属极少数情况。
"""
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


class SharedCdpSession:
    def __init__(self, headless=True, live_cdp_preferred=True):
        from login_cdp_fetch import discover_chrome_devtools
        from playwright.sync_api import sync_playwright

        self._p = sync_playwright().start()
        self._use_cdp = True
        self._live = False          # 是否接管了用户的活 Chrome
        self._ctx = None
        self._page = None
        self._headless = headless
        self.cdp_endpoint = None     # 供并行 worker 经 connect_over_cdp 复用的 ws 端点

        try:
            if not live_cdp_preferred:
                raise RuntimeError("fallback forced")
            port, ws_path = discover_chrome_devtools()
            browser = self._p.chromium.connect_over_cdp(f"ws://127.0.0.1:{port}{ws_path}")
            self._browser = browser
            self._ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            self._live = True
            self.cdp_endpoint = f"ws://127.0.0.1:{port}{ws_path}"
            print(f"[CDP] 接管活 Chrome: ws://127.0.0.1:{port}{ws_path}（浏览器保持打开）")
        except RuntimeError:
            self._use_cdp = False
            self._live = False
            print("[fallback] CDP 不可用 → kill chrome + profile_clone（会短暂关闭你的 Chrome）")
            subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe", "/T"],
                capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=10,
            )
            time.sleep(2)
            from profile_clone_fetch import ensure_profile_clone
            tmpdir = ensure_profile_clone()
            self._tmpdir = tmpdir
            # 回退克隆浏览器暴露调试端口：并行 worker 经 connect_over_cdp 复用同一浏览器，
            # 从而「父进程 kill 一次、多 worker 共享」，避免各自 kill+clone 的并发污染与重复开销。
            _dbg_port = _free_port()
            self._ctx = self._p.chromium.launch_persistent_context(
                user_data_dir=str(tmpdir),
                headless=headless,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-sandbox", "--disable-dev-shm-usage",
                      f"--remote-debugging-port={_dbg_port}"],
                viewport={"width": 1280, "height": 800},
                ignore_https_errors=True,
            )
            self.cdp_endpoint = _probe_endpoint(_dbg_port)

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
        self._tmpdir = None
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
        # 活 Chrome：只断开 playwright，绝不关闭用户浏览器 / 上下文
        if not self._live and not self._use_cdp:
            try:
                if self._ctx:
                    self._ctx.close()
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
