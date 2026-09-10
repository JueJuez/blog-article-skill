"""shared/cdp_session.py — 单一共享 CDP / 登录态浏览器会话（只走一条路径）。

用户 2026-08-26 明确：公众号 与 scys 共用一套 CDP 逻辑，应「只开关一次浏览器」。

唯一路径（2026-09-02 塌缩，删除路径1/路径3）：
  关掉用户的 Chrome（释放 Network/Cookies 独占锁）
  → 复制真实 profile 到非默认目录（ensure_profile_clone，默认 CdpAutomationProfile\\Chrome，
     可用 CDP_PROFILE_DIR 覆盖；旧 ProfileClone 目录已弃用）
  → 以该目录 + --remote-debugging-port 启动系统 Chrome（Chrome 151+ 仅在非默认 dir 放行调试）
  → connect_over_cdp 接管（导航/DOM/请求可读，登录态+扩展天然保留）。

为什么要删另两条：
  - 路径1（接管活 Chrome）：Chrome 151+ 在默认 profile 上写了调试端口却不监听，本机不可行 → 删。
  - 路径3（headless 克隆无登录兜底）：会静默撞登录墙产出空壳 → 删。
CdpAutomationProfile\\Chrome 是非默认目录的真实副本，Chrome 151+ 放行调试且不触发扩展垃圾回收。

2026-09-04 修正：ensure_profile_clone 不再做增量同步（会破坏 Secure Preferences 一致性，
导致扩展/Google 登录态丢失），改为「首次/缺失/陈旧时全量复制，否则直接复用」。

2026-09-10 健康复用（以 cdp-automation-profile 的 3 天 marker 机制为准）：
  __init__ 先探测克隆调试 Chrome（读克隆目录 DevToolsActivePort 端口 + /json/version）——
  健康 → connect_over_cdp 直接接管（_own_browser=False，close 只断开不杀），跳过
  杀 Chrome / 复制 / 重启：多 Agent 不再互杀对方浏览器，每次会话省 ~30s+。
  不健康 → 按旧端口精准清僵尸（不误伤日常 Chrome）→ 重建；重建仅在 clone 陈旧/
  缺失（要全量复制、需释放源 cookie 独占锁）时才关日常 Chrome（clone_is_fresh）。
  复用会话撞登录墙（克隆登录态过期）→ restart_fresh() 重建全新会话。

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
import re
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


def _probe_endpoint(port: int, retries: int = 20, delay: float = 0.25,
                    fallback: bool = True) -> str | None:
    """回退克隆浏览器启动后，探测其 DevTools websocket 端点（供并行 worker 复用）。

    fallback=True（默认，启动路径）：重试耗尽返回拼接假地址（部分 Chrome 版本只在
    /devtools/browser/<uuid> 暴露）；fallback=False（复用探测）：快速失败返回 None，
    绝不返回假地址（否则启动失败被延迟成 connect ECONNREFUSED）。
    """
    from login_cdp_fetch import probe_chrome_devtools
    for _ in range(retries):
        try:
            ver = probe_chrome_devtools(port)
            if ver and ver.get("webSocketDebuggerUrl"):
                return ver["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(delay)
    if fallback:
        return f"ws://127.0.0.1:{port}"
    return None


def _read_devtools_port(clone_dir: Path) -> int | None:
    """从克隆目录的 DevToolsActivePort 文件读调试端口（Chrome 启动时写入，第一行是端口）。"""
    try:
        first = (clone_dir / "DevToolsActivePort").read_text(encoding="utf-8").strip().splitlines()[0]
        return int(first)
    except Exception:
        return None


def _probe_reuse_endpoint(port: int, retries: int = 2, delay: float = 0.5) -> str | None:
    """复用探测：既有调试 Chrome 是否健康（快速失败，~1s；失败返回 None 而非假地址）。"""
    return _probe_endpoint(port, retries=retries, delay=delay, fallback=False)


def _kill_chrome_on_port(port: int) -> None:
    """按调试端口定位监听进程并杀掉（netstat 找 PID → taskkill）。

    只杀监听该端口的进程，不误伤日常 Chrome——用于清理半死/僵尸的克隆调试
    Chrome（探测失败≠进程已死：可能还握着克隆目录单实例锁，直接重启会报
    profile in use）。进程已死时 netstat 无监听 → no-op。
    """
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                             encoding="utf-8", errors="ignore", timeout=15)
        pids = set()
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
                pids.add(parts[4])
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True,
                           text=True, encoding="utf-8", errors="ignore", timeout=10)
    except Exception:
        pass


def _get_chrome_cmdlines() -> list:
    """枚举本机所有 chrome.exe 进程的 (pid, 命令行)（PowerShell CIM）。

    wmic 在 Win11 24H2+ 已被移除 → 统一走 Get-CimInstance；输出用 TAB 分隔，
    无 TAB 的行按空白切兜底。非 Windows / 调用失败一律返回 []。
    """
    if sys.platform != "win32":
        return []
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
          "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        ).stdout
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        s = line.strip()
        if not s:
            continue
        parts = s.split("\t", 1)
        if len(parts) < 2:
            parts = s.split(None, 1)
        if len(parts) < 2 or not parts[0].strip().isdigit():
            continue
        rows.append((int(parts[0].strip()), parts[1].strip()))
    return rows


def _find_clone_chrome_port(clone_dir: Path) -> int | None:
    """从活进程命令行里找以 clone_dir 为 user-data-dir 的调试端口。

    crashpad-handler 等子进程匹配目录但无端口 flag → continue 继续找，不误判。
    """
    dir_s = str(clone_dir).lower()
    for _pid, cmd in _get_chrome_cmdlines():
        c = cmd.lower()
        if f"--user-data-dir={dir_s}" not in c and f'--user-data-dir="{dir_s}"' not in c:
            continue
        m = re.search(r"--remote-debugging-port=(\d+)", cmd)
        if m:
            return int(m.group(1))
    return None


def _rewrite_devtools_port_file(clone_dir: Path, ws_endpoint: str) -> bool:
    """把恢复出来的真实端点回写 DevToolsActivePort（下次 __init__ 直读即中）。

    Chrome 原生格式「端口\\n/devtools/browser/<uuid>」；无 browser path 只写端口行。
    任何失败静默返回 False（回写只是优化，不是必须）。
    """
    try:
        m = re.search(r":(\d+)", ws_endpoint or "")
        if not m:
            return False
        path_m = re.search(r"(/devtools/.*)$", ws_endpoint)
        content = f"{m.group(1)}\n{path_m.group(1)}" if path_m else m.group(1)
        (clone_dir / "DevToolsActivePort").write_text(content, encoding="utf-8")
        return True
    except Exception:
        return False


def _kill_clone_chrome(clone_dir: Path) -> int:
    """按克隆目录精准清掉所有 user-data-dir=clone_dir 的 chrome 进程，返回尝试数。

    覆盖「文件端口无监听 → _kill_chrome_on_port 是 no-op → 僵尸仍握克隆目录锁」。
    """
    dir_s = str(clone_dir).lower()
    killed = 0
    try:
        for pid, cmd in _get_chrome_cmdlines():
            c = cmd.lower()
            if f"--user-data-dir={dir_s}" not in c and f'--user-data-dir="{dir_s}"' not in c:
                continue
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="ignore", timeout=10)
            except Exception:
                pass
            killed += 1
    except Exception:
        pass
    return killed


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
    """可靠兜底（带登录态）：按需关 Chrome → 按需全量复制 → 非默认 dir 开调试端口启动。

    为什么这条能同时保住「登录态 + CDP 控制」：
      - Chrome 151+ 在【默认 user-data-dir】上禁调试端口（实测 DevToolsActivePort 写了但不监听）；
        在【非默认目录】上放行（实测 5599 正常响应 /json/version）。
      - 故启动用 CdpAutomationProfile\\Chrome（或 CDP_PROFILE_DIR 指定的非默认 dir）→ CDP 控得住。
      - 登录态+扩展靠一次性「全量复制」整个真实 profile；运行期 cookie 被独占锁，
        因此只有【确实要复制】时才需要先关 Chrome（2026-09-10 条件化：clone_is_fresh()
        为真时 ensure_profile_clone 是 no-op，重启克隆浏览器用不到源锁，不杀日常 Chrome）。
      - 2026-09-04 已验证：全量复制能完整保留书签、cookies、扩展、Google 登录态；
        而只同步部分文件（增量）会破坏 Secure Preferences，导致扩展/Google 登录态丢失。
    启动方式是「直接拉起系统 Chrome + --remote-debugging-port」再 connect_over_cdp，
    规避 Playwright 在默认 dir 上对 pipe 的限制；非默认 dir 下该方式已实测可用。

    Returns:
        (context, cdp_endpoint, proc) —— proc 为拉起的 Chrome 进程，close() 时杀掉进程树。
    """
    from profile_clone_fetch import ensure_profile_clone, clone_is_fresh
    if not clone_is_fresh():
        _ensure_chrome_closed()                 # 仅当要全量复制（需释放源 cookie 锁）才关 Chrome
    clone_dir = ensure_profile_clone()          # 新鲜时是 no-op（3 天 marker 机制）
    chrome_exe = _find_system_chrome()
    if not chrome_exe:
        raise RuntimeError("找不到系统 Chrome 可执行文件")
    dbg_port = _free_port()
    # 直接拉起系统 Chrome（真实 profile 副本 + 非默认 dir + 调试端口）
    proc = subprocess.Popen(
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
        # 健康复用（2026-09-10）→ 按需关 Chrome → 按需全量复制 → 非默认 dir 开调试端口启动。
        # ① 克隆目录有活调试 Chrome（DevToolsActivePort 可探测）→ connect_over_cdp 直接接管
        #   （_own_browser=False，close 只断开不杀），跳过杀 Chrome/复制/重启，多 Agent 不再互杀；
        # ② 文件端口探测不通 → 先按活进程命令行自愈 stale 端口文件（2026-09-10 代码化），
        #    命中真实端口即回写直接复用；自愈失败 → 按旧端口 + 按克隆目录双重清僵尸后走重建；
        # ③ 重建：仅 clone 陈旧/缺失（要全量复制、需释放源 cookie 独占锁）才关日常 Chrome。
        # 失败回滚（2026-09-10）：__init__ 任一步失败必须 stop 已启动的 playwright，
        # 否则其事件循环在本线程永久 running，同线程后续每次构造都报
        # "using Playwright Sync API inside the asyncio loop"（loop 泄漏 → 整批全灭）。
        try:
            from profile_clone_fetch import CLONE_DIR
            reuse_port = _read_devtools_port(CLONE_DIR)
            endpoint = _probe_reuse_endpoint(reuse_port) if reuse_port else None
            if not endpoint:
                # stale 端口恢复（2026-09-10 真机案例）：DevToolsActivePort 属于上一任实例，
                # 文件端口可能无监听而活实例实际在别的端口 → 从活进程命令行找真实端口再探测。
                real_port = _find_clone_chrome_port(CLONE_DIR)
                if real_port and real_port != reuse_port:
                    endpoint = _probe_reuse_endpoint(real_port)
                    if endpoint:
                        _rewrite_devtools_port_file(CLONE_DIR, endpoint)
                        print(f"[CDP] DevToolsActivePort 文件端口 {reuse_port or '无'} ≠ "
                              f"实际监听 {real_port} → 按命令行恢复复用（文件已回写）")
            if endpoint:
                try:
                    self._browser = self._p.chromium.connect_over_cdp(endpoint)
                    self._ctx = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
                    self._own_browser = False
                    self._proc = None
                    self.cdp_endpoint = endpoint
                    self._page = self._ctx.new_page()   # 共享 context 开自己的页，避免误关别人的页
                    print("[CDP] 复用既有调试 Chrome（跳过杀 Chrome/复制/重启）")
                    return
                except Exception as e:
                    print(f"[CDP] 复用既有调试 Chrome 失败（{e}）→ 回退重建")
                    self._browser = None
            if reuse_port:
                _kill_chrome_on_port(reuse_port)        # 探测失败的半死实例可能还握着克隆目录锁
            _kill_clone_chrome(CLONE_DIR)               # 文件端口无监听时按端口杀是 no-op → 按克隆目录兜底清

            self._ctx, self.cdp_endpoint, self._proc = _launch_cloned_logged_in_browser(self._p)
            print("[CDP] 重建调试 Chrome（按需关 Chrome→按需全量复制→非默认 dir 开调试端口启动）")

            # 重建路径独占浏览器，沿用 pages[0]
            self._page = self._ctx.pages[0] if getattr(self._ctx, "pages", None) else self._ctx.new_page()
        except Exception:
            try:
                self._p.stop()
            except Exception:
                pass
            raise

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
        try:
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
        except Exception:
            try:
                self._p.stop()
            except Exception:
                pass
            raise

    def restart_fresh(self) -> bool:
        """复用会话（_own_browser=False）撞登录墙等异常时的兜底：按旧端口精准清掉
        当前调试 Chrome，重建全新会话并接管（转为 _own_browser=True）。
        自启动会话本身就是全量克隆，直接返回 False（调用方自行走其他兜底）。"""
        if self._own_browser:
            return False
        port_m = re.search(r":(\d+)", self.cdp_endpoint or "")
        try:
            self._p.stop()
        except Exception:
            pass
        if port_m:
            _kill_chrome_on_port(int(port_m.group(1)))
        from playwright.sync_api import sync_playwright
        self._p = sync_playwright().start()
        self._ctx, self.cdp_endpoint, self._proc = _launch_cloned_logged_in_browser(self._p)
        self._page = self._ctx.pages[0] if getattr(self._ctx, "pages", None) else self._ctx.new_page()
        self._own_browser = True
        return True

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
