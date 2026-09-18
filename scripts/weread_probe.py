#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/weread_probe.py — 微信读书(weread) 公众号接口 · CDP 被动抓包探针。

## 为什么有这个脚本

原公众号源 = wewe-rss 的公开实例 `weread.111965.xyz`，2026-08-28 起 **502 已死**
（见 `monitors/PROXY_NOTES.md` §9，两层故障：本机 DNS 被 Steam++ 劫持 + 源站真死）。
而 wewe-rss 本身就是拿**微信读书的登录态**去拉公众号文章列表的——第三方实例死了，
但 weread 官方接口还在。**自建 = 把凭据换成我们自己的 weread cookie**。

本脚本负责第一阶段：找出 weread 侧「按公众号拉文章列表」的接口到底长什么样。

## 设计原则（用户明确要求：低频率、少请求）

- **纯被动**：一个额外的网络请求都不发。不发心跳、不轮询服务端、不自动刷新/翻页、
  不预加载。只记录**浏览器（用户自己的操作）自然产生**的请求。
- **二维码靠本地截图交付**：不轮询登录状态接口，只按固定间隔本地截图（无网络开销）。
- **请求头脱敏落盘**：cookie / authorization 只记长度不记内容。

## 用法

    python scripts/launch_weread_probe.py                  # 推荐（DETACHED 后台，抗会话回收）
    python scripts/launch_weread_probe.py --duration 1800
    python scripts/weread_probe.py --url https://weread.qq.com/   # 前台跑（调试用）

参数：
    --url            起始导航 URL（默认 https://weread.qq.com/）
    --nav            启动后额外导航一次到该 URL（例如微信读书里的公众号页）
    --duration       运行时长（秒，默认 1800）
    --shot-interval  截图间隔（秒，默认 45）
    --out-dir        产物目录（默认 _tmp/weread_probe）

产物：
    _tmp/weread_probe/net-<ts>.jsonl   逐条请求记录（含响应体预览）
    _tmp/weread_probe/latest.png       最近一次截图（给用户看二维码）
    _tmp/weread_probe/probe.log        运行日志

停止：写 `_tmp/weread_probe/STOP` 文件 / --duration 到期 / 关掉被接管的 Chrome。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared.cdp_session import SharedCdpSession  # noqa: E402

DEFAULT_URL = "https://weread.qq.com/"
BODY_MAX = 8000
POST_MAX = 4000
STATIC_TYPES = {"image", "stylesheet", "script", "font", "media"}
SECRET_HEADERS = {"cookie", "authorization", "set-cookie", "x-skey", "x-vid"}


def _now() -> str:
    return datetime.now().strftime("%F %T")


def _log(msg: str) -> None:
    print(f"[{_now()}] {msg}", flush=True)


def _snap_headers(headers: dict) -> dict:
    """请求头脱敏：cookie/authorization 等只记长度，其余截断。"""
    out = {}
    for k, v in (headers or {}).items():
        if k.lower() in SECRET_HEADERS:
            out[k] = f"<omitted len={len(v)}>"
        else:
            out[k] = (v or "")[:200]
    return out


class Recorder:
    """把请求/响应追加写进 JSONL（每条一行，flush 保证后台进程被杀也不丢）。"""

    def __init__(self, path: Path):
        self.path = path
        self.fh = open(path, "a", encoding="utf-8")
        self.count = 0
        self.xhr = 0

    def write(self, entry: dict) -> None:
        self.count += 1
        if entry.get("type") in ("xhr", "fetch"):
            self.xhr += 1
        self.fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.fh.flush()

    def close(self) -> None:
        try:
            self.fh.close()
        except Exception:
            pass


