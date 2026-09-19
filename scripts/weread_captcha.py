#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/weread_captcha.py — weread 图像点选验证码「半自动过码」工具（PLAN-20260919 B）。

架构（FORCE_AGENT_MODE）：脚本出产物（截图/点击），识别由会话内执行模型完成。
验证码是图像点选类（用户确认），文字 OCR 无解——不做 tesseract，不做全自动乱点
（无人监督乱点风险，见 PLAN §3.3 边界）。

流程（用户或会话内模型在场时）：
  1. python scripts/weread_captcha.py --shot
     → 截图 _tmp/weread_probe/captcha-<ts>.png，打印【图像像素尺寸】/【CSS 视口尺寸】/缩放比
  2. 模型 Read 截图 → 识别点选目标与顺序 → 输出坐标序列（相对截图的像素坐标）
  3. python scripts/weread_captcha.py --click "x1,y1" "x2,y2" ...
     → 像素坐标 ÷ 缩放比 = CSS 坐标 → page.mouse.click 依次点击（间隔 0.6~1.2s 抖动）
     → 点击后再存一张 captcha-after-<ts>.png 供核验
  4. 核验：过码成功 → 重跑一轮 weread 源；仍在 → 换新码重来（回步骤 1），≤2 次，
     仍失败保持停手转人工。

子命令（可组合，按序执行）：
  --detect            只检测页面是否出现安全检测/验证码标记（不截图、不点击）
  --shot              截当前 weread 页，打印尺寸与缩放比
  --click "x,y" ...   按截图像素坐标依次点击（自动换算），点击后自动再截图核验
  --text              打印页面 body 文本前 600 字（辅助判定过码结果）

纪律：每个子命令内部一次 SharedCdpSession 会话（ healthy 复用活调试 Chrome，close 只断开
不杀）；两次点击之间随机抖动模拟人手；不做重试死循环——重试决策归会话内模型。
"""
from __future__ import annotations

import argparse
import random
import struct
import sys
import time
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared.cdp_session import SharedCdpSession  # noqa: E402
from monitors.weread import (  # noqa: E402
    CAPTCHA_DIR, _find_or_open_weread_page, detect_captcha, screenshot_captcha,
)

_TEXT_JS = "() => document.body ? document.body.innerText : ''"


def _png_size(path: str) -> tuple:
    """读 PNG IHDR 拿像素尺寸（免 PIL 依赖）。"""
    with open(path, "rb") as f:
        head = f.read(33)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise ValueError(f"不是合法 PNG：{path}")
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def _shot(page) -> str:
    path = screenshot_captcha(page)
    if not path:
        print("❌ 截图失败", file=sys.stderr)
        raise SystemExit(1)
    img_w, img_h = _png_size(path)
    vp = page.viewport_size or {}
    vp_w = vp.get("width") or img_w  # 无视口信息时假设缩放比 1
    scale = img_w / vp_w if vp_w else 1.0
    print(f"[shot] {path}")
    print(f"[shot] 图像像素 {img_w}x{img_h} · CSS 视口 {vp_w}x{vp.get('height', '?')} · "
          f"缩放比 {scale:.4f}")
    print(f"[shot] --click 坐标请用【截图像素坐标】，脚本会自动 ÷ {scale:.4f} 换算")
    return path


def _click(page, coord_args: list) -> None:
    """按截图像素坐标依次点击（换算 CSS 坐标），最后自动截图核验。"""
    if not coord_args:
        print("❌ --click 需要至少一个 \"x,y\" 坐标", file=sys.stderr)
        raise SystemExit(2)
    # 以最近一张 captcha 截图的缩放比换算（先截一张拿当前视口关系，保证坐标基准一致）
    base = screenshot_captcha(page)
    img_w, _ = _png_size(base)
    vp_w = (page.viewport_size or {}).get("width") or img_w
    scale = img_w / vp_w if vp_w else 1.0
    for c in coord_args:
        try:
            px, py = (float(v) for v in c.replace("，", ",").split(","))
        except ValueError:
            print(f"❌ 坐标格式非法（应为 \"x,y\"）：{c}", file=sys.stderr)
            raise SystemExit(2)
        cx, cy = px / scale, py / scale
        print(f"[click] 像素({px:.0f},{py:.0f}) → CSS({cx:.1f},{cy:.1f})")
        page.mouse.move(cx, cy)
        time.sleep(random.uniform(0.15, 0.4))
        page.mouse.click(cx, cy)
        time.sleep(random.uniform(0.6, 1.2))  # 人手节奏抖动，防机械节拍
    after = screenshot_captcha(page)
    print(f"[click] 已完成 {len(coord_args)} 次点击，核验截图：{after}")


def main() -> int:
    ap = argparse.ArgumentParser(description="weread 图像点选验证码半自动过码")
    ap.add_argument("--detect", action="store_true", help="只检测页面是否出现验证码标记")
    ap.add_argument("--shot", action="store_true", help="截图当前 weread 页并打印缩放比")
    ap.add_argument("--click", nargs="+", default=[], metavar="X,Y",
                    help="按截图像素坐标依次点击（自动换算 CSS 坐标）")
    ap.add_argument("--text", action="store_true", help="打印页面 body 文本前 600 字")
    args = ap.parse_args()
    if not (args.detect or args.shot or args.click or args.text):
        ap.print_help()
        return 2

    Path(CAPTCHA_DIR).mkdir(parents=True, exist_ok=True)
    with SharedCdpSession() as s:
        page = _find_or_open_weread_page(s)
        if args.detect:
            hit = detect_captcha(page)
            print(f"[detect] {'⚠️ 页面出现安全检测/验证码标记' if hit else '✅ 未检测到验证码标记'}")
            return 0 if not hit else 3
        if args.shot:
            _shot(page)
        if args.click:
            _click(page, args.click)
            if detect_captcha(page):
                print("[after] ⚠️ 点击后页面仍有验证码标记——查看核验截图，"
                      "换新码重来（≤2 次）或转人工")
                return 3
            print("[after] ✅ 点击后未检测到验证码标记；可重跑一轮 weread 源"
                  "（python monitors/run.py --mode auto --apply）")
        if args.text:
            try:
                t = (page.evaluate(_TEXT_JS) or "")[:600]
            except Exception as e:  # noqa: BLE001
                t = f"<evaluate 失败 {type(e).__name__}: {e}>"
            print(f"[text] {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
