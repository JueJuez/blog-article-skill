#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/weread_sig_probe.py — 验证 weread 的 x-wrpa-0 签名是否可复用 + 找签名生成器。

背景（2026-09-18 抓包实锤）：
  `/web/mp/articles` 等接口要求请求头 `x-wrpa-0`（由页面加载的
  `cdn.weread.qq.com/web/wrpa/wasm/wpa-1.0.1.min.wasm` 生成）。缺它 → `{"errCode":-2041}`。
  前端在 `/web/mp/reader/<hex>` 页面里带签名调用，返回 200 + reviews 列表。

本脚本做三件事（在**已打开的 mp/reader 页面上下文内**执行，同源带 cookie）：
  1. 枚举 window 全局，找 wrpa/wpa/sign 相关的签名生成器入口；
  2. 无签名重放 articles（确认仍 -2041，排除 Referer 等其他因素）；
  3. 带抓包里拿到的旧签名重放 articles（offset=0 与 offset=50 翻页），
     → 200 = 签名可短期复用；-2041 = 签名一次性/时效，必须页内实时生成。

凭据安全：签名值只在本机 JSONL/日志里流转，不打印 cookie。
"""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared.cdp_session import SharedCdpSession  # noqa: E402

PROBE_DIR = os.path.join(ROOT, "_tmp", "weread_probe")

JS_GLOBALS = """() => {
  const keys = Object.getOwnPropertyNames(window)
    .filter(k => /wrpa|wpa|wr_sign|sign|ticket|getskey|skey/i.test(k));
  return {
    keys,
    nuxt: typeof window.$nuxt !== 'undefined',
    webpack: typeof window.webpackChunk_Nuxt !== 'undefined' || typeof window.__NUXT__ !== 'undefined',
  };
}"""

JS_FETCH = """async (args) => {
  const [url, sig] = args;
  const headers = { Accept: 'application/json, text/plain, */*' };
  if (sig) headers['x-wrpa-0'] = sig;
  try {
    const r = await fetch(url, { credentials: 'include', headers });
    const t = await r.text();
    return { status: r.status, len: t.length, body: t.slice(0, 300) };
  } catch (e) {
    return { status: 0, err: String(e) };
  }
}"""


def _last_sig() -> tuple[str, str]:
    """从最新抓包 JSONL 里取最近一条 articles 请求的签名与完整 URL。"""
    files = sorted(glob.glob(os.path.join(PROBE_DIR, "net-*.jsonl")), key=os.path.getmtime)
    for f in reversed(files):
        for line in reversed(open(f, encoding="utf-8").readlines()):
            try:
                e = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if "/web/mp/articles" in e.get("url", ""):
                h = e.get("req_headers") or {}
                sig = h.get("x-wrpa-0") or h.get("X-wrpa-0") or ""
                if sig:
                    return sig, e["url"]
    return "", ""


def main() -> int:
    sig, url = _last_sig()
    print(f"[sig] {'取到 (' + str(len(sig)) + ' chars)' if sig else '未找到'} from {url[:90]}")

    with SharedCdpSession() as s:
        ctx = s.context
        target = None
        for p in list(getattr(ctx, "pages", []) or []):
            if "/web/mp/reader/" in (p.url or ""):
                target = p
                break
        if target is None:
            print("[fail] 没找到 /web/mp/reader/ 页面。现有：")
            for p in getattr(ctx, "pages", []) or []:
                print("   ", (p.url or "")[:110])
            return 1
        print(f"[page] {target.url[:110]}")

        print("[globals]", json.dumps(target.evaluate(JS_GLOBALS), ensure_ascii=False))

        base = "https://weread.qq.com/web/mp/articles?bookId=MP_WXS_2399233620&offset=0"
        for label, u, s_ in (
            ("无签名 offset=0", base, ""),
            ("旧签名 offset=0", base, sig),
            ("旧签名 offset=50", base.replace("offset=0", "offset=50"), sig),
        ):
            r = target.evaluate(JS_FETCH, [u, s_])
            print(f"[{label}] {json.dumps(r, ensure_ascii=False)[:340]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
