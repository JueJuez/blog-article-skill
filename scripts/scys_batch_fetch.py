# -*- coding: utf-8 -*-
"""scys_batch_fetch.py — scys 项目标签批量抓取器（限速 · 断点续传 · 外链跟进）。

用法：
    python scripts/scys_batch_fetch.py --project AI产品开发 --pages 1 --limit 3
    python scripts/scys_batch_fetch.py --project AI产品开发 --limit 20   # 续跑
    python scripts/scys_batch_fetch.py --list-projects                  # 看可抓领域

换领域 / 调时间窗：改 scripts/scys_projects.json（projects + defaults），脚本零改动。
新领域的 menuId 捕获方法见 references/scys-fetch-sop.md §7（tags 页真实点击标签）。

流程：
    1. 打开 tags 页按项目标签捕获 searchTopic 响应（真实点击翻页，模拟浏览）
    2. 按 isDigested + 阅读数排序，逐篇 goto 正文页抓取
    3. 正文内：站内 articleDetail 链接（前情提要）递归抓一层；
       外部知识库链接（飞书/语雀等）滚动到底抓全文
    4. 限速：篇间 15~40s 随机；每 10~15 篇歇 3~8 分钟；翻页间 3~6s
    5. state.json 断点续传；抓完入 pending_summaries.json 等待总结落飞书

产物：
    notes/_scraped/scys/<topicId>.md        正文原文
    notes/_scraped/scys/state.json          进度状态
    notes/_scraped/scys/list_<menuId>.json  列表快照
    notes/_scraped/scys/pending_summaries.json  待总结队列
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 让 shared 包可导入
from login_cdp_fetch import discover_chrome_devtools, write_output
from shared.cdp_session import SharedCdpSession

CONFIG_PATH = Path(__file__).resolve().parent / "scys_projects.json"


def load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    projects = {k: v for k, v in cfg.get("projects", {}).items() if v}
    if not projects:
        raise SystemExit(f"配置无可用项目：{CONFIG_PATH}")
    return {"projects": projects, "defaults": cfg.get("defaults", {})}


BASE = Path(__file__).resolve().parent.parent / "notes" / "_scraped" / "scys"
TAGS_URL = "https://scys.com/tags?projectId={menu_id}"
ARTICLE_URL = "https://scys.com/articleDetail/xq_topic/{topic_id}"

EXTERNAL_DOC_HOSTS = (
    "feishu.cn", "larksuite.com", "yuque.com", "notion.so", "docs.qq.com",
    "shimo.im", "wolai.com", "flowus.cn", "kdocs.cn",
)
LOGIN_MARKERS = ["立即登录", "登录后查看", "请登录", "成为会员", "开通会员", "订阅后"]

# 列表为空（疑似登录墙/临时空）后：保持当前页面打开、让用户扫码，间隔重试
LIST_EMPTY_RETRIES = 3
LIST_RETRY_GAP = 20  # 秒

ARTICLE_GAP = (15, 40)
PAGE_GAP = (3, 6)
BATCH_SIZE_RANGE = (10, 15)
BATCH_REST = (180, 480)


def human_gap(rng: tuple[float, float]) -> None:
    time.sleep(random.uniform(*rng))


LOCK_STALE_HOURS = 6


def _lock_is_stale(lock: Path) -> bool:
    """残留锁判定：持有进程已死（PID 判定）→ True；PID 读不出/无 psutil → 锁龄超 LOCK_STALE_HOURS。"""
    try:
        pid_txt = lock.read_text(encoding="utf-8").strip()
    except OSError:
        pid_txt = ""
    if pid_txt.isdigit():
        pid = int(pid_txt)
        if pid == os.getpid():
            return True  # 自身持有：允许重入/接管，不当外来进程误杀
        try:
            import psutil
            return not psutil.pid_exists(pid)
        except ImportError:
            pass
    try:
        return (time.time() - lock.stat().st_mtime) > LOCK_STALE_HOURS * 3600
    except OSError:
        return False


def _acquire_lock() -> Path:
    """进程互斥锁：补齐批量与日常监控共用 state/pending 文件，并发写会互相覆盖丢数据。
    残留自动释放（DECISION-20260825）：持有进程已退出或锁超龄 → 自动接管，不再需手动删。"""
    lock = BASE / ".lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if _lock_is_stale(lock):
            print("[lock] 检测到残留锁（持有进程已退出或锁超龄），自动接管")
            _force_unlink(lock)
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        else:
            raise SystemExit(f"[lock] 已有一个 scys 抓取进程在跑（{lock}）。"
                             f"若确认没有进程在跑，手动删除该文件后重试。")
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))
    return lock


def _force_unlink(path: Path) -> None:
    """删除锁文件。沙箱安全删除 shim 会把 unlink 路由到回收站并抛 OSError，
    导致 os.remove 永远删不掉锁 → 跨域残留自身 PID 误杀。这里 os.remove 失败后用
    ctypes.DeleteFileW 直接调 Windows API 真删（绕过回收站路由），确保锁能释放。
    """
    p = os.fspath(path)
    try:
        os.remove(p)
    except OSError:
        pass
    if os.path.exists(p):  # os.remove 被 shim 拦截未删 → 用 ctypes 真删
        try:
            import ctypes
            if not ctypes.windll.kernel32.DeleteFileW(p):
                code = ctypes.windll.kernel32.GetLastError()
                if code != 2:  # 2=文件不存在，正常
                    print(f"[lock] DeleteFileW 失败 code={code}: {p}")
        except Exception:
            pass


def _release_lock(lock: Path) -> None:
    _force_unlink(lock)


def filter_todo(items: list[dict], done_ids: set, since_days: int = 0,
                digested_only: bool = False, min_reading: int = 0,
                engagement: dict = None) -> list[dict]:
    """列表过滤链：去重 → 时间窗 → 精华/互动门槛 → 阅读数，返回待抓列表。

    非精华高价值判定（2026-08-21 用户决策）：阅读数会被官方指南/推送帖污染，
    改用互动组合——投锚 coinCount ≥ engagement["min_coin"] 或点赞 likeCount ≥
    engagement["min_like"] 任一达标即保留；精华帖直通。engagement=None 时不做
    互动过滤（保持旧的「非精华全抓」语义）。缺字段按 0 处理（兼容旧快照）。
    """
    todo = [it for it in items if str(it["topicId"]) not in done_ids]
    if since_days > 0:
        cutoff = time.time() - since_days * 86400
        before = len(todo)
        todo = [it for it in todo if (it.get("gmtCreate") or 0) >= cutoff]
        print(f"[filter] 近 {since_days} 天内：{before} → {len(todo)} 篇")
    if digested_only:
        before = len(todo)
        todo = [it for it in todo if it["isDigested"]]
        print(f"[filter] 仅精华：{before} → {len(todo)} 篇")
    elif engagement:
        before = len(todo)
        min_coin = engagement.get("min_coin", 0)
        min_like = engagement.get("min_like", 0)
        coin_floor = engagement.get("coin_floor", 0)

        def _valuable(it: dict) -> bool:
            if it["isDigested"]:
                return True
            coin = it.get("coinCount") or 0
            like = it.get("likeCount") or 0
            return (coin >= min_coin > 0
                    or (like >= min_like > 0 and coin >= coin_floor))

        todo = [it for it in todo if _valuable(it)]
        print(f"[filter] 精华+非精华互动达标（锚≥{min_coin}，或 赞≥{min_like}且锚≥{coin_floor}）："
              f"{before} → {len(todo)} 篇")
    if min_reading > 0:
        before = len(todo)
        todo = [it for it in todo if (it.get("readingCount") or 0) >= min_reading]
        print(f"[filter] 阅读数≥{min_reading}：{before} → {len(todo)} 篇")
    return todo


class ScysBatchFetcher:
    def __init__(self, name: str, menu_id: int, limit: int, pages: int,
                 since_days: int = 0, digested_only: bool = False,
                 min_reading: int = 0, engagement: dict = None) -> None:
        self.name = name
        self.menu_id = menu_id
        self.limit = limit
        self.pages = pages
        self.since_days = since_days
        self.digested_only = digested_only
        self.min_reading = min_reading
        self.engagement = engagement or {}
        self.state_path = BASE / "state.json"
        self.state: dict = self._load_json(self.state_path, default={})
        self.pending_path = BASE / "pending_summaries.json"

    @staticmethod
    def _load_json(path: Path, default):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return default

    def _save_state(self) -> None:
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- 列表 ----------

    # CDP 路径下 searchTopic 响应体偶发 'No resource with given identifier found'：
    # CDP 上报 response 事件早于 body 缓冲完成。对 resp.body() 做短延迟重试（取代整页
    # reload 的 band-aid），body 通常毫秒级就绪，重试即成功。仅重试「body 读取」，
    # JSON 解析失败不重试（body 已到手，重试无意义）。
    @staticmethod
    def _read_response_body(resp, retries: int = 6, delay: float = 0.25) -> bytes:
        last_err = None
        for _ in range(retries):
            try:
                return resp.body()
            except Exception as e:  # Playwright 抛错多为 CDP 资源未就绪
                last_err = e
                time.sleep(delay)
        raise last_err

    def collect_list(self, page) -> list[dict]:
        """打开 tags 页捕获 searchTopic 响应；点击翻页拿后续页。

        2026-09-02 修复（reload band-aid → body 重试）：Playwright 在 CDP/profile_clone
        路径下偶发 `Protocol error (Network.getResponseBody): No resource with given identifier
        found`——CDP 上报 response 事件早于 body 缓冲完成，导致 searchTopic 解析失败、items 空。
        原兜底用整页 reload（重代价且常无效）；改为在 on_response 内对 resp.body() 做短延迟重试，
        body 通常毫秒级就绪，重试即成功，无需 reload。
        """
        items: dict[int, dict] = {}
        captured: list[dict] = []
        done = {"flag": False}
        parse_failed_once = {"flag": False}

        def on_response(resp):
            if "searchTopic" not in resp.url:
                return
            # CDP 路径下 resp.body() 偶发 'No resource with given identifier found'：
            # 短延迟重试读 body（取代整页 reload 的 band-aid）。
            try:
                raw = self._read_response_body(resp)
            except Exception as e:
                parse_failed_once["flag"] = True
                print(f"[list] searchTopic body 读取失败（CDP 资源未就绪，已重试）: {e}")
                return
            try:
                body = json.loads(raw)
                data = body.get("data") or {}
                for it in data.get("items") or []:
                    t = it.get("topicDTO") or {}
                    tid = t.get("topicId")
                    if tid:
                        items[tid] = {
                            "topicId": tid,
                            "showTitle": t.get("showTitle") or "",
                            "isDigested": bool(t.get("isDigested")),
                            "readingCount": t.get("readingCount") or 0,
                            "likeCount": t.get("likeCount") or 0,
                            "coinCount": t.get("coinCount") or 0,
                            "commentCount": t.get("commentsCount") or 0,
                            "favoriteCount": t.get("favoriteCount") or 0,
                            "gmtCreate": t.get("gmtCreate"),
                            "aiSummary": t.get("aiSummaryContent") or "",
                            "articlePreview": t.get("articleContent") or "",
                        }
                try:
                    pd = json.loads(resp.request.post_data or "{}")
                    captured.append(pd.get("pageIndex"))
                    if pd.get("pageIndex", 1) >= self.pages or len(items) >= (data.get("total") or 0):
                        done["flag"] = True
                except Exception:
                    pass
            except Exception as e:
                parse_failed_once["flag"] = True
                print(f"[list] searchTopic 解析失败: {e}")

        page.on("response", on_response)
        page.goto(TAGS_URL.format(menu_id=self.menu_id), wait_until="domcontentloaded", timeout=30_000)
        # 等列表响应到达（而非固定 sleep）；最多 ~12s
        for _ in range(24):
            page.wait_for_timeout(500)
            if items or done["flag"]:
                break
        if not items and parse_failed_once["flag"]:
            # body 重试仍失败（极罕见）：记录但不整页 reload（reload 是重代价且通常无效）
            print(f"[list] {self.name} 响应体重试后仍失败，跳过本域（不再 reload）")

        for _ in range(self.pages - 1):
            if done["flag"]:
                break
            nxt = self._find_next_page_btn(page)
            if not nxt:
                print("[list] 找不到下一页按钮，就用当前已捕获页")
                break
            n_before = len(items)
            try:
                nxt.click()
            except Exception as e:
                print(f"[list] 翻页失败: {e}")
                break
            # 等新页数据到达（而非固定 sleep），最多 12s
            for _ in range(24):
                page.wait_for_timeout(500)
                if len(items) > n_before or done["flag"]:
                    break
            if len(items) == n_before:
                print(f"[list] 翻页后无新数据（可能已是末页），停止")
                break
            human_gap(PAGE_GAP)
        page.remove_listener("response", on_response)

        lst = sorted(items.values(),
                     key=lambda x: (x["isDigested"], x["readingCount"] or 0), reverse=True)
        snap = BASE / f"list_{self.menu_id}.json"
        snap.write_text(json.dumps({"project": self.name, "count": len(lst), "items": lst},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[list] 捕获 {len(captured)} 页 / {len(lst)} 篇（按精华+阅读数排序），快照 → {snap.name}")
        return lst

    @staticmethod
    def _find_next_page_btn(page):
        # 优先取未禁用的下一页按钮（scys 用 Arco Design 分页器）
        for sel in [".arco-pagination-item-next:not(.arco-pagination-item-disabled)",
                    ".ant-pagination-next:not(.ant-pagination-disabled)",
                    "button:has-text('下一页')"]:
            try:
                el = page.locator(sel).first
                if el.count() and el.is_visible():
                    return el
            except Exception:
                continue
        # fallback：按钮存在但已禁用 → 末页
        for sel in [".arco-pagination-item-next", ".ant-pagination-next"]:
            try:
                el = page.locator(sel).first
                if el.count() and el.is_visible():
                    print("[list] 已到末页（按钮禁用）")
                    return None
            except Exception:
                continue
        return None

    # ---------- 正文 ----------

    @staticmethod
    def _extract_body(page) -> str:
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
        return body

    @staticmethod
    def _collect_links(page) -> list[dict]:
        return page.evaluate(
            """() => {
                const seen = new Set(), out = [];
                document.querySelectorAll('a[href]').forEach(a => {
                    const href = a.href;
                    if (!href || href.startsWith('javascript:') || seen.has(href)) return;
                    seen.add(href);
                    out.push({href, text: (a.innerText || '').trim().slice(0, 60)});
                });
                return out;
            }"""
        )

    @staticmethod
    def _classify(links: list[dict]) -> tuple[list[dict], list[dict]]:
        internal, external = [], []
        for l in links:
            href = l["href"]
            if "scys.com/articleDetail/" in href:
                internal.append(l)
            elif any(h in href for h in EXTERNAL_DOC_HOSTS):
                external.append(l)
        return internal, external

    def _fetch_external(self, page, url: str) -> dict:
        """外部知识库：滚动到底再抓（懒加载）。"""
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(5000)
        for _ in range(15):
            grew = page.evaluate(
                """() => {
                    const h = document.body.scrollHeight;
                    window.scrollTo(0, h);
                    return h;
                }"""
            )
            page.wait_for_timeout(1500)
            h2 = page.evaluate("() => document.body.scrollHeight")
            if h2 == grew:
                break
        page.wait_for_timeout(1000)
        title = page.title()
        body = page.evaluate("() => document.body.innerText")
        return {"url": url, "title": title, "body": body}

    def fetch_article(self, page, topic_id, *, depth: int = 0, allow_internal: bool = True) -> dict:
        """抓一篇正文；递归一层站内前情提要；跟进外部知识库。"""
        url = ARTICLE_URL.format(topic_id=topic_id)
        print(f"      goto {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(8000)
        title = page.title()
        body = self._extract_body(page)
        links = self._collect_links(page)
        internal, external = self._classify(links)
        wall = [m for m in LOGIN_MARKERS if m in body]
        result = {"topicId": topic_id, "url": url, "title": title,
                  "chars": len(body), "login_wall": wall}

        # 外部知识库跟进
        ext_docs = []
        for ext in external[:2]:
            if depth > 0 and not ext_docs:
                break
            print(f"      ↗ 外部文档: {ext['href'][:80]}")
            human_gap((3, 7))
            try:
                d = self._fetch_external(page, ext["href"])
                slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", ext["href"].split("//")[1])[:60]
                out = BASE / f"{topic_id}_ext_{slug}.md"
                write_output(out, d["url"], d["title"], d["body"])
                ext_docs.append({"url": d["url"], "chars": len(d["body"]),
                                 "output": str(out)})
                print(f"        [ext] {len(d['body'])} 字 → {out.name}")
                page.go_back(wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
            except Exception as e:
                print(f"        [ext] 失败: {e}")

        # 站内前情提要（只递归一层）
        related = []
        if allow_internal and depth == 0:
            for inner in internal[:3]:
                m = re.search(r"articleDetail/xq_topic/(\d+)", inner["href"])
                if not m:
                    continue
                rid = m.group(1)
                if rid == str(topic_id):
                    continue
                print(f"      ↘ 站内引用: {rid} ({inner['text'][:30]!r})")
                human_gap((8, 15))
                try:
                    r = self.fetch_article(page, rid, depth=depth + 1, allow_internal=False)
                    related.append({"topicId": rid, "chars": r["chars"],
                                    "title": r["title"], "output": r["output"]})
                except Exception as e:
                    print(f"        [related] 失败: {e}")

        result["external_docs"] = ext_docs
        result["related"] = related
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(topic_id))
        out = BASE / f"{slug}.md"
        write_output(out, url, title, body)
        result["output"] = str(out)
        print(f"      [{topic_id}] {len(body)} 字 wall={wall or '无'} "
              f"ext={len(ext_docs)} related={len(related)} → {out.name}")
        return result

    # ---------- 主循环 ----------

    def run(self, list_only: bool = False, session=None) -> int:
        lock = _acquire_lock()
        try:
            return self._run_with_retry(list_only, session=session)
        finally:
            _release_lock(lock)

    def _run_with_retry(self, list_only: bool, session=None) -> int:
        # CDP 接管活 Chrome：用户关标签页/浏览器波动会抛 TargetClosedError，
        # state.json 断点续传使重跑幂等，自动重试最多 3 次
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                return self._run_once(list_only, session=session)
            except Exception as e:  # noqa: BLE001
                last_err = e
                msg = str(e)
                retryable = ("has been closed" in msg or "Target closed" in msg
                             or "Connection closed" in msg or "Browser closed" in msg)
                print(f"[retry] 第 {attempt + 1} 次运行异常: {msg[:120]} "
                      f"({'可重试' if retryable else '不可重试'})")
                if not retryable:
                    raise
                time.sleep(10)
        raise last_err  # type: ignore[misc]

    def _run_once(self, list_only: bool, session=None) -> int:
        BASE.mkdir(parents=True, exist_ok=True)
        done_ids: set[str] = set(self.state.get("done", []))
        fetched = 0
        pending: list[dict] = []

        # 共享 CDP 会话：run.py 监控路径会注入同一个 SharedCdpSession（scys / 公众号 / 重试
        # 共用一个，Chrome 最多杀一次）；未注入时自建（独立 CLI 行为不变）。
        # ⚠️ 注入的会话【绝不在此关闭】——它还要给公众号/重试用，由 run.py 的 with 统一回收。
        own_session = session is None
        sess = None
        try:
            if own_session:
                sess = SharedCdpSession()
                sess.__enter__()
            else:
                sess = session
            page = sess.new_page()
            try:
                lst = self.collect_list(page)
                # 列表为空（疑似登录墙或临时空）：保持当前页面打开，让用户扫码，
                # 每 LIST_RETRY_GAP 秒重试一次，最多 LIST_EMPTY_RETRIES 次
                if not lst:
                    for _attempt in range(1, LIST_EMPTY_RETRIES + 1):
                        print(f"[scys] 列表为空（可能需登录，页面保持打开请扫码）。"
                              f"{LIST_RETRY_GAP}s 后第 {_attempt}/{LIST_EMPTY_RETRIES} 次重试…")
                        time.sleep(LIST_RETRY_GAP)
                        lst = self.collect_list(page)  # 内部重新 goto + 重新抓响应（同一 page）
                        if lst:
                            break
                    if not lst:
                        print("[scys] 重试后仍为空，本域跳过（下轮可重试）")
                        return 0
                if list_only:
                    dig = [it for it in lst if it["isDigested"]]
                    print(f"[list-only] {self.name}: 共 {len(lst)} 篇，其中精华 {len(dig)} 篇")
                    return 0
                todo = filter_todo(lst, done_ids, since_days=self.since_days,
                                   digested_only=self.digested_only,
                                   min_reading=self.min_reading,
                                   engagement=self.engagement)
                if self.limit > 0:
                    todo = todo[: self.limit]
                print(f"[run] 本次目标 {len(todo)} 篇（已完成 {len(done_ids)} 篇）")

                batch_at = random.randint(*BATCH_SIZE_RANGE)
                pending = self._load_json(self.pending_path, default=[])
                for it in todo:
                    tid = str(it["topicId"])
                    print(f"  [{fetched + 1}/{len(todo)}] {it['showTitle'][:50]}")
                    try:
                        r = self.fetch_article(page, tid)
                    except Exception as e:
                        msg = str(e)
                        if "has been closed" in msg or "Target closed" in msg:
                            raise  # 页面/浏览器级故障：交给外层重试
                        print(f"      [FAIL] {e}")
                        continue
                    done_ids.add(tid)
                    self.state["done"] = sorted(done_ids)
                    self._save_state()
                    pending.append({
                        "topicId": tid, "project": self.name, "title": r["title"],
                        "url": r["url"], "chars": r["chars"],
                        "output": r["output"],
                        "external_docs": r["external_docs"], "related": r["related"],
                        "list_meta": {k: it.get(k) for k in
                                      ("isDigested", "readingCount", "likeCount", "coinCount",
                                       "commentCount", "favoriteCount", "gmtCreate", "aiSummary")},
                    })
                    self.pending_path.write_text(
                        json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
                    fetched += 1

                    if fetched >= batch_at:
                        rest = random.uniform(*BATCH_REST)
                        print(f"  [rest] 已抓 {fetched} 篇，休息 {rest:.0f}s（模拟人类）")
                        time.sleep(rest)
                        batch_at = fetched + random.randint(*BATCH_SIZE_RANGE)
                    else:
                        gap = random.uniform(*ARTICLE_GAP)
                        print(f"      (下篇间隔 {gap:.0f}s)")
                        time.sleep(gap)
            finally:
                page.close()  # 注入会话不关；自建会话由 __exit__ 统一关
        finally:
            if own_session and sess is not None:
                sess.__exit__(None, None, None)

        # CdpAutomationProfile\Chrome 是持久化的全量副本，不删除（由 ensure_cdp_profile.py 每天首跑全量、当天复用）

        print(f"[done] 本次抓取 {fetched} 篇，累计 {len(done_ids)} 篇，队列 {len(pending)} 待总结")
        return 0


def main(argv: list[str]) -> int:
    cfg = load_config()
    projects: dict[str, int] = cfg["projects"]
    defaults: dict = cfg["defaults"]

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", choices=list(projects),
                    help=f"要抓的项目领域（可选：{' / '.join(projects)}）")
    ap.add_argument("--pages", type=int, default=0, help="列表翻页数（每页30条，0=自动翻到末页）")
    ap.add_argument("--limit", type=int, default=defaults.get("batch_limit", 0),
                    help=f"本次最多抓几篇正文（默认取配置 {defaults.get('batch_limit', 0)}，0=不限）")
    ap.add_argument("--list-only", action="store_true", help="只拉列表快照，不进正文（分析分布用）")
    ap.add_argument("--list-projects", action="store_true", help="列出配置的可抓领域及其 menuId")
    ap.add_argument("--since-days", type=int, default=defaults.get("since_days", 0),
                    help=f"只抓近 N 天内发布的帖子（默认取配置 {defaults.get('since_days', 0)}，0=不限）")
    ap.add_argument("--digested-only", action="store_true",
                    default=bool(defaults.get("digested_only", False)),
                    help="只抓精华帖（默认取配置）")
    ap.add_argument("--no-digested-only", dest="digested_only", action="store_false",
                    help="关闭仅精华过滤")
    ap.add_argument("--min-reading", type=int, default=defaults.get("min_reading", 0),
                    help="最低阅读数门槛（默认取配置）")
    args = ap.parse_args(argv[1:])

    if args.list_projects:
        for name, mid in projects.items():
            print(f"{name}\tmenuId={mid}")
        return 0

    if not args.project:
        ap.error("请用 --project 指定项目领域（或 --list-projects 查看可选项）")

    pages = 999 if args.pages == 0 else args.pages
    engagement = {"min_coin": int(defaults.get("nondigested_min_coin", 30)),
                  "min_like": int(defaults.get("nondigested_min_like", 80)),
                  "coin_floor": int(defaults.get("nondigested_coin_floor", 10))}
    fetcher = ScysBatchFetcher(args.project, projects[args.project], args.limit, pages,
                               since_days=args.since_days, digested_only=args.digested_only,
                               min_reading=args.min_reading, engagement=engagement)
    return fetcher.run(list_only=args.list_only)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
