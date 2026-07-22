"""monitors/run.py — 订阅监控命令行入口。

默认模式（发现）：检查所有订阅源，输出「新内容」JSON 列表，供上层 AI 调用总结管线。
  python monitors/run.py

应用模式（直接总结）：发现新内容后直接调 articles/videos 管线落盘。
  python monitors/run.py --apply

订阅配置：monitors/subscriptions.json（参考 subscriptions.example.json）
  {
    "wechat":   [{"mp_id": "MP_XXX", "name": "中金研究"}],
    "bilibili": [{"uid": "22675713"}]
  }

认证：B站用根目录 .env 的 BILI_COOKIE（登录态 Cookie，动态接口硬性要求，缺失则降级游客态并告警）；
      公众号用 monitors/.wechat_auth.json 里的 JWT（weread.111965.xyz 转发服务器签发，数小时失效，
      run.py 自动检测失效并弹码续期，本次运行跳过微信源保 B站照跑）。
"""
import sys
import os
import json
import time
import random
import argparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

def _load_env():
    """依赖无关地解析项目根 .env 到 os.environ（仅 setdefault，不覆盖已有值）。

    确保 BILI_COOKIE 等变量在导入 monitors 前就位（bilibili 在模块加载时读取 cookie）。
    """
    env_path = os.path.join(BASE_DIR, ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass


_load_env()

from monitors.state import load_state, save_state  # noqa: E402
from monitors.wechat import (  # noqa: E402
    WereadClient, WechatSource, trigger_relogin, _notify_user,
)
from monitors.bilibili import BilibiliSource, _SOURCE_GAP  # noqa: E402
from monitors.ad_filter import is_fully_ad, purify_content  # noqa: E402

SUB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subscriptions.json")
AUTH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".wechat_auth.json")
# 每类型「安全上限」（防极端 UP 单窗口发几百条刷爆笔记）；真正的内容量由
# 时间窗口（BILI_FIRST_WINDOW_DAYS / BILI_DAILY_WINDOW_DAYS）约束，不是这个 count。
FIRST_RUN_LIMIT = int(os.environ.get("FIRST_RUN_LIMIT", "50"))
# 短动态轻量化阈值：正文去广告净化后长度 <= 此值，走「短动态速览」（直接存原文，不调重 LLM 总结模板）
SHORT_DYNAMIC_MAX = int(os.environ.get("BILI_SHORT_DYNAMIC_MAX", "80"))


def load_subscriptions() -> dict:
    if os.path.exists(SUB_PATH):
        with open(SUB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"wechat": [], "bilibili": []}


