#!/usr/bin/env python3
"""手动检查/刷新 B站 cookie（fetch_up_range 惰性轮换的人工触发版）。

cookie 获取【有且只有一个方法】（用户 2026-09-03 拍板）：CDP profile 克隆 →
真实访问 bilibili.com → 从活会话读 cookie → 校验 → 写回 .env/.cache。
- 未登录【不报错退出】：保持会话打开，每 5s 轮询 SESSDATA，等你在弹出的
  浏览器窗口里登录（扫码/账密），默认最长 5 分钟（BILI_COOKIE_WAIT_S 可调）。
- Playwright 挂真实 profile 的老路已死（Chrome 151+ 默认目录禁远程调试），
  已从 videos/fetch.py 删除，不再保留。

两种模式：
    python scripts/bili_cookie_refresh.py          # 惰性：校验当前 cookie，失效才走 CDP 轮换
    python scripts/bili_cookie_refresh.py --force  # 无条件走 CDP 重新提取（跳过校验当前）

成功后自动写回 .env 的 BILI_COOKIE= 行（原 .env 备份为 .env.bili_cookie.bak），
管线下一轮直接可用。抓取管线也会在运行开始/命中412后自动校验并轮换
（事件记入 <作者>_backfill_*.json 运行日志），无需定时轮询。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> int:
    from videos import fetch as bfetch

    if "--force" in sys.argv:
        fresh = bfetch._bili_extract_cookies_cdp()
        if not fresh:
            print("❌ CDP 提取失败（登录等待超时或会话异常），详见上方输出。")
            return 1
        if not bfetch.validate_bilibili_cookies(fresh):
            print("❌ 提取到的 cookie nav 校验未通过（异常），请重试。")
            return 1
        bfetch._persist_cookie_to_env(fresh)
        print("✅ 已强制重新提取，校验通过并写回 .env。")
        return 0

    state, fresh = bfetch.rotate_bili_cookie_if_dead()
    if state == "valid":
        print("✅ 当前 cookie 有效（nav isLogin=True），无需轮换。")
        return 0
    if state == "rotated" and fresh:
        os.environ["BILI_COOKIE"] = fresh
        print("✅ 已轮换并写回 .env/.cache，管线即刻可用。")
        return 0
    print("❌ 轮换失败（登录等待超时或 CDP 会话异常），详见上方输出。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
