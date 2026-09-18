#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/weread_hook_sr.py — hook window.__WRPA__.sr，记录前端真实调用参数。

背景：黑盒试探 sr(url)/sr(url,method)/… 均能出签名，但拿去 fetch 一律 -2041——
说明真实调用的参数形式与我们的猜测不同。本脚本在 mp/reader 页面 reload 前注入
init-script 包装 sr（记录每次调用的参数与返回），让前端自己暴露调用约定。

只动本页 JS，不产生额外网络请求（reload 本身是页面正常加载，被被动探针记录）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared.cdp_session import SharedCdpSession  # noqa: E402

HOOK_JS = r"""
(() => {
  let tries = 0;
  const timer = setInterval(() => {
    const w = window.__WRPA__;
    if (w && typeof w.sr === 'function' && !w.__hooked) {
      const orig = w.sr;
      window.__srlog = [];
      const wrapped = function (...args) {
        const rec = {
          t: Date.now(),
          args: args.map(a => (typeof a === 'string' ? a.slice(0, 300) : JSON.stringify(a))),
        };
        window.__srlog.push(rec);
        try {
          const r = orig.apply(this, args);
          if (r && typeof r.then === 'function') {
            return r.then(v => {
              try { rec.ret = JSON.stringify(v).slice(0, 400); } catch (e) { rec.ret = String(v).slice(0, 100); }
              return v;
            });
          }
          try { rec.ret = JSON.stringify(r).slice(0, 400); } catch (e) { rec.ret = String(r).slice(0, 100); }
          return r;
        } catch (e) {
          rec.err = String(e).slice(0, 150);
          throw e;
        }
      };
      try {
        Object.defineProperty(w, 'sr', { value: wrapped, writable: true, configurable: true });
      } catch (e) {
        try { w.sr = wrapped; } catch (e2) { return; }
      }
      w.__hooked = true;
      clearInterval(timer);
    }
    if (++tries > 400) clearInterval(timer);
  }, 25);
})();
"""


def main() -> int:
    with SharedCdpSession() as s:
        ctx = s.context
        target = None
        for p in list(getattr(ctx, "pages", []) or []):
            if "/web/mp/reader/" in (p.url or ""):
                target = p
                break
        if target is None:
            print("[fail] 没找到 /web/mp/reader/ 页面（先跑探针 --nav 打开它）")
            return 1
        print(f"[page] {target.url[:110]}")
        target.add_init_script(HOOK_JS)
        target.reload(wait_until="domcontentloaded", timeout=30_000)
        print("[hook] 已注入并 reload，等待前端调用 sr…")
        got = None
        for _ in range(40):  # 最多 20s
            time.sleep(0.5)
            try:
                log = target.evaluate("() => window.__srlog || null")
            except Exception:  # noqa: BLE001
                continue
            if log:
                got = log
                if len(log) >= 2:
                    break
        if not got:
            print("[fail] 20s 内没观察到 sr 调用（页面可能缓存了列表，没发新请求）")
            return 1
        print(json.dumps(got, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