def _make_handler(rec: Recorder, seen_hooked: set):
    def _on_response(resp) -> None:
        try:
            req = resp.request
            rtype = req.resource_type
            ctype = ""
            try:
                ctype = (resp.headers or {}).get("content-type", "") or ""
            except Exception:
                pass
            entry = {
                "ts": _now(),
                "method": req.method,
                "url": resp.url,
                "status": resp.status,
                "type": rtype,
                "ctype": ctype,
            }
            if rtype in STATIC_TYPES:
                rec.write(entry)  # 静态资源只记一行，不读 body
                return
            entry["req_headers"] = _snap_headers(req.headers)
            try:
                entry["post"] = (req.post_data or "")[:POST_MAX]
            except Exception:
                entry["post"] = ""
            if rtype in ("xhr", "fetch") or "json" in (ctype or "").lower():
                try:
                    entry["body"] = resp.text()[:BODY_MAX]
                except Exception as e:
                    entry["body_err"] = f"{type(e).__name__}: {str(e)[:120]}"
            elif rtype == "document":
                try:
                    entry["body"] = resp.text()[:2000]
                except Exception:
                    pass
            rec.write(entry)
            if rtype in ("xhr", "fetch"):
                _log(f"XHR {req.method} {resp.status} {resp.url[:160]}")
        except Exception as e:
            _log(f"[handler-error] {type(e).__name__} {str(e)[:120]}")

    def _on_page(page) -> None:
        key = id(page)
        if key in seen_hooked:
            return
        seen_hooked.add(key)
        try:
            page.on("response", _on_response)
            _log(f"已挂载监听: {page.url[:120]}")
        except Exception as e:
            _log(f"[hook-error] {e}")

    return _on_response, _on_page


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--nav", default="", help="启动后额外导航一次到该 URL")
    ap.add_argument("--duration", type=int, default=1800)
    ap.add_argument("--shot-interval", type=int, default=45)
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else Path(ROOT) / "_tmp" / "weread_probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    net_path = out_dir / f"net-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"
    shot_path = out_dir / "latest.png"
    stop_file = out_dir / "STOP"

    rec = Recorder(net_path)
    seen_hooked: set = set()
    _on_response, _on_page = _make_handler(rec, seen_hooked)

    _log(f"产物目录: {out_dir}")
    _log(f"网络记录: {net_path}")
    _log(f"启动 URL: {args.url}")

    sess = SharedCdpSession()
    page = sess.new_page()
    _on_page(page)
    try:
        page.goto(args.url, wait_until="domcontentloaded", timeout=30_000)
        _log(f"已导航: {args.url}")
    except Exception as e:
        _log(f"[goto-error] {type(e).__name__} {str(e)[:200]}")

    deadline = time.time() + args.duration
    next_shot = 0.0
    nav_done = False
    rc = 0
    try:
        while time.time() < deadline:
            if stop_file.exists():
                _log("检测到 STOP 文件，退出")
                break
            time.sleep(1)
            # 给后续新开的标签页也挂上监听（用户可能在新页里点公众号）
            try:
                for p in sess.context.pages:
                    _on_page(p)
            except Exception:
                pass
            now = time.time()
            if now >= next_shot:
                next_shot = now + args.shot_interval
                try:
                    page.screenshot(path=str(shot_path))
                    title = page.title()
                    _log(f"截图已更新 url={page.url[:120]} title={title[:80]} "
                         f"reqs={rec.count} xhr={rec.xhr}")
                except Exception as e:
                    _log(f"[shot-error] {type(e).__name__} {str(e)[:120]}")
                    try:
                        rc = 3
                        break  # 页面/浏览器没了，没必要空转
                    except Exception:
                        pass
            if args.nav and not nav_done and time.time() > deadline - args.duration + 5:
                nav_done = True
                try:
                    page.goto(args.nav, wait_until="domcontentloaded", timeout=30_000)
                    _log(f"二次导航: {args.nav}")
                except Exception as e:
                    _log(f"[nav-error] {e}")
    except KeyboardInterrupt:
        _log("收到中断，退出")
    finally:
        _log(f"结束：共记录 {rec.count} 条请求（其中 XHR/fetch {rec.xhr} 条）")
        rec.close()
        try:
            sess.close()
        except Exception:
            pass
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
