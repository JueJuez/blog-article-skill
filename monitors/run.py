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
      run.py 检测到失效则本次跳过公众号源、保 B站照跑；交互式会话弹码续期、下次运行恢复）。
"""
import sys
import os
import re
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
# 公众号正文限流重试队列：正文抓空（疑似微信限流）的文章暂存于此，下次运行优先重抓
PENDING_REFETCH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pending_refetch.json")
# 降级待总结队列：无外部 AI 时抓到的原文（raw 文件）在此排队，由外层执行模型总结后
# 调 articles.save_summary_only 落盘（含 folder 归档），完成降级闭环
PENDING_SUMMARY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pending_summaries.json")
# 每类型「安全上限」（防极端 UP 单窗口发几百条刷爆笔记）；真正的内容量由
# 时间窗口（BILI_FIRST_WINDOW_DAYS / BILI_DAILY_WINDOW_DAYS）约束，不是这个 count。
FIRST_RUN_LIMIT = int(os.environ.get("FIRST_RUN_LIMIT", "50"))
# 短动态轻量化阈值：正文去广告净化后长度 <= 此值，走「短动态速览」（直接存原文，不调重 LLM 总结模板）
SHORT_DYNAMIC_MAX = int(os.environ.get("BILI_SHORT_DYNAMIC_MAX", "80"))
# 公众号逐篇抓正文的间隔秒数（+0~3s 抖动）。2026-07-23 实测：无间隔连抓 25 篇，
# 第 5 篇起被微信限流返回空正文页——这个间隔是硬保护，不要调 0。
WECHAT_GAP = float(os.environ.get("WECHAT_GAP", "6"))
# 正文短于此值视为「限流空页/无正文」，不落 raw、进重试队列（下次运行优先重抓）
MIN_CONTENT_LEN = int(os.environ.get("WECHAT_MIN_CONTENT_LEN", "100"))
# 监控产出默认归档分类（Obsidian/飞书目录第一级）；订阅条目可用 "category" 字段覆盖
DEFAULT_CATEGORY = os.environ.get("MONITOR_DEFAULT_CATEGORY", "投资交易")


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _item_folder(it: dict) -> str:
    """按「分类/账号名」生成归档子目录，如「投资交易/舟亦横」。"""
    category = it.get("category") or DEFAULT_CATEGORY
    name = it.get("sub_name") or it.get("mp_name") or ""
    return f"{category}/{name}" if name else category


def _queue_pending_summary(it: dict, res: dict) -> None:
    """AI 降级时把待总结条目入队（按 url 去重），供外层执行模型接单。"""
    if not isinstance(res, dict) or not res.get("need_continue_summary"):
        return
    pending = _load_json(PENDING_SUMMARY_PATH, [])
    url = it.get("url", "")
    if url and any(p.get("url") == url for p in pending):
        return
    entry = {
        "url": url,
        "title": res.get("original_title") or it.get("title", ""),
        "author": res.get("author") or it.get("mp_name", ""),
        "note_type": res.get("note_type", ""),
        "tags": res.get("tags") or [],
        "publish_time": it.get("publish_time", 0),
        "folder": _item_folder(it),
        "raw_file": res.get("raw_file", ""),
        "queued_at": int(time.time()),
    }
    pending.append(entry)
    _save_json(PENDING_SUMMARY_PATH, pending)
    print(f"[need-summary] 已入降级队列: {entry['title']} -> {entry['raw_file']}")


def _promote_empty_summaries() -> int:
    """不变量保护：pending_summaries 中的条目必须携带真实正文。
    若某条的 raw_file 缺失/正文过短（限流空壳），将其提升回 pending_refetch
    （正文重试队列），使 --refetch-only 成为唯一抓取重试入口，无需 refetch_recover 之类补丁脚本。
    返回提升条数。"""
    s = _load_json(PENDING_SUMMARY_PATH, [])
    if not s:
        return 0
    keep, move = [], []
    for x in s:
        rf = x.get("raw_file", "")
        empty = True
        if rf and os.path.exists(rf):
            try:
                t = open(rf, encoding="utf-8").read()
                body = t.split("\n---\n", 1)[-1] if "\n---\n" in t else t
                body = re.split(r"原文链接", body)[0].replace("> 原始文章内容（自动暂存）", "").strip()
                empty = len(body) < MIN_CONTENT_LEN
            except Exception:
                empty = True
        if empty:
            move.append({k: x[k] for k in x if k != "content"})
        else:
            keep.append(x)
    if move:
        _save_json(PENDING_SUMMARY_PATH, keep)
        ref = _load_json(PENDING_REFETCH_PATH, [])
        seen = {r.get("url") for r in ref}
        for m in move:
            if m.get("url") not in seen:
                ref.append(m)
        _save_json(PENDING_REFETCH_PATH, ref)
    return len(move)


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
                    found = src.discover(state, first_run_limit=FIRST_RUN_LIMIT, mode=mode)
                    for it in found:
                        it["category"] = w.get("category", "")
                        it["sub_name"] = w.get("name", "")
                    all_new.extend(found)
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
            found = src.discover(state, first_run_limit=FIRST_RUN_LIMIT, mode=mode)
            for it in found:
                it["category"] = b.get("category", "")
                it["sub_name"] = b.get("name", "")
            all_new.extend(found)
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
        "empty_retry": 0,
    }
    # 上次运行被限流抓空的文章：优先重抓（发现阶段已 mark seen，不重试就永久漏）
    refetch_prev = _load_json(PENDING_REFETCH_PATH, [])
    if refetch_prev:
        print(f"[refetch] 恢复上次限流未抓到正文的 {len(refetch_prev)} 篇，优先重抓")
        items = refetch_prev + items
    refetch_next = []  # 本轮仍抓空的，写回队列下次再试
    _first_article = True
    for it in items:
        if it["route"] in ("article", "cv"):
            # 逐篇间隔（硬保护）：无间隔连抓会触发微信限流返回空正文页
            if not _first_article:
                time.sleep(WECHAT_GAP + random.uniform(0, 3))
            _first_article = False
            # 自抓正文一次：整篇纯广告则 skip；否则净化夹带广告后喂总结
            try:
                fetched = fetch_web_content(it["url"])
                title = fetched[0] if isinstance(fetched, tuple) else it.get("title", "")
                content = fetched[1] if isinstance(fetched, tuple) else ""
            except Exception as e:
                print(f"[fetch-err] {it['title']}: {e}", file=sys.stderr)
                stats["error"] += 1
                refetch_next.append({k: it[k] for k in it if k != "content"})
                continue
            # 正文过短 = 限流空页/无正文：不落 raw、不喂总结，进重试队列
            if len((content or "").strip()) < MIN_CONTENT_LEN:
                print(f"[empty-retry] {it['title']}（正文 {len((content or '').strip())} 字，"
                      f"疑似限流/无正文，已入重试队列）")
                stats["empty_retry"] += 1
                refetch_next.append({k: it[k] for k in it if k != "content"})
                continue
            real_title = title or it.get("title", "")
            if is_fully_ad(real_title, content or ""):
                print(f"[ad-skip] {real_title}（整篇纯广告，跳过）")
                stats["ad_skip"] += 1
                continue
            purified = purify_content(content or "")
            src_text = f"{purified}\n\n---\n原文链接：{it['url']}"
            try:
                from articles.main import skill_main
                res = skill_main({"content": src_text, "author": it.get("mp_name", ""),
                                  "publish_time": it.get("publish_time", 0),
                                  "original_title": it.get("title", ""),
                                  "folder": _item_folder(it)})
                msg = res.get("message", "") if isinstance(res, dict) else str(res)
                print(f"[{it['source']}] {it['title']}: {msg}")
                _queue_pending_summary(it, res if isinstance(res, dict) else {})
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
                res = summarize_video({"url": it["url"], "publish_time": it.get("publish_time", 0),
                                       "folder": _item_folder(it)})
                msg = res.get("message", "") if isinstance(res, dict) else str(res)
                print(f"[bilibili] {it['title']}: {msg}")
                _queue_pending_summary(it, res if isinstance(res, dict) else {})
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
                        folder=_item_folder(it),
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
                                  "publish_time": it.get("publish_time", 0),
                                  "original_title": it.get("title", ""),
                                  "folder": _item_folder(it)})
                msg = res.get("message", "") if isinstance(res, dict) else str(res)
                print(f"[bilibili-动态] {it['title']}: {msg}")
                _queue_pending_summary(it, res if isinstance(res, dict) else {})
                stats["dynamic_full"] += 1
            except Exception as e:
                print(f"[error] {it['title']}: {e}", file=sys.stderr)
                stats["error"] += 1
        else:
            continue

    # 限流/失败的文章写回重试队列（下次运行优先重抓；空队列则清掉文件内容）
    _save_json(PENDING_REFETCH_PATH, refetch_next)
    if refetch_next:
        print(f"\n⏳ {len(refetch_next)} 篇正文未抓到（限流/失败），已存重试队列，下次运行自动重抓")

    # 📊 健康度一行：视频 / 动态（速览·完整）/ 文章 / 跳过项，异常时一眼可见
    print(
        f"\n📊 本轮健康度：视频 {stats['video']}（充电跳过 {stats['video_charging_skip']}）"
        f" / 动态 {stats['dynamic_full'] + stats['dynamic_light']}"
        f"（速览 {stats['dynamic_light']} · 完整 {stats['dynamic_full']}）"
        f" / 文章 {stats['article']}"
        f" | 广告跳过 {stats['ad_skip']} · 过短跳过 {stats['short_skip']}"
        f" · 限流待重试 {stats['empty_retry']} · 错误 {stats['error']}"
    )

    # 降级待总结队列提示：外层执行模型据此接单（Read raw → 按模板总结 → save_summary_only）
    pending = _load_json(PENDING_SUMMARY_PATH, [])
    if pending:
        print(
            f"\n🤖 NEED_CONTINUE_SUMMARY: {len(pending)} 条内容已抓取但无外部 AI 总结，"
            f"清单见 {PENDING_SUMMARY_PATH}\n"
            f"   外层模型请逐条处理：Read 条目 raw_file → 按 note_type 模板"
            f"（prompts.templates.get_note_prompt）总结 → 调 articles.main.save_summary_only("
            f"{{summarized_content, original_url, author, tags, original_title, publish_time, folder}}) "
            f"落盘 → 从队列移除该条。"
        )


def main():
    parser = argparse.ArgumentParser(description="订阅监控")
    parser.add_argument("--apply", action="store_true", help="发现后直接调用总结管线")
    parser.add_argument("--mode", choices=["auto", "first"], default="auto",
                        help="auto=首次抓最近N、之后增量抓最近N+去重(默认,每天调度用它)；first=强制首跑(最近N,忽略已处理)")
    parser.add_argument("--first-run", action="store_true", help="等价 --mode first")
    parser.add_argument("--refetch-only", action="store_true",
                        help="统一抓取重试入口：重抓 pending_refetch 中的限流文章，并把 pending_summaries 里 raw 为空的条目也提升回重试；重抓后自动重总结")
    args = parser.parse_args()
    mode = "first" if args.first_run else args.mode

    if args.refetch_only:
        n = _promote_empty_summaries()
        if n:
            print(f"[refetch] 将 {n} 条 raw 为空的总结队列条目提升回正文重试队列")
        apply_summaries([])  # apply 内部自动合并 pending_refetch 队列并重抓+重总结
        return

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
