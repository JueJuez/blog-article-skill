# -*- coding: utf-8 -*-
"""按 ext 批次补抓飞书外链全文，覆盖截断版 `<tid>_ext_*.md`。

背景：scys_batch_fetch 的 _fetch_external 只滚 window 两次取一次 innerText，
飞书 wiki/docx 虚拟化渲染导致只抓到目录+开头（500~2000 字级）。
本脚本复用 `feishu_ext_refetch.collect_full_text`（增量滚动
`.bear-web-x-container` 容器 + 按行去重合并，逼近完整正文），
对 resum_ext_batches/ 里的引流帖逐个重抓飞书外链。

用法：
    python scripts/refetch_feishu_ext_batch.py [--limit N] [--ids 逗号tid]
产物：覆盖 notes/_scraped/scys/<tid>_ext_<slug>.md（命名与 scys_batch_fetch 一致）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from shared.cdp_session import SharedCdpSession  # noqa: E402
from feishu_ext_refetch import collect_full_text  # noqa: E402

EXT_DIR = ROOT / "notes" / "_meta" / "resum_ext_batches"
SCYS_BASE = ROOT / "notes" / "_scraped" / "scys"
GAP = (15, 25)
SETTLE_MS = 8000
MAX_RETRY = 3

FEISHU_RE = re.compile(r"https?://[^\s)\]]+(?:feishu\.cn|larksuite\.com)[^\s)\]]*")
DOC_RE = re.compile(r"(wiki|docx|doc|s/)")


def extract_feishu_url(main_txt: str) -> str:
    links = [l for l in FEISHU_RE.findall(main_txt) if DOC_RE.search(l)]
    if not links:
        return ""
    url = links[0].rstrip(".…。，,）)】]")
    return url


def full_url_from_ext(tid: str) -> str:
    """优先从现有 `<tid>_ext_*.md` 头部的 `> 来源：` 取完整 URL（当时来自 <a href>，非显示文本）。"""
    for e in sorted(SCYS_BASE.glob(f"{tid}_ext_*.md")):
        for ln in e.read_text(encoding="utf-8").splitlines()[:6]:
            if ln.startswith("> 来源："):
                return ln.split("> 来源：", 1)[1].strip()
    return ""


def slug_of(url: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", url.split("//")[1])[:60]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ids", default="", help="逗号分隔 tid，只抓指定")
    args = ap.parse_args()

    items = []
    for bp in sorted(EXT_DIR.glob("*.json")):
        for it in json.loads(bp.read_text(encoding="utf-8")):
            tid = re.search(r"xq_topic/(\d+)", it["url"]).group(1)
            url = full_url_from_ext(tid)
            if not url:
                raw = SCYS_BASE / f"{tid}.md"
                if raw.exists():
                    url = extract_feishu_url(raw.read_text(encoding="utf-8"))
            items.append({"tid": tid, "title": it["title"], "url": url})
    if args.ids:
        want = set(args.ids.split(","))
        items = [x for x in items if x["tid"] in want]
    if args.limit:
        items = items[: args.limit]
    todo = [x for x in items if x["url"]]
    print(f"[feishu-ext] 目标 {len(items)} 条，含飞书链接 {len(todo)} 条")

    sess = SharedCdpSession()
    sess.__enter__()
    try:
        page = sess.new_page()
        ok = fail = 0
        for i, x in enumerate(todo):
            tid, url = x["tid"], x["url"]
            out = SCYS_BASE / f"{tid}_ext_{slug_of(url)}.md"
            print(f"[{i+1}/{len(todo)}] {x['title'][:24]} -> {out.name}", flush=True)
            got = ""
            for attempt in range(1, MAX_RETRY + 1):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(SETTLE_MS)
                    _title, body = collect_full_text(page)
                    if len(body) >= 300:
                        got = body
                        break
                    print(f"   ⚠️ 第{attempt}次过短 {len(body)} 字，{GAP[0]}s 后重试", flush=True)
                    time.sleep(__import__("random").uniform(*GAP))
                except Exception as e:
                    print(f"   ❌ 第{attempt}次异常 {e}，重试", flush=True)
                    time.sleep(__import__("random").uniform(*GAP))
            if got:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                out.write_text(
                    f"> 来源：{url}\n> 抓取时间：{stamp}（懒加载全文重抓）\n\n---\n\n{got}\n",
                    encoding="utf-8")
                print(f"   ✅ {len(got)} 字符 / {len(got.splitlines())} 行 -> {out.name}", flush=True)
                ok += 1
            else:
                print(f"   ❌ 重试 {MAX_RETRY} 次仍失败", flush=True)
                fail += 1
            time.sleep(__import__("random").uniform(*GAP))
        print(f"[feishu-ext] 完成：成功 {ok} / 失败 {fail}", flush=True)
    finally:
        sess.__exit__(None, None, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
