#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/weread_captcha.py — weread 图像点选验证码「半自动过码」工具（PLAN-20260919 B）。

架构（FORCE_AGENT_MODE）：脚本出产物（截图/点击），识别由会话内执行模型完成。
验证码是图像点选类（用户确认），文字 OCR 无解——不做 tesseract，不做全自动乱点
（无人监督乱点风险，见 PLAN §3.3 边界）。

流程（用户或会话内模型在场时）：
  1. python scripts/weread_captcha.py --shot
     → 截图 _tmp/weread_probe/captcha-<ts>.png（码渲染在跨源 iframe，整页截图才可见）
  2. 模型 Read 截图 → 读出「目标描述词」+ 目标物在 3×2 拼图中的格子位置
     （row-major 1 起：左上=1,1；右上=1,3；左下=2,1；中下=2,2；右下=2,3）
  3. python scripts/weread_captcha.py --grid "2,2" --confirm
     → 在验证码 iframe 内按元素局部坐标点选（Playwright 自动处理跨源与 CSS 缩放）
     → 点「确定」提交 → 自动截图核验
  4. 核验：过码成功 → 重跑一轮 weread 源；仍显示码 → --refresh 换新码回步骤 1，
     ≤2 次，仍失败保持停手转人工。

  ⚠️ 旧 --click（裸视口坐标）保留兜底：点选码 iframe 被 CSS 缩放，裸坐标会错位
  （2026-09-19 实测选错格），仅用于非 iframe 场景。

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


def _css_viewport_width(page) -> float:
    """真实 CSS 视口宽。⚠️ connect_over_cdp 接管的页面 page.viewport_size 返回 None，
    必须问 window.innerWidth（2026-09-19 实测：否则 scale 恒 1，坐标不换算全点歪）。"""
    try:
        w = page.evaluate("() => window.innerWidth")
        if w and w > 0:
            return float(w)
    except Exception:
        pass
    vp = page.viewport_size or {}
    return float(vp.get("width") or 0)


def _shot(page) -> str:
    path = screenshot_captcha(page)
    if not path:
        print("❌ 截图失败", file=sys.stderr)
        raise SystemExit(1)
    img_w, img_h = _png_size(path)
    vp_w = _css_viewport_width(page)
    scale = img_w / vp_w if vp_w else 1.0
    print(f"[shot] {path}")
    print(f"[shot] 图像像素 {img_w}x{img_h} · CSS 视口 {vp_w:.0f}x? · "
          f"缩放比 {scale:.4f}")
    print(f"[shot] --click 坐标请用【截图像素坐标】，脚本会自动 ÷ {scale:.4f} 换算")
    return path


def _click(page, coord_args: list) -> None:
    """按截图像素坐标依次点击（换算 CSS 坐标），最后自动截图核验。"""
    if not coord_args:
        print("❌ --click 需要至少一个 \"x,y\" 坐标", file=sys.stderr)
        raise SystemExit(2)
    # 以最新视口关系计算缩放比（截图与 innerWidth 同刻采集，保证坐标基准一致）
    base = screenshot_captcha(page)
    img_w, _ = _png_size(base)
    vp_w = _css_viewport_width(page)
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


def _captcha_frame(page):
    """定位验证码 iframe（腾讯 captcha.gtimg.com 组件，跨源，按内容识别）。

    ⚠️ 2026-09-19 实测：点选码整个渲染在跨源 iframe 里，六张图是一张合成大图
    （.tc-bg-img），iframe 被父页 CSS 缩放——裸 page.mouse.click 的视口坐标会
    错位选错格，必须用 Playwright 在 frame 内按元素局部坐标点击（自动映射）。
    """
    for f in getattr(page, "frames", None) or []:
        try:
            t = f.evaluate("() => document.body ? document.body.innerText : ''") or ""
        except Exception:
            continue
        if "最符合描述" in t or "换一组" in t:
            return f
    return None


# 码只在 mp reader 页由前端渲染（-2041 后弹码；shelf 页无任何提示）。
# 过码时若当前页无码：导航到任一公众号 reader 页让它现身（前端会自己发 1 次
# articles 请求触发弹码）。默认哥飞（WR_READER_URL 可覆盖为任意 reader 页 URL）。
DEFAULT_READER_URL = ("https://weread.qq.com/web/mp/reader/"
                      "d5442a0224d505f5758535f323339393233333632301a9")


def _ensure_captcha_visible(page) -> bool:
    if _captcha_frame(page) is not None:
        return True
    url = os.environ.get("WR_READER_URL", DEFAULT_READER_URL)
    print(f"[nav] 当前页无验证码组件，导航 reader 页让它现身（前端 1 次请求）：{url[:70]}…")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(8000)
    except Exception as e:  # noqa: BLE001
        print(f"[nav] 导航失败: {e}", file=sys.stderr)
        return False
    return _captcha_frame(page) is not None


def _click_visible_text(frame, text: str) -> bool:
    """在 frame 里点第一个可见的指定文本元素（如 确定 / 换一组）。"""
    loc = frame.get_by_text(text, exact=True)
    try:
        n = loc.count()
    except Exception:
        n = 0
    for i in range(n):
        item = loc.nth(i)
        try:
            if item.is_visible():
                item.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


