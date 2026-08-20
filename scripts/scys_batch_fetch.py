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
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from login_cdp_fetch import discover_chrome_devtools, write_output

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

ARTICLE_GAP = (15, 40)
PAGE_GAP = (3, 6)
BATCH_SIZE_RANGE = (10, 15)
BATCH_REST = (180, 480)


def human_gap(rng: tuple[float, float]) -> None:
    time.sleep(random.uniform(*rng))


class ScysBatchFetcher:
    def __init__(self, name: str, menu_id: int, limit: int, pages: int,
                 since_days: int = 0, digested_only: bool = False,
                 min_reading: int = 0) -> None:
        self.name = name
        self.menu_id = menu_id
        self.limit = limit
        self.pages = pages
        self.since_days = since_days
        self.digested_only = digested_only
        self.min_reading = min_reading
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

    def collect_list(self, page) -> list[dict]:
        """打开 tags 页捕获 searchTopic 响应；点击翻页拿后续页。"""
        items: dict[int, dict] = {}
        captured: list[dict] = []
        done = {"flag": False}

        def on_response(resp):
            if "searchTopic" not in resp.url:
                return
            try:
                body = resp.json()
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
                            "gmtCreate": t.get("gmtCreate"),
                            "aiSummary": t.get("aiSummaryContent") or "",
                            "articlePreview": t.get("articleContent") or "",
                        }
                pd = json.loads(resp.request.post_data or "{}")
                captured.append(pd.get("pageIndex"))
                if pd.get("pageIndex", 1) >= self.pages or len(items) >= (data.get("total") or 0):
                    done["flag"] = True
            except Exception:
                pass

        page.on("response", on_response)
        print(f"[list] goto tags 页（{self.name}={self.menu_id}）")
        page.goto(TAGS_URL.format(menu_id=self.menu_id), wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(6000)

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

    def run(self, list_only: bool = False) -> int:
        # CDP 接管活 Chrome：用户关标签页/浏览器波动会抛 TargetClosedError，
        # state.json 断点续传使重跑幂等，自动重试最多 3 次
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                return self._run_once(list_only)
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

    def _run_once(self, list_only: bool) -> int:
        from playwright.sync_api import sync_playwright

        BASE.mkdir(parents=True, exist_ok=True)
        done_ids: set[str] = set(self.state.get("done", []))
        port, ws_path = discover_chrome_devtools()
        print(f"[CDP] ws://127.0.0.1:{port}{ws_path}")

        fetched = 0
        pending: list[dict] = []
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"ws://127.0.0.1:{port}{ws_path}")
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()

            lst = self.collect_list(page)
            if list_only:
                dig = [it for it in lst if it["isDigested"]]
                print(f"[list-only] {self.name}: 共 {len(lst)} 篇，其中精华 {len(dig)} 篇")
                page.close()
                return 0
            todo = [it for it in lst if str(it["topicId"]) not in done_ids]
            if self.since_days > 0:
                cutoff = time.time() - self.since_days * 86400
                before = len(todo)
                todo = [it for it in todo if (it.get("gmtCreate") or 0) >= cutoff]
                print(f"[filter] 近 {self.since_days} 天内：{before} → {len(todo)} 篇")
            if self.digested_only:
                before = len(todo)
                todo = [it for it in todo if it["isDigested"]]
                print(f"[filter] 仅精华：{before} → {len(todo)} 篇")
            if self.min_reading > 0:
                before = len(todo)
                todo = [it for it in todo if (it.get("readingCount") or 0) >= self.min_reading]
                print(f"[filter] 阅读数≥{self.min_reading}：{before} → {len(todo)} 篇")
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
                                  ("isDigested", "readingCount", "likeCount", "gmtCreate", "aiSummary")},
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
            page.close()

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
    fetcher = ScysBatchFetcher(args.project, projects[args.project], args.limit, pages,
                               since_days=args.since_days, digested_only=args.digested_only,
                               min_reading=args.min_reading)
    return fetcher.run(list_only=args.list_only)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
