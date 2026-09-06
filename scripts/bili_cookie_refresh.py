#!/usr/bin/env python3
"""手动检查/刷新 B站 cookie（fetch_up_range 惰性轮换的人工触发版）。

cookie 获取【有且只有一个方法】（用户 2026-09-03 拍板）：CDP profile 克隆 →
真实访问 bilibili.com → 从活会话读 cookie → 校验 → 写回 .env/.cache。

两种模式：
    python scripts/bili_cookie_refresh.py          # 间隔感知：cookie 失效 或 距上次换 ≥7天(默认)
                                                   # 才走 CDP 轮换；否则报 valid。
    python scripts/bili_cookie_refresh.py --force  # 无条件走 CDP 重新提取（跳过校验当前）

每次轮换（成功/失败/valid检查）都会写入 scripts/bili_cookie_rotation.json 的 history，
记录 ts / trigger / result / method，满足「每一次换都要记录，被动每7天」。
被动间隔由环境变量 BILI_COOKIE_INTERVAL_DAYS 覆盖（默认7）。

成功后自动写回 .env 的 BILI_COOKIE= 行（原 .env 备份为 .env.bili_cookie.bak），
管线下一轮直接可用。抓取管线也会在运行开始(interval) / 命中412后(risk412)自动校验并轮换。
"""
import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _print_state() -> None:
    from videos import fetch as bfetch
    try:
        st = bfetch._bili_load_rotation_state()
    except Exception:
        return
    print("\n── 轮换状态 (scripts/bili_cookie_rotation.json) ──")
    print(f"  被动间隔: 每 {st.get('interval_days', 7)} 天")
    print(f"  最近成功换: {st.get('last_rotation_ts') or '（无）'}")
    print(f"  最近被动尝试: {st.get('last_interval_attempt_ts') or '（无）'}")
    hist = st.get("history", [])
    if hist:
        print(f"  最近 {min(5, len(hist))} 条记录:")
        for h in hist[-5:]:
            print(f"    - {h.get('ts')} | trigger={h.get('trigger')} | "
                  f"result={h.get('result')} | method={h.get('method')}"
                  f"{(' | ' + h['note']) if h.get('note') else ''}")
    else:
        print("  记录: 暂无")


def main() -> int:
    from videos import fetch as bfetch

    if "--force" in sys.argv:
        state, fresh = bfetch.rotate_bili_cookie_if_dead(trigger="manual", force=True)
    else:
        state, fresh = bfetch.rotate_bili_cookie_if_dead(trigger="interval")

    _print_state()

    if state == "valid":
        print("✅ 当前 cookie 有效（未到轮换周期/未失效），无需换。")
        return 0
    if state == "rotated" and fresh:
        os.environ["BILI_COOKIE"] = fresh
        print("✅ 已轮换并写回 .env/.cache，管线即刻可用。")
        return 0
    print("❌ 轮换失败（本机 Chrome 未登录 B站 / 登录等待超时 / CDP 异常），详见上方输出。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
