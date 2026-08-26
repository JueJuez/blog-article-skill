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
import subprocess
from pathlib import Path

# 让本模块能 import scripts/ 下的 CDP 工具（与 scys_batch_fetch.py 同款做法）
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


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

        try:
            if not live_cdp_preferred:
                raise RuntimeError("fallback forced")
            port, ws_path = discover_chrome_devtools()
            browser = self._p.chromium.connect_over_cdp(f"ws://127.0.0.1:{port}{ws_path}")
            self._browser = browser
            self._ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            self._live = True
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
            self._ctx = self._p.chromium.launch_persistent_context(
                user_data_dir=str(tmpdir),
                headless=headless,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-sandbox", "--disable-dev-shm-usage"],
                viewport={"width": 1280, "height": 800},
                ignore_https_errors=True,
            )

        # 复用同一 page 取标题，避免泄漏
        self._page = self._ctx.pages[0] if getattr(self._ctx, "pages", None) else self._ctx.new_page()

    # ── 公众号：取单篇原文标题 ──
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