def load_weread_auth() -> tuple:
    """优先取环境变量 WEREAD_TOKEN/WEREAD_VID，否则回落到扫码保存的 .wechat_auth.json。"""
    token = os.environ.get("WEREAD_TOKEN", "")
    vid = os.environ.get("WEREAD_VID", "")
    if not token and os.path.exists(AUTH_PATH):
        try:
            with open(AUTH_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            token = d.get("token", "")
            vid = d.get("vid", "")
        except Exception:
            pass
    return token, vid


def discover_all(subs: dict, state: dict, mode: str = "auto") -> list:
    all_new: list = []
    token, vid = load_weread_auth()

    # ---------- 微信源（token 自愈） ----------
    wechat_subs = subs.get("wechat", [])
    need_qr = False
    qr_path = None
    if wechat_subs:
        first_share = wechat_subs[0].get("share_url", "")
        valid = bool(token)
        if valid:
            try:
                valid = WereadClient(token=token, vid=vid).is_token_valid(
                    probe_share_url=first_share)
            except Exception:
                valid = False
        if not valid:
            print("⚠️ [微信读书] token 失效或缺失，自动触发重新登录...", file=sys.stderr)
            qr_path = trigger_relogin()
            need_qr = bool(qr_path)
            if need_qr:
                print(f"RELOGIN_QR:{qr_path}", file=sys.stderr)
                print(f"⚠️ 请用微信「扫一扫」扫描二维码重新登录：{qr_path}", file=sys.stderr)
                print("⚠️ 本次运行跳过公众号源（B站照常）；扫码后 token 自动落盘，下次运行恢复。",
                      file=sys.stderr)
                _notify_user(qr_path)  # 弹图片查看器 + 提示框
            else:
                print("⚠️ 重新登录触发失败，请手动运行 `python monitors/_auth.py qr`", file=sys.stderr)
        else:
            for w in wechat_subs:
                client = WereadClient(token=token, vid=vid)
                src = WechatSource(client, mp_id=w.get("mp_id", ""),
                                   share_url=w.get("share_url", ""), name=w.get("name", ""))
                try:
                    all_new.extend(src.discover(state, first_run_limit=FIRST_RUN_LIMIT, mode=mode))
                except Exception as e:
                    if "401" in str(e) and not need_qr:
                        qr_path = trigger_relogin()
                        need_qr = bool(qr_path)
                        if qr_path:
                            print(f"RELOGIN_QR:{qr_path}", file=sys.stderr)
                            print(f"⚠️ 抓取时 token 失效，请扫码：{qr_path}", file=sys.stderr)
                            _notify_user(qr_path)  # 弹图片查看器 + 提示框
                        continue
                    print(f"[warn] wechat {w} 失败: {e}", file=sys.stderr)
                time.sleep(2)  # weread 代理频率退避，避免单号日内超次

    for b in subs.get("bilibili", []):
        src = BilibiliSource(b["uid"], types=b.get("types"))
        try:
            all_new.extend(src.discover(state, first_run_limit=FIRST_RUN_LIMIT, mode=mode))
        except Exception as e:
            print(f"[warn] bilibili {b} 失败: {e}", file=sys.stderr)
        # B站频率退避，规避 -352/-412 风控。加 ±5s 抖动，避免固定周期被识别为脚本。
        time.sleep(max(5, _SOURCE_GAP + random.uniform(-5, 5)))

    return all_new


def apply_summaries(items: list) -> None:
    from articles.fetch import fetch_web_content
    # 健康度统计：每轮运行结束打印一行，便于一眼看出监控是否健康
    stats = {
        "video": 0, "video_charging_skip": 0,
        "dynamic_full": 0, "dynamic_light": 0,
        "article": 0, "ad_skip": 0, "short_skip": 0, "error": 0,
    }
    for it in items:
        if it["route"] in ("article", "cv"):
            # 自抓正文一次：整篇纯广告则 skip；否则净化夹带广告后喂总结
            try:
                fetched = fetch_web_content(it["url"])
                title = fetched[0] if isinstance(fetched, tuple) else it.get("title", "")
                content = fetched[1] if isinstance(fetched, tuple) else ""
            except Exception as e:
                print(f"[fetch-err] {it['title']}: {e}", file=sys.stderr)
                stats["error"] += 1
                continue
            if is_fully_ad(title or it.get("title", ""), content or ""):
                print(f"[ad-skip] {it['title']}（整篇纯广告，跳过）")
                stats["ad_skip"] += 1
                continue
            purified = purify_content(content or "")
            src_text = f"{purified}\n\n---\n原文链接：{it['url']}"
            try:
                from articles.main import skill_main
                res = skill_main({"content": src_text, "author": it.get("mp_name", ""),
                                  "publish_time": it.get("publish_time", 0)})
                msg = res.get("message", "") if isinstance(res, dict) else str(res)
                print(f"[{it['source']}] {it['title']}: {msg}")
                stats["article"] += 1
            except Exception as e:
                print(f"[error] {it['title']}: {e}", file=sys.stderr)
                stats["error"] += 1
        elif it["route"] == "video":
            if it.get("is_charging"):
                badge = it.get("charging_badge") or "充电专属"
                print(f"[bili-charging-skip] {it['title']}（{badge}，付费内容未抓取正文）")
                stats["video_charging_skip"] += 1
                continue
            try:
                from videos import summarize_video
                res = summarize_video({"url": it["url"], "publish_time": it.get("publish_time", 0)})
                msg = res.get("message", "") if isinstance(res, dict) else str(res)
                print(f"[bilibili] {it['title']}: {msg}")
                stats["video"] += 1
            except Exception as e:
                print(f"[error] {it['title']}: {e}", file=sys.stderr)
                stats["error"] += 1
        elif it["route"] == "dynamic":
            # 动态正文直接来自 API（it["content"]），净化后喂总结，不重复抓网
            text = it.get("content", "")
            if is_fully_ad(it.get("title", ""), text):
                print(f"[ad-skip] {it['title']}（整篇纯广告，跳过）")
                stats["ad_skip"] += 1
                continue
            purified = purify_content(text)
            if len(purified.strip()) < 20:
                print(f"[skip] {it['title']}（动态正文过短，跳过）")
                stats["short_skip"] += 1
                continue
            # 短动态轻量化：正文较短不走重 LLM 总结模板，直接存「短动态速览」（原文+元信息），
            # 省 token 且避免笔记库被短评灌水；新鲜度标签由 save_summarized_article 统一追加。
            if len(purified) <= SHORT_DYNAMIC_MAX:
                try:
                    from articles.main import save_summarized_article
                    save_summarized_article(
                        f"（短动态速览 · 未做深度总结）\n\n{purified}",
                        original_url=it["url"], author=it.get("mp_name", ""),
                        tags=["动态速览", "短动态"], original_title=it.get("title", ""),
                        note_type="dynamic", publish_time=it.get("publish_time", 0),
                    )
                    print(f"[bilibili-动态-速览] {it['title']}: 已存短动态速览")
                    stats["dynamic_light"] += 1
                except Exception as e:
                    print(f"[error] {it['title']}: {e}", file=sys.stderr)
                    stats["error"] += 1
                continue
            src_text = f"{purified}\n\n---\n原文链接：{it['url']}"
            try:
                from articles.main import skill_main
                res = skill_main({"content": src_text, "author": it.get("mp_name", ""),
                                  "publish_time": it.get("publish_time", 0)})
                msg = res.get("message", "") if isinstance(res, dict) else str(res)
                print(f"[bilibili-动态] {it['title']}: {msg}")
                stats["dynamic_full"] += 1
            except Exception as e:
                print(f"[error] {it['title']}: {e}", file=sys.stderr)
                stats["error"] += 1
        else:
            continue

    # 📊 健康度一行：视频 / 动态（速览·完整）/ 文章 / 跳过项，异常时一眼可见
    print(
        f"\n📊 本轮健康度：视频 {stats['video']}（充电跳过 {stats['video_charging_skip']}）"
        f" / 动态 {stats['dynamic_full'] + stats['dynamic_light']}"
        f"（速览 {stats['dynamic_light']} · 完整 {stats['dynamic_full']}）"
        f" / 文章 {stats['article']}"
        f" | 广告跳过 {stats['ad_skip']} · 过短跳过 {stats['short_skip']} · 错误 {stats['error']}"
    )


def main():
    parser = argparse.ArgumentParser(description="订阅监控")
    parser.add_argument("--apply", action="store_true", help="发现后直接调用总结管线")
    parser.add_argument("--mode", choices=["auto", "first"], default="auto",
                        help="auto=首次抓最近N、之后增量抓最近N+去重(默认,每天调度用它)；first=强制首跑(最近N,忽略已处理)")
    parser.add_argument("--first-run", action="store_true", help="等价 --mode first")
    args = parser.parse_args()
    mode = "first" if args.first_run else args.mode

    subs = load_subscriptions()
    state = load_state()
    all_new = discover_all(subs, state, mode=mode)
    save_state(state)

    if args.apply:
        apply_summaries(all_new)
    else:
        print(json.dumps(all_new, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
