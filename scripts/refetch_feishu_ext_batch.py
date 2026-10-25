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

# 「疑似没抓全」哨兵阈值（行数）。
# 背景（2026-10-26）：本脚本的通过门槛只有 >=300 字，而飞书妙记(minutes)的转写列表
# 是 React 虚拟列表 .rc-virtual-list-holder，当时容器优先级缺这一档 → 退 window 滚动
# 纹丝不动，1h27m 的会议只拿到 **4 行 / 1093 字**，日志却照样打 ✅——bug 硬是藏了一整年。
# 实测 33 条 healthy 文件行数最少 69 行、中位 182 行，故取 30 线留足安全余量，不误伤。
THIN_LINES = 30

FEISHU_RE = re.compile(r"https?://[^\s)\]]+(?:feishu\.cn|larksuite\.com)[^\s)\]]*")
DOC_RE = re.compile(r"(wiki|docx|doc|s/)")


def extract_feishu_url(main_txt: str) -> str:
    links = [l for l in FEISHU_RE.findall(main_txt) if DOC_RE.search(l)]
    if not links:
        return ""
    url = links[0].rstrip(".…。，,）)】]")
    return url


def url_from_ext_file(e: Path) -> str:
    """从单个 `<tid>_ext_*.md` 头部的 `> 来源：` 取完整 URL（当时来自 <a href>，非显示文本）。"""
    for ln in e.read_text(encoding="utf-8").splitlines()[:6]:
        if ln.startswith("> 来源："):
            return ln.split("> 来源：", 1)[1].strip()
    return ""


def full_url_from_ext(tid: str) -> str:
    """优先从现有 `<tid>_ext_*.md` 头部的 `> 来源：` 取完整 URL。"""
    for e in sorted(SCYS_BASE.glob(f"{tid}_ext_*.md")):
        url = url_from_ext_file(e)
        if url:
            return url
    return ""


def slug_of(url: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", url.split("//")[1])[:60]


def _touch(sess) -> None:
    """给 CDP daemon 刷一次 last_access（长任务保活；非 daemon 模式静默 no-op）。

    背景：daemon reaper 按 CDP_IDLE_TIMEOUT（默认 300s）回收「没人用」的 Chrome，
    而 last_access 只在 ACQUIRE/RELEASE 刷新。本脚本每条 goto+滚动抓全文可超 1 分钟，
    不主动打招呼就会被误杀（2026-10-25 实测：第 5 条后 Chrome 被回收，余 29 条全灭）。
    """
    fn = getattr(sess, "keepalive", None)
    if callable(fn):
        try:
            fn()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ids", default="", help="逗号分隔 tid，只抓指定")
    ap.add_argument("--ids-file", default="",
                    help="文件路径，每行一个 tid；跳过 resum_ext_batches，直接按现有 ext 文件头 URL 重抓"
                         "（一条帖多个外链各抓一次）")
    ap.add_argument("--manifest", default="", help="manifest.json 路径，配合 --ids-file 取标题（可选）")
    args = ap.parse_args()

    if args.ids_file:
        titles = {}
        if args.manifest:
            for m in json.loads(Path(args.manifest).read_text(encoding="utf-8")):
                titles[m["topicId"]] = m.get("title", "")
        want = [l.strip() for l in Path(args.ids_file).read_text(encoding="utf-8").splitlines() if l.strip()]
        items = []
        for tid in want:
            exts = sorted(SCYS_BASE.glob(f"{tid}_ext_*.md"))
            if exts:
                for e in exts:
                    url = url_from_ext_file(e)
                    if url:
                        items.append({"tid": tid, "title": titles.get(tid, ""), "url": url})
            else:
                raw = SCYS_BASE / f"{tid}.md"
                url = extract_feishu_url(raw.read_text(encoding="utf-8")) if raw.exists() else ""
                if url:
                    items.append({"tid": tid, "title": titles.get(tid, ""), "url": url})
    else:
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
        thin: list[tuple[str, int, int]] = []  # (文件名, 行数, 字数) 疑似抓不全
        for i, x in enumerate(todo):
            _touch(sess)  # daemon 保活：每条前刷一次 last_access
            tid, url = x["tid"], x["url"]
            out = SCYS_BASE / f"{tid}_ext_{slug_of(url)}.md"
            print(f"[{i+1}/{len(todo)}] {x['title'][:24]} -> {out.name}", flush=True)
            got = ""
            for attempt in range(1, MAX_RETRY + 1):
                _touch(sess)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(SETTLE_MS)
                    _title, body = collect_full_text(page)
                    _touch(sess)  # 大文档滚动抓取可能单条超 300s，抓完再刷一次
                    if len(body) >= 300:
                        got = body
                        break
                    print(f"   ⚠️ 第{attempt}次过短 {len(body)} 字，{GAP[0]}s 后重试", flush=True)
                    time.sleep(__import__("random").uniform(*GAP))
                except Exception as e:
                    print(f"   ❌ 第{attempt}次异常 {e}，重试", flush=True)
                    time.sleep(__import__("random").uniform(*GAP))
            if got:
                nl = len([x for x in got.splitlines() if x.strip()])
                if nl < THIN_LINES:
                    print(f"   ⚠️ 仅 {nl} 行（<{THIN_LINES}）：疑似只抓到首屏、虚拟化容器未命中"
                          f" → 核对 shared.feishu_ext 容器优先级，本条需人工确认", flush=True)
                    thin.append((out.name, nl, len(got)))
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
        if thin:
            print(f"[feishu-ext] ⚠️ 疑似抓不全 {len(thin)} 条（行数 <{THIN_LINES}，可能是容器没命中"
                  f"而非文档真短）：", flush=True)
            for name, nl, nc in thin:
                print(f"   - {name}: {nl} 行 / {nc} 字", flush=True)
    finally:
        sess.__exit__(None, None, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
