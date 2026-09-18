#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/weread_state.py — weread 页面登录态体检（只读，不产生业务请求）。

为什么需要它：抓包探针在后台跑，光看网络日志判断不出「页面现在是游客还是已登录」
（尤其无法看截图时）。本脚本连到同一个 CDP 端点，采样：
  1. 各标签页 url/title
  2. 主页面正文前若干字（找「登录 / 扫码 / 书架 / 用户名」等线索）
  3. 页面上是否存在二维码元素
  4. weread 域 cookie 的**名字 + 长度**（不打印值，避免凭据落盘/进上下文）

用法：
    python scripts/weread_state.py [--text 800]

⚠️ 与探针共用同一 Chrome：CdpSession 的 D6 持有者机制保证「最后一个持有者才关灯」，
   探针进程还活着时本脚本退出不会杀 Chrome。
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


def _cookie_names(ctx, domain_hint: str = "weread") -> list:
    try:
        cookies = ctx.cookies()
    except Exception as e:  # noqa: BLE001
        return [f"<cookie read failed: {type(e).__name__}>"]
    out = []
    for c in cookies:
        dom = str(c.get("domain", ""))
        if domain_hint not in dom:
            continue
        val = str(c.get("value", ""))
        out.append(f"{c.get('name')}({len(val)})")
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", type=int, default=700, help="正文采样字数")
    args = ap.parse_args()

    with SharedCdpSession() as s:
        ctx = s.context
        print(f"[endpoint] {s.cdp_endpoint}")
        pages = list(getattr(ctx, "pages", []) or [])
        print(f"[pages] {len(pages)}")
        for i, p in enumerate(pages):
            try:
                print(f"  #{i} url={p.url[:120]!r} title={p.title()[:60]!r}")
            except Exception as e:  # noqa: BLE001
                print(f"  #{i} <读取失败 {type(e).__name__}>")

        page = s.page
        # 后台标签会被浏览器节流（innerText 可能返回空串），先激活再采样。
        try:
            page.bring_to_front()
            page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            pass
        try:
            txt = page.evaluate(f"() => (document.body.innerText || '').slice(0, {args.text})")
        except Exception as e:  # noqa: BLE001
            txt = f"<正文读取失败 {type(e).__name__}: {e}>"
        print("[body]")
        print(txt)

        try:
            qr = page.evaluate(
                "() => Array.from(document.querySelectorAll('img,canvas,iframe'))"
                ".map(e => (e.src || e.className || ''))"
                ".filter(s => /qr|login|scan/i.test(String(s))).slice(0, 10)"
            )
            print(f"[qr/login 元素] {qr}")
        except Exception as e:  # noqa: BLE001
            print(f"[qr/login 元素] <{type(e).__name__}>")

        print("[cookies] " + ", ".join(_cookie_names(ctx)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
