#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/weread_cookie_export.py — 导出微信读书 cookie，并可选实测直调接口。

背景：公众号新源 = 微信读书官方接口（不经过任何第三方中转）。
  - 列表接口 `/web/mp/articles` 已被官方废弃（恒返回 -2041，2026-09-18 实测）
  - 唯一可用的是 `GET /api/mp/cover?bookId=MP_WXS_xxx` → 返回该号**最新一篇**
    （reviewId 形如 MP_WXS_xxx_<token>，token 段即 mp.weixin.qq.com/s/<token> 原文直链）
  - 正文走项目原有的 mp.weixin.qq.com 直连抓取，不额外依赖 weread

为什么导出 cookie：每天为了 4 个号各 1 个请求去开一次 Chrome 太重。cookie 导出后可用
requests 直调（每天共 4 个请求）。cookie 失效时重新扫码、再跑本脚本即可。

⚠️ 凭据安全：cookie 只写 `monitors/.weread_cookie`（本机，600 权限尽力而为），
   **永远不会打印到 stdout / 不进对话上下文**；本脚本只打印 cookie 名与长度。

用法：
    python scripts/weread_cookie_export.py                      # 仅导出
    python scripts/weread_cookie_export.py --test MP_WXS_2399233620   # 导出 + 打 1 个请求自测
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared.cdp_session import SharedCdpSession  # noqa: E402

COOKIE_PATH = os.path.join(ROOT, "monitors", ".weread_cookie")
BASE = "https://weread.qq.com"


def _collect(ctx) -> tuple[dict, str]:
    cookies = ctx.cookies()
    jar: dict = {}
    for c in cookies:
        dom = str(c.get("domain", ""))
        if "weread" not in dom:
            continue
        jar[str(c.get("name"))] = str(c.get("value", ""))
    header = "; ".join(f"{k}={v}" for k, v in sorted(jar.items()) if v)
    return jar, header


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", default="", help="给一个 MP_WXS_ bookId，导出后打 1 个请求自测")
    args = ap.parse_args()

    with SharedCdpSession() as s:
        jar, header = _collect(s.context)

    if not jar:
        print("[fail] 没拿到 weread cookie——浏览器里是否仍停留在登录页？")
        return 1
    print("[cookie] " + ", ".join(f"{k}({len(v)})" for k, v in sorted(jar.items())))
    for must in ("wr_skey", "wr_vid"):
        if must not in jar or not jar[must]:
            print(f"[warn] 缺少 {must}，可能未真正登录")

    Path(COOKIE_PATH).write_text(header, encoding="utf-8")
    print(f"[saved] {COOKIE_PATH}（{len(header)} 字符，仅本机）")

    if not args.test:
        return 0

    import json
    import urllib.request

    url = f"{BASE}/api/mp/cover?bookId={args.test}"
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Referer": BASE + "/",
        "Cookie": header,
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode("utf-8", errors="replace")
        print(f"[test] HTTP {r.status}")
        data = json.loads(body)
        print("[test] name  =", data.get("name"))
        print("[test] title =", data.get("title"))
        rid = data.get("reviewId", "")
        token = rid.split("_", 3)[-1] if rid else ""
        print("[test] reviewId =", rid)
        print("[test] 原文直链 =", f"https://mp.weixin.qq.com/s/{token}" if token else "<无>")
    except Exception as e:  # noqa: BLE001
        print(f"[test] 失败 {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
