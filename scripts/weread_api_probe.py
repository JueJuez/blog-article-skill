#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/weread_api_probe.py — 在**已登录的 weread 页面上下文内** fetch 接口探测。

为什么在页面内 fetch 而不是用 requests：
  1. 同源请求自动带 cookie，**不需要把 wr_skey/wr_vid 等登录凭据导出到磁盘/上下文**；
  2. 请求头、Referer、UA 全部与真实浏览器一致，风控风险最低；
  3. 用户要求低频率：本脚本一次只发你显式指定的那几个请求，无重试、无翻页、无心跳。

用法：
    # 相对路径会自动补 https://weread.qq.com 前缀
    python scripts/weread_api_probe.py --path /web/user/userinfo --path /web/shelf/bookIds
    python scripts/weread_api_probe.py --path "/web/mp/articles?bookId=MP_WXS_xxx&offset=0" --dump 2000

产物：_tmp/weread_probe/api_probe.jsonl（含 status + 截断响应体，cookie 不入文件）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared.cdp_session import SharedCdpSession  # noqa: E402

BASE = "https://weread.qq.com"
OUT_DIR = os.path.join(ROOT, "_tmp", "weread_probe")

JS = """async (args) => {
  const [url, maxLen, method, body] = args;
  try {
    const opt = {
      credentials: 'include',
      method: method || 'GET',
      headers: { Accept: 'application/json, text/plain, */*' },
    };
    if (body) {
      opt.headers['Content-Type'] = 'application/json;charset=UTF-8';
      opt.body = body;
    }
    const r = await fetch(url, opt);
    const t = await r.text();
    return { ok: true, status: r.status, body: t.slice(0, maxLen), len: t.length };
  } catch (e) {
    return { ok: false, err: String(e) };
  }
}"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", action="append", default=[], help="相对路径或完整 URL，可重复")
    ap.add_argument("--post", default="", help="**先执行**的 POST 路径（写操作，会真实改动账号）")
    ap.add_argument("--body", default="", help="--post 的 JSON body")
    ap.add_argument("--dump", type=int, default=1200, help="响应体截断长度")
    ap.add_argument("--sleep", type=float, default=1.5, help="每个请求之间的间隔（秒）")
    args = ap.parse_args()
    if not args.path and not args.post:
        print("至少给一个 --path 或 --post")
        return 2

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "api_probe.jsonl")

    with SharedCdpSession() as s:
        page = s.page
        try:
            page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass
        cur = ""
        try:
            cur = page.url
        except Exception:  # noqa: BLE001
            pass
        print(f"[page] {cur[:120]}")
        if "weread.qq.com" not in cur:
            print("[nav] 当前页不在 weread 域，导航到首页（1 次请求）")
            page.goto(BASE + "/", wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(3000)

        seq = []
        if args.post:
            seq.append(("POST", args.post, args.body))  # 写操作先跑，后面的 GET 才好验证结果
        seq += [("GET", p, "") for p in args.path]

        for method, p, body in seq:
            url = p if p.startswith("http") else BASE + (p if p.startswith("/") else "/" + p)
            print(f"\n=== {method} {url}")
            if body:
                print(f"  body: {body}")
            try:
                res = page.evaluate(JS, [url, args.dump, method, body])
            except Exception as e:  # noqa: BLE001
                print(f"  <evaluate 失败 {type(e).__name__}: {e}>")
                continue
            if not res.get("ok"):
                print(f"  <fetch 失败: {res.get('err')}>")
            else:
                print(f"  status={res['status']} len={res['len']}")
                print("  body:", (res.get("body") or "")[: args.dump])
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.strftime("%F %T"), "method": method, "url": url, **res},
                                   ensure_ascii=False) + "\n")
            time.sleep(args.sleep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
