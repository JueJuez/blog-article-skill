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
      run.py 检测到失效会自动弹码并阻塞等待扫码续期，扫到即自动继续抓取公众号源、无需手动重跑；
      超时或 WECHAT_RELOGIN_WAIT=0 才退回「本次跳过、下次恢复」；B站照常不受影响）。
"""
import sys
import os
import re
import json
import time
import random
import argparse
import subprocess

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
    WereadClient, WechatSource, trigger_relogin,
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
# 系列课降级待总结队列：monitors/run.py 在发现 B站系列且 AI 不可用时登记，
# 由执行模型（Agent）串行落盘（避免并发重复节点）。对应 drainer: apply_pending_series.py
PENDING_SERIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pending_series.json")
# 每类型「安全上限」（防极端 UP 单窗口发几百条刷爆笔记）；真正的内容量由
# 时间窗口（BILI_FIRST_WINDOW_DAYS / BILI_DAILY_WINDOW_DAYS）约束，不是这个 count。
FIRST_RUN_LIMIT = int(os.environ.get("FIRST_RUN_LIMIT", "50"))
# 短动态轻量化阈值：正文去广告净化后长度 <= 此值，走「短动态速览」（直接存原文，不调重 LLM 总结模板）
SHORT_DYNAMIC_MAX = int(os.environ.get("BILI_SHORT_DYNAMIC_MAX", "80"))
# 公众号逐篇抓正文的间隔秒数（+0~3s 抖动）。2026-07-23 实测：无间隔连抓 25 篇，
# 第 5 篇起被微信限流返回空正文页——这个间隔是硬保护，不要调 0。
WECHAT_GAP = float(os.environ.get("WECHAT_GAP", "6"))
# 公众号 token 失效触发重新登录后，主进程阻塞等待用户扫码续期的时长（秒）。
# 扫到即自动继续抓取公众号（无需手动重跑）；超时则本次跳过、下次恢复。
# 设为 0 可恢复「本次跳过、下次恢复」旧行为（适合无人值守定时任务）。
WECHAT_RELOGIN_WAIT = int(os.environ.get("WECHAT_RELOGIN_WAIT", "180"))
# 正文短于此值视为「限流空页/无正文」，不落 raw、进重试队列（下次运行优先重抓）
MIN_CONTENT_LEN = int(os.environ.get("WECHAT_MIN_CONTENT_LEN", "100"))
# 监控产出默认归档分类（Obsidian/飞书目录第一级）；订阅条目可用 "category" 字段覆盖
DEFAULT_CATEGORY = os.environ.get("MONITOR_DEFAULT_CATEGORY", "投资交易")
# 公众号文章连续抓取正文为空（限流空页 / 微信扫码墙 / 文章已删除）达到此次数，
# 判定为「不可抓取」，移出重试队列并明确上报（不再无限重试刷虚假告警）。可调。
WECHAT_MAX_REFETCH = int(os.environ.get("WECHAT_MAX_REFETCH", "3"))
# ---- scys（生财有术）日常监控（「跑一下」第三源，2026-08-20 接入） ----
# 复用 scripts/scys_batch_fetch.py 入口按领域增量抓新帖。窗口默认 7 天而非 1 天：
# 新帖常在发布数日后才被标精华（互动也要时间发酵），窗口太窄会永久漏掉
# 「晚精华/晚高互动」帖；重复抓由其 state.json 的 done 列表去重兜底，窗口放大
# 只多翻列表页、不多抓正文。传 --no-digested-only：精华直通 + 非精华按互动门槛
# （投锚≥30 或 点赞≥80，阈值在 scys_projects.json）过滤，兼顾官方指南污染问题。
SCYS_CONFIG_PATH = os.path.join(BASE_DIR, "scripts", "scys_projects.json")
SCYS_PENDING_PATH = os.path.join(BASE_DIR, "notes", "_scraped", "scys", "pending_summaries.json")
SCYS_DAILY_WINDOW_DAYS = int(os.environ.get("SCYS_DAILY_WINDOW_DAYS", "7"))
SCYS_FIRST_WINDOW_DAYS = int(os.environ.get("SCYS_FIRST_WINDOW_DAYS", "7"))
SCYS_DAILY_LIST_PAGES = int(os.environ.get("SCYS_DAILY_LIST_PAGES", "2"))


def _scys_daily_cmd(project: str, since_days: int) -> list:
    return [sys.executable, os.path.join(BASE_DIR, "scripts", "scys_batch_fetch.py"),
            "--project", project, "--since-days", str(since_days),
            "--pages", str(SCYS_DAILY_LIST_PAGES),
            "--no-digested-only"]


def run_scys_daily(entries: list, mode: str = "auto") -> None:
    """「跑一下」的 scys 分支：按 subscriptions.json 的 scys 列表逐领域增量抓新帖。

    复用 scys_batch_fetch.py 全链路（列表 → 窗口/精华过滤 → 限速抓取 → 入 scys
    待总结队列），去重靠其 state.json；CDP 不可用 / 单领域失败 → 告警跳过，
    不影响公众号与 B站结果。领域必须存在于 scripts/scys_projects.json。
    """
    try:
        with open(SCYS_CONFIG_PATH, "r", encoding="utf-8") as f:
            known = json.load(f).get("projects", {})
    except Exception:
        known = {}
    default_days = SCYS_FIRST_WINDOW_DAYS if mode == "first" else SCYS_DAILY_WINDOW_DAYS
    for s in entries:
        project = s.get("project", "")
        if project not in known:
            print(f"[warn] scys 领域「{project}」不在 scripts/scys_projects.json，跳过", file=sys.stderr)
            continue
        days = int(s.get("since_days") or default_days)
        print(f"\n[scys] 领域「{project}」增量抓取（近 {days} 天窗口，精华过滤按配置默认）...")
        try:
            r = subprocess.run(_scys_daily_cmd(project, days), cwd=BASE_DIR)
        except Exception as e:
            print(f"[warn] scys {project} 启动失败: {e}", file=sys.stderr)
            continue
        if r.returncode != 0:
            print(f"[warn] scys {project} 抓取退出码 {r.returncode}"
                  f"（可能 CDP 不可用 / 已有 scys 抓取进程在跑），跳过该领域", file=sys.stderr)
        time.sleep(3)


def _decide_retry_or_drop(it: dict, max_retry: int):
    """抓取正文失败后的处置决策。返回 ("retry", item_with_count) 或 ("drop", reason)。

    - 累计失败次数 < max_retry：继续重试（写入 refetch_next，下次优先重抓）
    - 累计失败次数 >= max_retry：判定不可抓取，移出队列并给出原因（由调用方上报用户）
    """
    cnt = int(it.get("refetch_count", 0)) + 1
    if cnt >= max_retry:
        reason = (f"连续 {cnt} 次抓取正文为空（疑似微信扫码墙 / 限流 / 文章已删除），"
                  f"已停止自动重试")
        return ("drop", reason)
    new_it = {k: it[k] for k in it if k != "content"}
    new_it["refetch_count"] = cnt
    return ("retry", new_it)


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
    """统一路由器：监控内容归档到 【监控】/<平台>/<账号名>（或系列课子节点）。

    单一真相源见 shared/routing.resolve_folder；不再用旧的「分类/账号」扁平路径。
    """
    from shared.routing import resolve_folder
    return resolve_folder({
        "author": it.get("mp_name") or it.get("sub_name"),
        "mp_name": it.get("mp_name"),
        "sub_name": it.get("sub_name"),
        "source": it.get("source"),
        "route": it.get("route"),
        "url": it.get("url"),
        "category": it.get("category"),
    })


# 账号/周刊通称后缀：命中这些词时，列表标题视为「无实质主题」，需从正文提炼真实标题
_ACCOUNT_GENERIC_SUFFIXES = (
    # 单字/双字通称
    "研究", "精选", "周报", "日报", "晨报", "晚报", "周评", "月报",
    "季报", "年报", "热点", "要闻", "资讯", "动态", "合集", "专辑",
    "目录", "汇总", "回顾", "前瞻", "观察", "点评", "解读", "聚焦",
    # 常见组合
    "本周精选", "本月精选", "本周热点", "本周要闻", "本周资讯", "本周动态",
    "本周汇总", "本周回顾", "本周前瞻", "本周观察", "本周点评", "本周解读",
    "今日热点", "今日要闻", "今日资讯", "今日精选",
    "本周研究", "本周研报", "行业研究", "市场点评", "策略点评",
)


def _is_account_generic_title(title: str) -> bool:
    """标题等于监控账号名/alias，或「账号名/alias + 通称后缀」时，判为泛化。"""
    t = (title or "").strip()
    if not t:
        return True
    try:
        from shared.routing import load_account_registry
        reg = load_account_registry()
    except Exception:
        reg = {}
    for name, info in reg.items():
        aliases = [name] + list(info.get("aliases") or [])
        for a in aliases:
            a = (a or "").strip()
            if not a:
                continue
            if t == a:
                return True
            for suf in _ACCOUNT_GENERIC_SUFFIXES:
                if t == a + suf or t == suf:
                    return True
    return False


# 泛化/问候型列表标题：这些标题在多个不同推文里重复使用，列表不可区分，
# 必须改从正文首句提炼真标题（如「大家好，我是哥飞。」是哥飞真实推文标题，
# 但每篇都叫这名；中金也常把不同文章都叫「中金研究」）。
_GENERIC_RE = re.compile(
    r'^(大家好[，, ]*我是|各位[好呀]?|哈喽|嗨[，, ]|hi[\s,，]|hello[\s,，]|'
    r'亲爱的(朋友|读者)|朋友[们]?：)',
    re.IGNORECASE,
)


def _is_generic_title(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return True
    if _GENERIC_RE.match(t):
        return True
    # 自报式标题「大家好，我是XXX」整体作为标题
    if re.match(r'^大家好，我是', t):
        return True
    # 过短且无实质中文（如纯账号名/符号）
    if len(t) <= 6 and not re.search(r'[\u4e00-\u9fff]{4,}', t):
        return True
    # 账号名/alias + 通称后缀（如「中金研究」「中金点睛本周精选」）也需从正文提炼
    if _is_account_generic_title(t):
        return True
    return False


def derive_title_from_body(raw_file: str, list_title: str, max_len: int = 36) -> str:
    """列表标题是泛化/问候型时，从正文首句提炼更有信息量的标题。

    规则（确定性，无外部 AI）：
      - 列表标题不泛化 → 原样返回（保留高质量标题，如「7月19日哥飞的朋友们…」）。
      - 泛化 → 读 raw 正文，跳过与列表标题相同的问候首行、引用块、分隔线，
        取首个实质段落的首句（到句末标点）作为标题，过长则截断加省略号。
      - 任何异常或提炼失败 → 退回列表标题，绝不静默丢标题。
    """
    if not _is_generic_title(list_title):
        return list_title.strip()
    if not raw_file or not os.path.exists(raw_file):
        return list_title.strip()
    try:
        text = open(raw_file, encoding="utf-8").read()
    except Exception:
        return list_title.strip()
    in_body = False
    for ln in text.splitlines():
        if ln.strip() == "---":
            in_body = True
            continue
        if not in_body:
            continue
        s = ln.strip()
        if not s or s.startswith(">") or s == list_title.strip():
            continue
        m = re.search(r'[。！？!?]', s)
        cand = s[: m.end()] if m else s
        cand = cand.strip()
        if len(cand) > max_len:
            cand = cand[:max_len].rstrip("，,、；;") + "…"
        if cand and cand != list_title.strip():
            return cand
    return list_title.strip()


def _queue_pending_summary(it: dict, res: dict) -> None:
    """AI 降级时把待总结条目入队（按 url 去重），供外层执行模型接单。"""
    if not isinstance(res, dict) or not res.get("need_continue_summary"):
        return
    pending = _load_json(PENDING_SUMMARY_PATH, [])
    url = it.get("url", "")
    if url and any(p.get("url") == url for p in pending):
        return
    # 机械前置①（DECISION-20260825）：已总结过的 URL 不入队，省 AI 总结 token
    if url:
        from articles import dedup as _dedup
        if _dedup.is_summarized(url=url):
            print(f"[need-summary] 跳过入队（已总结过）: {url}")
            return
    # 标题从正文提炼：列表标题若泛化（如「大家好，我是哥飞。」），改从 body 首句命名
    list_title = res.get("original_title") or it.get("title", "")
    entry_title = derive_title_from_body(res.get("raw_file", ""), list_title)
    entry = {
        "url": url,
        "title": entry_title,
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
    print(f"[need-summary] 已入降级队列: {entry_title} -> {entry['raw_file']}")


def _queue_pending_series(it: dict, res: dict) -> None:
    """系列课降级时把待总结集登记到 pending_series.json，供执行模型（Agent）串行落盘。

    背景：_handle_bilibili_series 在 AI 不可用（FORCE_AGENT_MODE）时把每集字幕降级成
    notes/<系列名>/*_raw.md，但原本的返回值没有 need_continue_summary，导致监控落盘闭环
    （只认 pending_summaries 里的顶级 _raw_*.md）静默丢弃系列 raw，系列课从不被自动总结。
    此处把系列信息（series_title/series_dir/url/author/各集 raw 路径）登记到专门队列，
    并让 apply_pending_series.py 接管落盘，形成完整闭环。
    """
    data = _load_json(PENDING_SERIES_PATH, [])
    entry = {
        "series_title": res.get("series_title", ""),
        "series_dir": res.get("series_dir", ""),
        "url": res.get("url", it.get("url", "")),
        "author": res.get("author", it.get("sub_name", "")),
        "degraded_raws": res.get("degraded_raws", []),
        "queued_at": int(time.time()),
    }
    # 同系列不重复登记：已存在则合并 raw 列表（按相对路径去重）
    for d in data:
        if d.get("series_title") == entry["series_title"]:
            merged = set(d.get("degraded_raws", [])) | set(entry["degraded_raws"])
            d["degraded_raws"] = sorted(merged)
            break
    else:
        data.append(entry)
    _save_json(PENDING_SERIES_PATH, data)
    n = len(entry["degraded_raws"])
    print(f"   🤖 NEED_AGENT_SERIES_SUMMARY: 系列「{entry['series_title']}」{n} 集待总结，"
          f"详见 {os.path.basename(PENDING_SERIES_PATH)}")


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


def write_subscriptions(subs: dict) -> None:
    with open(SUB_PATH, "w", encoding="utf-8") as f:
        json.dump(subs, f, ensure_ascii=False, indent=2)
        f.write("\n")


def find_subscription(subs: dict, query: str):
    """机械查重：某 UP/公众号/scys 是否已在监控名单。

    返回 (platform, entry) 或 None。query 匹配：B站按 uid/name/aliases；
    公众号按 name/aliases/share_url/mp_id；scys 按 project。大小写不敏感、精确匹配。
    用于「新增订阅前先查重」，杜绝手搓 JSON 导致的重复添加（不再靠记忆）。
    """
    q = (query or "").strip().lower()
    if not q:
        return None

    def _hit(entry: dict) -> bool:
        for key in ("uid", "name", "mp_id", "project", "share_url"):
            v = entry.get(key)
            if v is not None and str(v).lower() == q:
                return True
        for alias in entry.get("aliases", []) or []:
            if str(alias).lower() == q:
                return True
        return False

    for b in subs.get("bilibili", []):
        if _hit(b):
            return ("bilibili", b)
    for w in subs.get("wechat", []):
        if _hit(w):
            return ("wechat", w)
    for s in subs.get("scys", []):
        if _hit(s):
            return ("scys", s)
    return None


def cmd_subscribe(args, subs: dict) -> None:
    """机械新增 B站UP 监控：先查重，已在名单则回『已在监控名单内』且不修改；
    不在则按窗口参数追加到 subscriptions.json 并落盘。

    窗口语义（对齐「监控新增 / 首抓逻辑」总纲）：
      --sub-all       → 首跑全量（all_videos:true，抓整门课/全部视频）
      --sub-window N  → 首跑窗口 N 天（N个月=N*30；1年=365；2年=730）
      都不传          → 首跑默认 30 天（靠 _BILI_FIRST_WINDOW_DAYS 常量）
    """
    uid = (args.uid or "").strip()
    name = (args.name or "").strip()
    if not uid or not name:
        print("❌ --subscribe 需同时提供 --uid 与 --name", file=sys.stderr)
        return
    # 查重：按 name 或 uid 任一命中即视为已监控
    hit = find_subscription(subs, name) or find_subscription(subs, uid)
    if hit:
        platform, entry = hit
        print(f"✅ 「{name}」已在监控名单内（{platform}），无需重复添加。"
              f"如需按指定窗口一次性抓取，请显式说明。")
        return
    entry = {"name": name, "uid": uid}
    if args.category:
        entry["category"] = args.category
    if args.sub_all:
        entry["all_videos"] = True
    elif args.sub_window:
        entry["window_days"] = args.sub_window
    subs.setdefault("bilibili", []).append(entry)
    write_subscriptions(subs)
    if args.sub_all:
        win_desc = "首跑全量(all_videos)"
    elif args.sub_window:
        win_desc = f"首跑窗口 {args.sub_window} 天"
    else:
        win_desc = "首跑默认 30 天"
    print(f"➕ 已新增 B站监控：{name}（uid={uid}）— {win_desc}。")


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


def _wait_for_token_refresh(old_token: str, timeout: int, interval: float = 3.0) -> tuple:
    """token 失效触发重新登录后，阻塞等待用户扫码续期。

    轮询 monitors/.wechat_auth.json + is_token_valid 探针，直到拿到与旧 token 不同的
    有效 token（用户扫完即返回）；超时返回 ("", "")。配合 trigger_relogin() 启动的
    后台 poll daemon 使用：daemon 扫到码写入文件，本函数检测到即让主流程继续抓取。

    timeout <= 0 时立即返回空（恢复「本次跳过」旧行为）。
    """
    if timeout <= 0:
        return "", ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(interval)
        tok, vid = load_weread_auth()
        if tok and tok != old_token:
            try:
                if WereadClient(token=tok, vid=vid).is_token_valid():
                    return tok, vid
            except Exception:
                pass  # 代理可能刚写入尚未就绪，继续等
    return "", ""


def _discover_wechat_retry(src, state, mode, name, retries: int = 3) -> tuple:
    """带重试的公众号发现：瞬错（网络/401）重试，避免误触发整轮重登等待。

    weread 代理极不稳定，单次 discover 偶发 401/空——这些大多是瞬时抖动，
    重试即可恢复；只有「token 确实失效」才应在 discover_all 入口（已重试确认的
    is_token_valid=False，或全源零结果 + 持续 401）走阻塞扫码流程。此处永不阻塞。

    返回 (items, auth_failed)：auth_failed 仅在「重试耗尽后最后一次仍为 401」时置 True，
    用于 discover_all 区分「代理瞬错」与「token 真正过期」。
    """
    last_err = None
    auth_failed = False
    for attempt in range(1, retries + 1):
        try:
            return src.discover(state, first_run_limit=FIRST_RUN_LIMIT, mode=mode), False
        except Exception as e:
            last_err = e
            if "401" in str(e):
                if attempt < retries:
                    print(f"[retry] wechat {name} 第{attempt}次 discover 遇 401，"
                          f"{attempt}s 后重试({attempt}/{retries})...", file=sys.stderr)
                    time.sleep(attempt)  # 轻量退避
                    continue
                auth_failed = True  # 重试耗尽仍是 401 → 视为 token 过期
            break
    print(f"[warn] wechat {name} discover 失败（已重试{retries}次）: {last_err}",
          file=sys.stderr)
    return [], auth_failed


def discover_all(subs: dict, state: dict, mode: str = "auto",
                 force_all: bool = False) -> list:
    all_new: list = []
    token, vid = load_weread_auth()

    # ---------- 微信源（token 自愈 + 扫码后自动续抓） ----------
    # 设计要点（首跑踩坑后固化，2026-07-28 又加固一次）：
    #   1) 仅在「token 缺失 / 经重试确认的 is_token_valid=False」时才阻塞等扫码
    #      （用户期望的「扫完自动续抓」）；token 有效时的 401 一律当瞬错重试，
    #      绝不误触发 180s 重登等待（否则代理抖动会卡死整轮并弹多余二维码）。
    #   2) 单号 discover 自带重试（_discover_wechat_retry，默认 3 次），
    #      返回 auth_failed 标记「重试耗尽仍为 401」以区分代理瞬错 vs token 过期。
    #   3) 发现阶段不落盘 state（见 main 的 --apply 守卫），预览不会「吃掉」待抓条目。
    #   4)【2026-07-28 加固】is_token_valid 探针改打 resolve_mp（过期 token 稳定 401），
    #      不再打 list_articles（过期返回 200 空、失明）。并新增兜底：预检误判有效、
    #      但全部源零结果且持续 401 时，仍触发重登并刷新后重试一轮——根治「过期却不弹码」。
    wechat_subs = subs.get("wechat", [])
    if wechat_subs:
        # 选一个真实 share_url 作为探针：resolve_mp(force=True) 对过期 token 稳定 401，
        # 而 list_articles 对过期 token 返回 200 空，无法用于失效检测。
        probe_share = next((w.get("share_url") for w in wechat_subs if w.get("share_url")), "")
        token_valid = bool(token) and WereadClient(token=token, vid=vid).is_token_valid(probe_share_url=probe_share)
        relogin_triggered = False
        if not token_valid:
            print("⚠️ [微信读书] 未检测到有效 token，自动触发重新登录并等待扫码...",
                  file=sys.stderr)
            qr_path = trigger_relogin()
            if not qr_path:
                print("⚠️ 重新登录触发失败，本次跳过公众号源（B站照常）。", file=sys.stderr)
                wechat_subs = []
            else:
                print(f"RELOGIN_QR:{qr_path}", file=sys.stderr)
                print(f"⏳ 已生成二维码，正在等待扫码（最长 {WECHAT_RELOGIN_WAIT}s）；"
                      f"扫到即自动继续抓取公众号，无需手动重跑。", file=sys.stderr)
                new_tok, new_vid = _wait_for_token_refresh(token, WECHAT_RELOGIN_WAIT)
                if new_tok:
                    print("✅ 扫码成功，token 已刷新，继续抓取公众号源。", file=sys.stderr)
                    token, vid = new_tok, new_vid
                    relogin_triggered = True
                else:
                    print(f"⏰ 等待扫码超时（{WECHAT_RELOGIN_WAIT}s），本次跳过公众号源"
                          f"（B站照常）；下次运行自动恢复。", file=sys.stderr)
                    wechat_subs = []

        # 第一轮 discovery（预检通过或重登成功后）
        total_items = 0
        any_auth_fail = False
        for w in wechat_subs:
            try:
                client = WereadClient(token=token, vid=vid)
                src = WechatSource(client, mp_id=w.get("mp_id", ""),
                                   share_url=w.get("share_url", ""), name=w.get("name", ""))
                found, auth = _discover_wechat_retry(src, state, mode, w.get("name", ""))
                for it in found:
                    it["category"] = w.get("category", "")
                    it["sub_name"] = w.get("name", "")
                all_new.extend(found)
                total_items += len(found)
                any_auth_fail |= auth
                time.sleep(2)  # weread 代理频率退避，避免单号日内超次
            except Exception as e:
                # 兜底（2026-08-17 加）：单源 discover 异常（代理 401 未完全兜住等）
                # 不再让整轮 run.py 崩溃退出（旧 Exit 1 根因），跳过该源、记错误、继续其他源。
                print(f"[warn] wechat {w.get('name','?')} discover 失败，跳过该源: "
                      f"{type(e).__name__} {str(e)[:160]}", file=sys.stderr)
                any_auth_fail = True
                continue

        # 兜底：预检认为 token 有效，但全部源零结果且出现持续性 401 →
        # 说明 is_token_valid 仍漏判（极少数情况下过期 token 在 resolve_mp 也返回非 401）。
        # 此时真正触发重登，刷新后重试整轮，避免公众号静默全挂。
        if (not relogin_triggered) and token_valid and total_items == 0 and any_auth_fail:
            print("⚠️ [微信读书] 预检通过但全部源零结果且持续 401，判定 token 已过期，"
                  "自动触发重新登录并等待扫码...", file=sys.stderr)
            qr_path = trigger_relogin()
            if qr_path:
                print(f"RELOGIN_QR:{qr_path}", file=sys.stderr)
                print(f"⏳ 已生成二维码，正在等待扫码（最长 {WECHAT_RELOGIN_WAIT}s）...",
                      file=sys.stderr)
                new_tok, new_vid = _wait_for_token_refresh(token, WECHAT_RELOGIN_WAIT)
                if new_tok:
                    print("✅ 扫码成功，token 已刷新，重试抓取公众号源。", file=sys.stderr)
                    token, vid = new_tok, new_vid
                    for w in wechat_subs:
                        client = WereadClient(token=token, vid=vid)
                        src = WechatSource(client, mp_id=w.get("mp_id", ""),
                                           share_url=w.get("share_url", ""), name=w.get("name", ""))
                        found, _ = _discover_wechat_retry(src, state, mode, w.get("name", ""))
                        for it in found:
                            it["category"] = w.get("category", "")
                            it["sub_name"] = w.get("name", "")
                        all_new.extend(found)
                        time.sleep(2)
                else:
                    print(f"⏰ 等待扫码超时（{WECHAT_RELOGIN_WAIT}s），本次跳过公众号源"
                          f"（B站照常）；下次运行自动恢复。", file=sys.stderr)

    for b in subs.get("bilibili", []):
        src = BilibiliSource(b["uid"], types=b.get("types"),
                             all_videos=b.get("all_videos", False),
                             window_days=b.get("window_days"),
                             force_all=force_all)
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


def apply_summaries(items: list, obsidian: bool = False) -> None:
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
    # 按发布时间倒序处理：新的先创建，使飞书 wiki 默认列表呈现「日期近在前，旧在后」
    items.sort(key=lambda x: x.get("publish_time", 0) or 0, reverse=True)
    refetch_next = []  # 本轮仍抓空的，写回队列下次再试
    dropped = []  # 连续多次抓不到（墙文/已删除/持续限流），移出队列待上报
    _first_article = True
    for it in items:
        if it["route"] in ("article", "cv"):
            # 逐篇间隔（硬保护）：无间隔连抓会触发微信限流返回空正文页
            if not _first_article:
                time.sleep(WECHAT_GAP + random.uniform(0, 3))
            _first_article = False
            # 自抓正文（一手自动补抓，2026-08-17 升级）：
            # 微信对直连 mp.weixin.qq.com 有频控，单次抓取常返回空页/墙页（生财外链帖尤甚，
            # 表现为正文只有「原文链接：https://...」壳）。此处同一次运行内退避重试
            # WECHAT_MAX_REFETCH 次，命中即继续总结；重试耗尽仍空才降级到跨轮 refetch 队列/丢弃。
            fetched = None
            last_err = None
            title = it.get("title", "")
            content = ""
            for _att in range(WECHAT_MAX_REFETCH + 1):
                try:
                    fetched = fetch_web_content(it["url"])
                except Exception as e:
                    fetched = None
                    last_err = e
                if isinstance(fetched, tuple):
                    title = fetched[0] or title
                    content = fetched[1] or ""
                else:
                    content = ""
                if len((content or "").strip()) >= MIN_CONTENT_LEN:
                    break  # 拿到正文，立即跳出重试
                # 壳/限流空页：退避后重试（一手补抓）
                if _att < WECHAT_MAX_REFETCH:
                    time.sleep(6 * (_att + 1) + random.uniform(0, 3))
            # 重试耗尽仍空：走原有跨轮重试/丢弃逻辑（fetch 异常 or 正文过短）
            if fetched is None or len((content or "").strip()) < MIN_CONTENT_LEN:
                if fetched is None:
                    print(f"[fetch-err] {it['title']}: {last_err}", file=sys.stderr)
                    stats["error"] += 1
                    refetch_next.append({k: it[k] for k in it if k != "content"})
                    continue
                decision, payload = _decide_retry_or_drop(it, WECHAT_MAX_REFETCH)
                stats["empty_retry"] += 1
                if decision == "drop":
                    dropped.append({
                        "title": it.get("title", ""),
                        "mp_name": it.get("mp_name", "") or it.get("sub_name", ""),
                        "url": it.get("url", ""),
                        "reason": payload,
                    })
                    print(f"[drop-gate] {it['title']}（{payload}）")
                else:
                    refetch_next.append(payload)
                    print(f"[empty-retry] {it['title']}（正文 {len((content or '').strip())} 字，"
                          f"疑似限流/无正文，已入重试队列 {payload.get('refetch_count')}/{WECHAT_MAX_REFETCH}）")
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
                _cat = it.get("category", "")
                res = skill_main({"content": src_text, "author": it.get("mp_name", ""),
                                  "publish_time": it.get("publish_time", 0),
                                  "original_title": real_title,
                                  "tags": [c for c in [_cat] if c],
                                  "folder": _item_folder(it), "obsidian": obsidian})
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
                                       "folder": _item_folder(it), "obsidian": obsidian})
                msg = res.get("message", "") if isinstance(res, dict) else str(res)
                print(f"[bilibili] {it['title']}: {msg}")
                # 系列课降级：把待总结的集字幕登记到系列待总结队列，由 Agent 串行落盘（避免漏接）
                if isinstance(res, dict) and res.get("degraded_any") and res.get("degraded_raws"):
                    _queue_pending_series(it, res)
                    stats["video"] += 1
                    continue
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
                        tags=["动态速览", "短动态"] + [c for c in [it.get("category", "")] if c],
                        original_title=it.get("title", ""),
                        note_type="dynamic", publish_time=it.get("publish_time", 0),
                        folder=_item_folder(it), obsidian=obsidian,
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
                _cat = it.get("category", "")
                res = skill_main({"content": src_text, "author": it.get("mp_name", ""),
                                  "publish_time": it.get("publish_time", 0),
                                  "original_title": it.get("title", ""),
                                  "tags": [c for c in [_cat] if c],
                                  "folder": _item_folder(it), "obsidian": obsidian})
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

    # 不可抓取（墙文/已删除/持续限流）明确上报，避免静默丢失用户订阅内容
    if dropped:
        print(f"\n⚠️ 以下 {len(dropped)} 篇连续 {WECHAT_MAX_REFETCH} 次抓不到正文，已移出重试队列"
              f"（不再自动重试；如需总结请手动粘贴原文）：")
        for d in dropped:
            print(f"   - 《{d['title']}》｜{d['mp_name']}｜{d['url']}")
            print(f"     └ 原因：{d['reason']}")

    # 📊 健康度一行：视频 / 动态（速览·完整）/ 文章 / 跳过项，异常时一眼可见
    print(
        f"\n📊 本轮健康度：视频 {stats['video']}（充电跳过 {stats['video_charging_skip']}）"
        f" / 动态 {stats['dynamic_full'] + stats['dynamic_light']}"
        f"（速览 {stats['dynamic_light']} · 完整 {stats['dynamic_full']}）"
        f" / 文章 {stats['article']}"
        f" | 广告跳过 {stats['ad_skip']} · 过短跳过 {stats['short_skip']}"
        f" · 限流待重试 {len(refetch_next)} · 墙文移除 {len(dropped)} · 错误 {stats['error']}"
    )

    # Agent 待总结队列提示：由 WorkBuddy 执行模型（主 Agent / 子 Agent）接单处理
    pending = _load_json(PENDING_SUMMARY_PATH, [])
    if pending:
        print(
            f"\n🤖 NEED_AGENT_SUMMARY: {len(pending)} 条内容已抓取，等待执行模型（Agent）总结。"
            f"清单见 {PENDING_SUMMARY_PATH}\n"
            f"   处理路径：Read 条目 raw_file → 按 note_type 模板"
            f"（prompts.templates.get_note_prompt）总结 → 调 articles.main.save_summary_only("
            f"{{summarized_content, original_url, author, tags, original_title, publish_time, folder}}) "
            f"落盘 → 从队列移除该条。"
        )

    # 系列课降级待总结队列提示（与单篇队列对称）
    series_pending = _load_json(PENDING_SERIES_PATH, [])
    if series_pending:
        total_ep = sum(len(s.get("degraded_raws", [])) for s in series_pending)
        print(
            f"\n🤖 NEED_AGENT_SERIES_SUMMARY: {len(series_pending)} 个系列课、共 {total_ep} 集待总结。"
            f"清单见 {PENDING_SERIES_PATH}\n"
            f"   处理路径：执行模型按 notes/<系列名>/*_raw.md 分片总结成 body → 串行调"
            f" videos.main._save_series_note 落飞书（避免并发重复节点）→ 跑 apply_pending_series.py 收尾。"
        )

    # 自动落地：若已有 .body.md（Agent 此前已总结 / 本次会话稍后总结），直接落飞书。
    # 系列课的「总结」由执行模型在收尾例程里完成（与单篇对称），本调用负责「落地」闭环，
    # 使系列课与单篇一样全自动：检测 →（Agent 总结 raws→bodies）→ 落地，无需手动命令。
    try:
        from apply_pending_series import drain_series_pending
        drain_series_pending(obsidian=obsidian)
    except Exception as e:
        print(f"  ⚠️ 系列课自动落地异常（非致命）：{e}")


def main():
    parser = argparse.ArgumentParser(description="订阅监控")
    parser.add_argument("--apply", action="store_true", help="发现后直接调用总结管线")
    parser.add_argument("--mode", choices=["auto", "first"], default="auto",
                        help="auto=首次抓最近N、之后增量抓最近N+去重(默认,每天调度用它)；first=强制首跑(最近N,忽略已处理)")
    parser.add_argument("--first-run", action="store_true", help="等价 --mode first")
    parser.add_argument("--refetch-only", action="store_true",
                        help="统一抓取重试入口：重抓 pending_refetch 中的限流文章，并把 pending_summaries 里 raw 为空的条目也提升回重试；重抓后自动重总结")
    parser.add_argument("--obsidian", action="store_true",
                        help="同时写入 Obsidian（默认只写飞书）")
    parser.add_argument("--all-videos", action="store_true",
                        help="事后强制全量重抓：无视首跑/seen 门禁，翻全部分页抓所有 B站视频。"
                             "仅用于『seen 已填充、想补历史』的显式场景；"
                             "已总结过的视频（dedup 索引命中）自动跳过，不会重复入队/重复落盘。"
                             "默认不传则首跑按各源配置（全抓=all_videos / 指定窗口 / 默认30天）、"
                             "之后增量（1~2天窗口、封顶30天 + seen 去重）只处理新增")
    # ---- 机械新增/查重订阅 ----
    parser.add_argument("--check-subscribed", type=str, default="",
                        help="机械查重：输入名字/uid，报告是否已在监控名单（bilibili/wechat/scys）")
    parser.add_argument("--subscribe", action="store_true",
                        help="机械新增 B站UP 监控：先查重，已在名单则回『已在监控名单内』不添加；"
                             "否则按窗口参数追加到 subscriptions.json")
    parser.add_argument("--uid", type=str, default="", help="--subscribe 用：B站UP主 uid")
    parser.add_argument("--name", type=str, default="", help="--subscribe 用：UP主名字")
    parser.add_argument("--category", type=str, default="", help="--subscribe 用：分类标签")
    parser.add_argument("--sub-all", action="store_true",
                        help="--subscribe 用：首跑全量（all_videos:true，抓整门课/全部视频）")
    parser.add_argument("--sub-window", type=float, default=0,
                        help="--subscribe 用：首跑窗口天数（N个月=N*30；1年=365；2年=730）")
    # ---- 公众号历史回溯（续批）功能 ----
    parser.add_argument("--backfill", action="store_true",
                        help="公众号历史回溯（续批）模式：配合 --names/--since 入队，或 --drain 取队列 job")
    parser.add_argument("--names", type=str, default="",
                        help="回溯目标公众号名（逗号分隔，须存在于 subscriptions.json 的 wechat 列表）")
    parser.add_argument("--since", type=str, default="",
                        help="回溯起点：YYYY-MM-DD 或时间戳；早于该日期的文章不抓")
    parser.add_argument("--batch", type=int, default=0,
                        help="每批最大入队篇数（默认 15），控制单次运行规模、便于自动续批")
    parser.add_argument("--drain", action="store_true",
                        help="从 backfill_targets.json 队列取第一个未完成 job 续批（自动化用）")
    parser.add_argument("--reset-backfill", type=str, default="",
                        help="重置指定公众号的回溯完成状态（逗号分隔名），便于重新往前翻")
    args = parser.parse_args()
    mode = "first" if args.first_run else args.mode

    subs = load_subscriptions()
    state = load_state()

    if args.check_subscribed:
        hit = find_subscription(subs, args.check_subscribed)
        if hit:
            platform, entry = hit
            print(f"✅ 已在监控名单内（{platform}）：{entry.get('name', entry)}")
        else:
            print(f"ℹ️ 不在监控名单内：{args.check_subscribed}")
        return

    if args.subscribe:
        cmd_subscribe(args, subs)
        return

    if args.refetch_only:
        n = _promote_empty_summaries()
        if n:
            print(f"[refetch] 将 {n} 条 raw 为空的总结队列条目提升回正文重试队列")
        apply_summaries([], args.obsidian)  # apply 内部自动合并 pending_refetch 队列并重抓+重总结
        return

    if args.backfill:
        cmd_backfill(args, subs, state)
        return

    all_new = discover_all(subs, state, mode=mode, force_all=args.all_videos)

    if not all_new:
        # 首跑踩坑：discover_all 返回 0 时容易被误认成「成功无新内容」，这里显式提示，
        # 便于第一时间发现「代理冷启动 / 限流 / 窗口内无更新 / 订阅源异常」。
        print("ℹ️ 本轮 discover_all 返回 0 条新内容（窗口内无更新 / 代理暂时无响应 / "
              "或订阅源异常）。若为异常，请稍后重试；未抓取时不落盘 state。",
              file=sys.stderr)

    if args.apply:
        # 仅在实际抓取时落盘 state（标记已发现条目），避免「仅预览」就把待抓条目标记为
        # 已处理，导致后续 --apply 永远抓不到它们（首跑已踩此坑：发现模式把中金点睛 3 篇
        # 标记为 seen，后续 --apply 直接去重成 0）。
        save_state(state)
        apply_summaries(all_new, args.obsidian)
        # scys（生财有术）日常增量：独立子进程复用 scys_batch_fetch.py 全链路，
        # 抓到的原文进 scys 专属待总结队列（与单篇队列闭环方式相同）
        if subs.get("scys"):
            run_scys_daily(subs["scys"], mode=mode)
            scys_pending = _load_json(SCYS_PENDING_PATH, [])
            if scys_pending:
                print(f"\n🤖 NEED_AGENT_SCYS_SUMMARY: scys {len(scys_pending)} 篇待总结。"
                      f"清单见 {SCYS_PENDING_PATH}\n"
                      f"   处理路径：Read 条目 output 指向的 md 原文 → 按模板总结 → "
                      f"save_summary_only 落飞书（folder=生财有术/<领域>）→ 从队列移除该条。")
    else:
        print(json.dumps(all_new, ensure_ascii=False, indent=2))


def cmd_backfill(args, subs: dict, state: dict) -> None:
    """公众号历史回溯（续批）子命令。

    复用 discover_all 的全链路（token 预检/扫码续期/抓历史/seen 去重/apply 落盘闭环），
    仅通过 env 把范围限定到目标号、把窗口改成 since、把每批上限改成 batch；
    回溯游标（seen + state["backfill"][name]）必须落盘，否则无法跨运行续批。
    """
    from monitors import backfill as bf

    # 重置：清掉指定号的回溯完成标记，便于重新往前翻
    if args.reset_backfill:
        names = [n.strip() for n in args.reset_backfill.split(",") if n.strip()]
        bf.reset_backfill(state, names)
        save_state(state)
        print(f"♻️ 已重置回溯完成状态：{', '.join(names)}（下次 backfill 会重新往前翻）")
        return

    # 解析目标：--drain 从队列取第一个未完成 job；否则用 --names + --since
    if args.drain and not args.names:
        job = bf.first_pending_job()
        if not job:
            print("ℹ️ 回溯队列 backfill_targets.json 无未完成 job。")
            return
        names = job["names"]
        since = job["since"]
        batch = int(job.get("batch", bf.DEFAULT_BATCH))
        # 旧 job 若 since 深于稳定边界，直接夹到边界（避免队列里残留深历史 job 无限重试）
        job_ts = bf._parse_since(since)
        boundary_ts = bf.default_since()
        if job_ts < boundary_ts:
            since = time.strftime("%Y-%m-%d", time.localtime(boundary_ts))
            print(f"⚠️ job 的 since 深于稳定边界，已夹取到 {since}")
        print(f"📥 队列 job：{', '.join(names)} | since {since} | batch {batch}")
    elif args.names:
        names = [n.strip() for n in args.names.split(",") if n.strip()]
        # since 默认 = 稳定边界（最近 35 天），超过此边界不补；仍可显式指定 --since 临时覆盖。
        if args.since:
            since = args.since
            since_ts = bf._parse_since(since)
            boundary_ts = bf.default_since()
            if since_ts < boundary_ts:
                print(f"⚠️ 指定 since 深于稳定边界 {bf.DEFAULT_BACKFILL_DAYS} 天，"
                      "深历史代理不可靠，仍按你指定执行但请预期漏段。", file=sys.stderr)
        else:
            since = time.strftime("%Y-%m-%d", time.localtime(bf.default_since()))
            print(f"ℹ️ --since 未指定，使用默认稳定边界：{since}（最近 {bf.DEFAULT_BACKFILL_DAYS} 天）")
        batch = args.batch or bf.DEFAULT_BATCH
        bf.add_job(names, since, batch)
        print(f"📥 回溯：{', '.join(names)} | since {since} | batch {batch}")
    else:
        print("❌ --backfill 需 --names <逗号名> 或 --drain", file=sys.stderr)
        return

    since_ts = bf._parse_since(since)
    # 设 env，复用 discover_all：只跑微信（bilibili=[] 避免混入日常动态/视频），
    # 范围限定到 names，窗口改 since，每批上限 = batch。
    os.environ["WECHAT_BACKFILL"] = "1"
    os.environ["WECHAT_BACKFILL_NAMES"] = ",".join(names)
    os.environ["WECHAT_BACKFILL_SINCE"] = str(since_ts)
    os.environ["FIRST_RUN_LIMIT"] = str(batch)
    wechat_only = {"wechat": subs.get("wechat", []), "bilibili": []}

    # 稳定窗口内短退避重试：代理偶发空窗，重试几次即可；深历史已不再追求，
    # 不需要 20 次×60s 的长重试。
    all_new = []
    max_attempts = int(os.environ.get("WECHAT_BACKFILL_ATTEMPTS", "5"))
    probe_share = next((w.get("share_url") for w in subs.get("wechat", []) if w.get("share_url")), "")
    for attempt in range(1, max_attempts + 1):
        # 中途 token 过期（weread token 寿命 ~2h）则弹码续上。
        tok, vid = load_weread_auth()
        if not (tok and WereadClient(token=tok, vid=vid).is_token_valid(probe_share_url=probe_share)):
            print("⚠️ [回溯] 检测到 token 失效，自动触发重新登录并等待扫码...", file=sys.stderr)
            qr = trigger_relogin()
            if qr:
                print(f"RELOGIN_QR:{qr}", file=sys.stderr)
                print(f"⏳ 已生成二维码，等待扫码（最长 {WECHAT_RELOGIN_WAIT}s）后继续回溯...",
                      file=sys.stderr)
                nt, nv = _wait_for_token_refresh(tok, WECHAT_RELOGIN_WAIT)
                if nt:
                    print("✅ 扫码成功，token 已刷新，继续回溯。", file=sys.stderr)
                    continue
                print(f"⏰ 等待扫码超时（{WECHAT_RELOGIN_WAIT}s），本次跳过公众号回溯。",
                      file=sys.stderr)
                break
            print("⚠️ 重新登录触发失败，本次跳过公众号回溯。", file=sys.stderr)
            break
        all_new = discover_all(wechat_only, state, mode="auto")
        if all_new:
            break
        if attempt < max_attempts:
            backoff = min(8 * attempt, 30)
            print(f"[backfill-retry] 第{attempt}次代理返回空（抖动），{backoff}s 后重试"
                  f"（最多 {max_attempts} 次）...", file=sys.stderr)
            time.sleep(backoff)
    if not all_new:
        print("⚠️ 回溯重试耗尽仍 0 条（代理持续空窗），本次跳过。",
              file=sys.stderr)

    # 游标（seen）只在真正入队（--apply）时落盘 —— 与日常监控一致：
    # 仅预览不落盘，避免把文章标记 seen 却未总结，导致后续漏抓。
    if args.apply:
        save_state(state)
        apply_summaries(all_new, args.obsidian)
        if args.drain and not args.names:
            bf.mark_job_done(state, names, note="shallow_backfill_run_completed")
    else:
        print(json.dumps(all_new, ensure_ascii=False, indent=2))
    bf.report_progress(state, names, since_ts)


if __name__ == "__main__":
    main()