def _solve_grid(page, cells: list, confirm: bool) -> None:
    """按 3×2 拼图的格子序号点选（row-major，1 起：左上=1,1 → 右下=2,3）。

    在 .tc-bg-img 元素局部坐标系内定位格子中心，Playwright 负责跨 iframe 与
    CSS 缩放的落点映射；确认提交用可见「确定」文本元素。
    """
    frame = _captcha_frame(page)
    if frame is None:
        print("❌ 未找到验证码 iframe（码可能已消失或形态变化，请 --shot 取证）",
              file=sys.stderr)
        raise SystemExit(4)
    dims = frame.evaluate(
        "() => { const e = document.querySelector('.tc-bg-img');"
        " return e ? [e.offsetWidth, e.offsetHeight] : null; }")
    if not dims or not dims[0]:
        dims = [340, 243]  # drag_ele 模板实测默认尺寸（缩放前的元素局部尺寸）
    w, h = float(dims[0]), float(dims[1])
    bg = frame.locator(".tc-bg-img").first
    for cell in cells:
        try:
            r, c = (int(v) for v in cell.replace("，", ",").split(","))
        except ValueError:
            print(f"❌ 格子格式非法（应为 \"行,列\" 1~2/1~3）：{cell}", file=sys.stderr)
            raise SystemExit(2)
        px = w * (c - 0.5) / 3.0
        py = h * (r - 0.5) / 2.0
        print(f"[grid] 点选 第{r}行第{c}列（元素局部 {px:.0f},{py:.0f}）")
        bg.click(position={"x": px, "y": py}, timeout=8000)
        time.sleep(random.uniform(0.6, 1.2))  # 人手节奏
    if confirm:
        if _click_visible_text(frame, "确定"):
            print("[grid] 已点「确定」提交")
        else:
            frame.locator(".tc-embed-verify-btn").first.click(force=True, timeout=5000)
            print("[grid] 已点 .tc-embed-verify-btn 提交（文本定位未见可见元素）")


def main() -> int:
    ap = argparse.ArgumentParser(description="weread 图像点选验证码半自动过码")
    ap.add_argument("--detect", action="store_true", help="只检测页面是否出现验证码标记")
    ap.add_argument("--shot", action="store_true", help="截图当前 weread 页并打印缩放比")
    ap.add_argument("--refresh", action="store_true",
                    help="点「换一组」换新码（识别错误或格子被误选后用）")
    ap.add_argument("--grid", nargs="+", default=[], metavar="行,列",
                    help="按 3×2 拼图格子序号点选（row-major 1 起，如 \"2,2\"=中下格），"
                         "配合 --confirm 提交；推荐替代 --click（iframe 缩放下裸坐标会错位）")
    ap.add_argument("--confirm", action="store_true", help="--grid 点选后点「确定」提交")
    ap.add_argument("--click", nargs="+", default=[], metavar="X,Y",
                    help="（旧路径）按截图像素坐标裸点视口坐标；验证码在缩放 iframe 内时不可靠")
    ap.add_argument("--text", action="store_true", help="打印页面 body 文本前 600 字")
    args = ap.parse_args()
    if not (args.detect or args.shot or args.grid or args.refresh
            or args.click or args.text):
        ap.print_help()
        return 2

    Path(CAPTCHA_DIR).mkdir(parents=True, exist_ok=True)
    with SharedCdpSession() as s:
        page = _find_or_open_weread_page(s)
        if args.detect:
            hit = detect_captcha(page)
            print(f"[detect] {'⚠️ 页面出现安全检测/验证码标记' if hit else '✅ 未检测到验证码标记'}")
            return 0 if not hit else 3
        # --shot/--refresh/--grid 都是过码流程：当前页无码时先导航 reader 页让它现身
        if args.shot or args.refresh or args.grid:
            if not _ensure_captcha_visible(page):
                print("ℹ️ reader 页也未出现验证码（风险态可能已解除或已过码），"
                      "按截图核验即可", file=sys.stderr)
        if args.shot:
            _shot(page)
        if args.refresh:
            frame = _captcha_frame(page)
            if frame is None or not _click_visible_text(frame, "换一组"):
                print("❌ 未点到「换一组」（码可能已消失）", file=sys.stderr)
                raise SystemExit(4)
            time.sleep(2.5)  # 等新图加载
            print(f"[refresh] 已换新码，核验截图：{screenshot_captcha(page)}")
        if args.grid:
            _solve_grid(page, args.grid, confirm=args.confirm)
            time.sleep(2.0)  # 等提交/关闭动画
            after = screenshot_captcha(page)
            if detect_captcha(page):
                print(f"[after] ⚠️ 提交后仍有验证码标记——查看核验截图 {after}，"
                      "点错的格子用 --refresh 换新码重来（≤2 次）或转人工")
                return 3
            print(f"[after] ✅ 过码成功（核验截图 {after}）；可重跑一轮 weread 源"
                  "（python monitors/run.py --mode auto --apply）")
        elif args.click:
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
