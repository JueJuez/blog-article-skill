"""monitors/weread.py — 微信读书直连公众号源（PLAN-20260919，wewe-rss 死代理的接替方案）。

事实依据全部来自 2026-09-18/19 登录态实测（references/weread-direct-source.md，勿凭记忆改）：
  - 列表 GET /web/mp/articles?bookId=&offset=N：登录态 200；未登录/安全检测 → {"errCode":-2041}
  - 签名：页面内 window.__WRPA__.sr(api).join(',') 作 x-wrpa-0 头；登录 cookie 是真正门槛
  - 翻页：每页固定 20 条，offset += len(reviews)，空列表即到底（50 步长会漏页，禁用）
  - reviewId = MP_WXS_<bid>_<token>，末段即 mp.weixin.qq.com/s/<token> 原文直链

生产纪律（红线，内置非可选）：
  - 列表只在页面内 fetch（同源带登录 cookie + 页面签名）；requests 直调仅限免签名接口
  - 增量按时间窗过滤（WEREAD_WINDOW_DAYS，断跑自动补齐、封顶 30 天）：日更号 30 天
    ≈30 篇、周更号≈4 篇，翻页翻到「早于窗口起点」即停，与更新频率无关
  - 登录失效（-2041 且无验证码）→ 自动开登录页截二维码等扫码（WEREAD_AUTO_RELOGIN，
    扫码成功 cookie 浏览器内自动生效、无需换任何凭据，扫到即继续抓）；页面出现
    验证码 → 停手截图上报（过码走 scripts/weread_captcha.py，识别需模型在场）
  - 列表间隔 ≥2s + 抖动；个人单账号低频自用，不做号池
  - 正文不在此模块抓：条目与旧代理源同形（route=article）→ 既有 apply_summaries
    管线走 mp.weixin.qq.com 直链抓正文（风险隔离：weread 每号每天只 1~2 次列表请求）

开关：WEREAD_SOURCE_ENABLED（run.py 读，默认 0）。订阅名单复用 subscriptions.json 的
wechat 列表，bookId 映射独立维护（BOOK_ID_FALLBACK + monitors/.mp_cache.json 反查）。
历史补全（「公众号补全 / 补到什么时候」）：run.py --weread-backfill --names X --since 日期。
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time

from monitors.state import get_seen, mark_seen, effective_window_days

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WEREAD_BASE = "https://weread.qq.com"
LIST_API = "/web/mp/articles?bookId={book_id}&offset={offset}"
MP_URL = "https://mp.weixin.qq.com/s/{token}"

MP_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".mp_cache.json")
# bookId 兜底表（.mp_cache.json 缺失/损坏时用；来源 references/weread-direct-source.md §6。
# 无在线解析途径，新增号需手工在微信读书页面请求里抄 MP_WXS_ id）
BOOK_ID_FALLBACK = {
    "中金点睛": "MP_WXS_3270332840",
    "DeepVan的逃生地牢": "MP_WXS_3905719449",
    "哥飞": "MP_WXS_2399233620",
    "生财有术": "MP_WXS_3891906515",
}

# 每号列表请求间隔下限（秒），叠加 0~1s 抖动；防封纪律见模块 docstring
LIST_GAP = float(os.environ.get("WEREAD_LIST_GAP", "2"))
# 每号每次运行拉的页数（1 页 = 20 条）。日常增量保持 1；历史补齐另起任务显式传大值
DAILY_PAGES = int(os.environ.get("WEREAD_DAILY_PAGES", "1"))
# 增量时间窗（天）：只抓窗口内发布的文章（按 createTime 过滤，与更新频率无关——
# 日更号 30 天≈30 篇、周更号≈4 篇）；翻页翻到「早于窗口起点」即停。0=关闭时间过滤
WEREAD_WINDOW_DAYS = float(os.environ.get("WEREAD_WINDOW_DAYS", "2"))
# 窗口封顶（天）：断跑自动补齐时最多补到这里（对齐 B站 WECHAT_MAX_WINDOW_DAYS 语义）
WEREAD_MAX_WINDOW_DAYS = float(os.environ.get("WEREAD_MAX_WINDOW_DAYS", "30"))
# 单号单轮翻页安全上限（防极端高频号翻爆；正常 30 天窗口 ≤2 页）
WEREAD_MAX_PAGES = int(os.environ.get("WEREAD_MAX_PAGES", "15"))
# 登录态失效自动弹码：-2041 且无验证码 → 打开登录页截二维码等扫码（0=关闭，退回纯上报）
WEREAD_AUTO_RELOGIN = os.environ.get("WEREAD_AUTO_RELOGIN", "1") == "1"
# 等扫码时长（秒）；扫到即自动继续抓取该号及后续号，超时跳过本轮
WEREAD_RELOGIN_WAIT = int(os.environ.get("WEREAD_RELOGIN_WAIT", "180"))
# 请求配额（熔断线，双层）：weread 列表请求持久化计数（.weread_quota.json，
# 点文件不入库）。日层按自然天清零、小时层按自然小时清零；任一层到线即熔断：
# 日常监控跳过公众号源（B站/scys 照跑）、补全任务停止续批。
# 依据防封纪律：单日「十几请求封顶」（25 = 4 号日常 2 页 + 补齐余量）+ 小时级
# 防突发（6/小时，2026-09-19 连环验证码事故教训：短时密集请求比日总量更危险）
WEREAD_DAILY_QUOTA = int(os.environ.get("WEREAD_DAILY_QUOTA", "25"))
WEREAD_HOURLY_QUOTA = int(os.environ.get("WEREAD_HOURLY_QUOTA", "6"))
# 连环码熔断（用户定策 2026-09-19）：正常仅 1 次人机验证；同日第 2 次「确定」提交
# = 高危风控信号（行为像人机/请求过多才会连环触发），熔断 N 小时不发任何请求
WEREAD_CAPTCHA_SERIAL_LIMIT = int(os.environ.get("WEREAD_CAPTCHA_SERIAL_LIMIT", "2"))
WEREAD_RISK_COOLDOWN_HOURS = float(os.environ.get("WEREAD_RISK_COOLDOWN_HOURS", "12"))
QUOTA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".weread_quota.json")


class WereadQuotaExhausted(RuntimeError):
    """weread 请求配额耗尽（日/小时层熔断）。消息即用户可读的任务返回。"""


def _load_quota() -> dict:
    try:
        with open(QUOTA_PATH, encoding="utf-8") as f:
            q = json.load(f)
        return q if isinstance(q, dict) else {}
    except Exception:
        return {}


def quota_today() -> int:
    """今天已用的 weread 列表请求数（台账日期非今天 = 0）。"""
    q = _load_quota()
    return int(q.get("count", 0)) if q.get("date") == time.strftime("%Y-%m-%d") else 0


def quota_this_hour() -> int:
    """本自然小时已用的请求数（台账小时非当前 = 0）。"""
    q = _load_quota()
    return int(q.get("hour_count", 0)) if q.get("hour") == time.strftime("%Y-%m-%d %H") else 0


def quota_remaining() -> int:
    """当前还可发的请求数 = min(日层余量, 小时层余量)。"""
    return max(0, WEREAD_DAILY_QUOTA - quota_today()),         max(0, WEREAD_HOURLY_QUOTA - quota_this_hour())


def quota_remaining_n() -> int:
    d, h = quota_remaining()
    return min(d, h)


def quota_block_message() -> str:
    """到线的层对应任务消息（先报小时层——它整点就恢复，更 actionable）。"""
    d, h = quota_remaining()
    if h <= 0:
        return (f"公众号 weread 请求已达小时上限 {WEREAD_HOURLY_QUOTA}/小时，已熔断跳过；"
                f"整点后自动恢复")
    return (f"公众号 weread 请求已达单日上限 {WEREAD_DAILY_QUOTA}，已熔断跳过；"
            f"明天自动恢复（补全任务再跑一次即续批）")


def _save_quota(q: dict) -> None:
    try:
        with open(QUOTA_PATH, "w", encoding="utf-8") as f:
            json.dump(q, f, ensure_ascii=False)
    except Exception:
        pass


def risk_blocked_seconds() -> float:
    """连环码高危熔断剩余秒数（未熔断 = 0）。"""
    q = _load_quota()
    until = float(q.get("risk_until", 0) or 0)
    return max(0.0, until - time.time())


def record_captcha_event() -> str | None:
    """记一次人机验证「确定」提交（每次提交 = 消耗一个挑战）。

    同日提交次数 ≥ WEREAD_CAPTCHA_SERIAL_LIMIT → 高危熔断：置 risk_until 并返回
    用户可读的熔断消息（未触发返回 None）。事件列表按自然日判定、跨天清零。
    """
    today = time.strftime("%Y-%m-%d")
    q = _load_quota()
    if q.get("date") != today:
        q = {"date": today, "count": 0,
             "hour": time.strftime("%Y-%m-%d %H"),
             "hour_count": q.get("hour_count", 0) if q.get("hour") == time.strftime("%Y-%m-%d %H") else 0,
             "captcha_events": []}
    events = [e for e in (q.get("captcha_events") or []) if isinstance(e, str)]
    events.append(time.strftime("%H:%M"))
    q["captcha_events"] = events
    _save_quota(q)
    if len(events) >= WEREAD_CAPTCHA_SERIAL_LIMIT:
        until = time.time() + WEREAD_RISK_COOLDOWN_HOURS * 3600
        q["risk_until"] = until
        _save_quota(q)
        return weread_block_message()
    return None


def weread_block_message() -> str | None:
    """统一请求闸门：连环码高危熔断优先，其次日/小时配额。None = 放行。"""
    secs = risk_blocked_seconds()
    if secs > 0:
        return (f"⚠️ 今日连环出现 {WEREAD_CAPTCHA_SERIAL_LIMIT} 次人机验证（高危风控信号："
                f"行为像人机/请求过多才会连环触发），weread 已熔断，"
                f"{int(secs // 3600)}小时{int(secs % 3600 // 60)}分后自动恢复；期间不发任何请求")
    if quota_remaining_n() <= 0:
        return quota_block_message()
    return None


def quota_record(n: int = 1) -> None:
    """记 n 次请求（日/小时双层持久化；跨天/跨小时各自自动重置）。"""
    today = time.strftime("%Y-%m-%d")
    hour = time.strftime("%Y-%m-%d %H")
    q = _load_quota()
    if q.get("date") != today:
        q["date"], q["count"] = today, 0
    if q.get("hour") != hour:
        q["hour"], q["hour_count"] = hour, 0
    q["count"] = int(q.get("count", 0)) + n
    q["hour_count"] = int(q.get("hour_count", 0)) + n
    _save_quota(q)
# 等 __WRPA__ 签名器（WASM）就绪的超时秒数。实测签名器只在 /web 等 SPA 路由加载，
# 首页 `/` 不加载（2026-09-19 诊断，见 references/weread-direct-source.md §2.4）
WRPA_WAIT_S = float(os.environ.get("WEREAD_WRPA_WAIT_S", "30"))

# -2041=未登录/安全检测（实测真凶）；-2010/-2012=登录态类错误码。均无官方文档：
# 只观测上报，绝不自动重试硬闯、绝不猜测性换 cookie
AUTH_ERROR_CODES = {-2010, -2012, -2041}

# 「安全检测中」遮罩 / 验证码标记（2026-09-19 实测补充：点选码是 iframe 里的组件，
# 文案为「选择最符合描述的图片」，主 frame innerText 查不到——检测必须遍历 frames）
CAPTCHA_MARKERS = ("安全检测", "安全验证", "验证码", "人机识别", "拖动滑块", "完成拼图",
                   "最符合描述的图片")
CAPTCHA_DIR = os.path.join(BASE_DIR, "_tmp", "weread_probe")

_REVIEW_ID_RE = re.compile(r"^MP_WXS_\d+_(.+)$")
# mp.weixin.qq.com/s/<token> 直链 token 的实测安全字符集（不含 ~ 等）。
# 含 ~ 的 originalId（实测约 1/10 条目）拼直链必「参数错误」——疑似非 /s/ 型 token，
# 处理：不入队、不标 seen，健康度行持续暴露（观测上报，等用户反馈，不猜映射规则）
_MP_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


# ---------------------------------------------------------------------------
# 纯函数（单测覆盖层：不依赖 playwright / CDP）
# ---------------------------------------------------------------------------

def parse_reviews(resp) -> list:
    """列表响应 → 展平的 review 列表（reviews[] → subReviews[] → review）。

    非 dict / 结构缺层时返回 []，绝不抛异常（列表结构是逆向结论，防御式解析）。
    """
    if not isinstance(resp, dict):
        return []
    out = []
    for grp in resp.get("reviews") or []:
        if not isinstance(grp, dict):
            continue
        for sub in grp.get("subReviews") or []:
            r = sub.get("review") if isinstance(sub, dict) else None
            if isinstance(r, dict) and r.get("reviewId"):
                out.append(r)
    return out


def extract_token(review_id: str) -> str:
    """reviewId = MP_WXS_<bid>_<token> → 末段即原文 token（title 与 mp 页面逐字比对已验证）。

    不匹配格式返回 ""（调用方跳过该条，不用残缺 id 拼直链）。
    """
    m = _REVIEW_ID_RE.match(review_id or "")
    return m.group(1) if m else ""


def classify_list_response(resp) -> str:
    """列表响应分类（只认观测事实，不猜）：
      "ok"          → 有 reviews，可继续
      "empty"       → 无 errCode 且 reviews 空（翻页到底）
      "auth"        → errCode ∈ AUTH_ERROR_CODES（登录态/安全检测，观测上报路径）
      "unknown_err" → 其他非零 errCode（同样上报，不硬闯）
    """
    if not isinstance(resp, dict):
        return "unknown_err"
    code = resp.get("errCode", 0) or 0
    if code:
        return "auth" if code in AUTH_ERROR_CODES else "unknown_err"
    return "ok" if parse_reviews(resp) else "empty"


def paginate(fetch_page, book_id: str, max_pages: int = 1, cutoff_ts: int = None):
    """翻页累加器（纯逻辑，fetch_page 由调用方注入）。

    offset += len(reviews)（实测每页固定 20 条、页间无缝衔接；issue #442 的 50 步长
    实测会跳过每页之间约 30 条，禁用）。空列表即到底；中途遇 auth/unknown_err
    立即中断上报。cutoff_ts 给定时：翻到「最老一条 createTime < cutoff_ts」即停
    （时间窗语义——窗口内全要、窗口外不翻，与号更新频率无关）。
    返回 (reviews, end_category, last_resp)。
    """
    reviews, offset = [], 0
    last_resp = None
    for _ in range(max_pages):
        last_resp = fetch_page(book_id, offset)
        cat = classify_list_response(last_resp)
        if cat == "empty":
            break  # 空列表 = 翻页到底，正常结束
        if cat != "ok":
            return reviews, cat, last_resp
        page_items = parse_reviews(last_resp)
        if not page_items:
            break
        reviews.extend(page_items)
        offset += len(page_items)
        if cutoff_ts is not None and page_items and \
                all(int(r.get("createTime", 0) or 0) < cutoff_ts for r in page_items):
            break  # 整页都早于窗口起点：窗口已覆盖，停止翻页
    return reviews, "ok", last_resp


def review_to_item(review: dict, mp_name: str, category: str = "") -> dict | None:
    """review → 监控条目（与旧代理源同形：route=article → 既有管线按 mp 直链抓正文）。"""
    rid = review.get("reviewId", "") if isinstance(review, dict) else ""
    token = extract_token(rid)
    if not token:
        return None
    info = review.get("mpInfo") or {}
    return {
        "source": "wechat",
        "mp_name": mp_name,
        "id": rid,
        "title": info.get("title", "") or review.get("title", ""),
        "publish_time": int(review.get("createTime", 0) or 0),
        "url": MP_URL.format(token=token),
        "route": "article",  # -> articles.skill_main（正文走 fetch_web_content，勿在此抓）
    }


def resolve_book_ids(subs: dict) -> list:
    """subscriptions.json 的 wechat 名单 → weread 条目 [{name, book_id, category}]。

    bookId 解析顺序：BOOK_ID_FALLBACK 硬编码表 → monitors/.mp_cache.json 按 share_url
    反查（旧代理时代的映射缓存，仍有效）。解析不到的号打 warn 跳过。
    """
    cache = {}
    try:
        with open(MP_CACHE_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        for share_url, info in raw.items():
            if isinstance(info, dict) and info.get("id"):
                cache[share_url] = info["id"]
    except Exception:
        pass
    entries = []
    for w in subs.get("wechat", []):
        name = w.get("name", "")
        book_id = BOOK_ID_FALLBACK.get(name) or cache.get(w.get("share_url", ""))
        if not book_id:
            print(f"[weread] 「{name}」无 bookId 映射（硬编码表/mp_cache 均未命中），跳过。"
                  f"新增号请手工在微信读书页面请求里抄 MP_WXS_ id 更新 BOOK_ID_FALLBACK",
                  file=sys.stderr)
            continue
        entries.append({"name": name, "book_id": book_id, "category": w.get("category", "")})
    return entries


def format_health(health: dict) -> str:
    """health dict → 人类可读 weread 健康度段（供 run.py 打印）。"""
    seg = f"正常 {health.get('ok', 0)} 号 · 新文章 {health.get('new', 0)}"
    if health.get("quota_exhausted"):
        seg += f" · ⛔{quota_block_message()}"
    if health.get("baseline"):
        seg += f" · 首跑基线 {'/'.join(health['baseline'])}（不回填历史）"
    if health.get("captcha"):
        seg += " · ⚠️触发安全检测（已截图，停手等反馈）"
    if health.get("unreachable"):
        seg += f" · 直链不可达 {len(health['unreachable'])} 篇（非 /s/ 型 token，未入队待反馈）"
    for name, kind, detail in health.get("errors", []):
        seg += f" · 异常 {name}({kind}: {detail})"
    if health.get("skipped"):
        seg += f" · 本轮跳过 {'/'.join(health['skipped'])}"
    return seg


# ---------------------------------------------------------------------------
# 页面层（CDP）：验证码检测 / 截图 / 页内 fetch 客户端
# ---------------------------------------------------------------------------

def detect_captcha(page) -> bool:
    """页面（含全部 iframe）出现「安全检测中」遮罩 / 验证码标记 → True。

    ⚠️ 点选验证码组件在 iframe 里渲染（2026-09-19 实测），只查主 frame 会漏检。
    """
    frames = getattr(page, "frames", None) or [page]
    for frame in frames:
        try:
            text = frame.evaluate("() => document.body ? document.body.innerText : ''") or ""
        except Exception:
            continue
        if any(m in text for m in CAPTCHA_MARKERS):
            return True
    return False


def screenshot_captcha(page) -> str:
    """截图存档 _tmp/weread_probe/captcha-<ts>.png，返回路径（失败返回 ""，不阻断上报）。"""
    try:
        os.makedirs(CAPTCHA_DIR, exist_ok=True)
        path = os.path.join(CAPTCHA_DIR, "captcha-%d-%03d.png" % (
            int(time.time()), int(time.time() * 1000) % 1000))
        page.screenshot(path=path)
        return path
    except Exception:
        return ""


def _find_or_open_weread_page(session):
    """定位既有 weread 页，没有则新开并导航到 SPA 路由（§2.7 坑 2：s.page 不一定是 weread 页）。

    ⚠️ 两个实测约束（2026-09-19）：
    - 必须导航到 /web 等 SPA 路由（如 /web/shelf）：首页 `/` **不加载** __WRPA__ 签名器；
    - 页内 fetch 前需等 `window.__WRPA__` 就绪（WASM 异步加载），就绪即可签名。

    ⚠️ 调用方须保证「开页→导航→全部 fetch」在同一 SharedCdpSession 存活窗口内完成
    （坑 1：单持有者 with 退出会杀 Chrome）。
    """
    page = next((p for p in session.context.pages if "weread.qq.com" in (p.url or "")), None)
    if page is None:
        page = session.new_page()
        page.goto(WEREAD_BASE + "/web/shelf", wait_until="domcontentloaded", timeout=30_000)
    if not _ensure_wrpa_ready(page):
        # 现页无签名器（如停在首页 / 或深链失效页）：导航到 SPA 稳定路由再等一轮
        page.goto(WEREAD_BASE + "/web/shelf", wait_until="domcontentloaded", timeout=30_000)
        _ensure_wrpa_ready(page)
    return page


def _ensure_wrpa_ready(page, timeout_s: float = None) -> bool:
    """轮询等签名器 window.__WRPA__ 就绪（WASM 异步加载）。超时返回 False，由 fetch 报错兜底。"""
    timeout_s = WRPA_WAIT_S if timeout_s is None else timeout_s
    deadline = time.time() + timeout_s
    while True:
        try:
            if page.evaluate("() => typeof window.__WRPA__") == "object":
                return True
        except Exception:
            pass
        if time.time() >= deadline:
            return False
        time.sleep(min(2, max(0.2, deadline - time.time())))


# 页内 fetch：同源带登录 cookie（credentials:'include'，200 的关键）+ __WRPA__ 页面签名。
# 与 scripts/weread_api_probe.py 同源，生产化只加了签名缺失的显式错误上报。
# ⚠️ 2026-09-19 实测两个签名硬约束（违反即 -2041，表象与未登录无法区分）：
#   1. sr() 必须签**完整 URL**（含 origin）——签相对路径只返回一段签名，服务端判无效；
#   2. sr() 返回 Promise<Array>，需 await 后 join(',')。
_FETCH_JS = """async (args) => {
  const [api, maxLen] = args;
  const full = 'https://weread.qq.com' + api;
  try {
    let sig = '';
    try {
      sig = (await window.__WRPA__.sr(full)).join(',');
    } catch (e) {
      return { ok: false, err: 'no_signature:' + e };
    }
    const r = await fetch(full, {
      credentials: 'include',
      headers: { Accept: 'application/json, text/plain, */*', 'x-wrpa-0': sig },
    });
    const t = await r.text();
    return { ok: true, status: r.status, body: t.slice(0, maxLen), len: t.length };
  } catch (e) {
    return { ok: false, err: String(e) };
  }
}"""


class WereadLister:
    """已登录 weread 页面内的列表接口客户端（页内 fetch：cookie + 页面签名自动带上）。

    只封装请求，不管会话生命周期（创建/关闭归调用方）。
    """

    def __init__(self, session):
        self.session = session
        self.page = _find_or_open_weread_page(session)

    def fetch_list_raw(self, book_id: str, offset: int = 0, max_len: int = 2_000_000) -> dict:
        """拉一页列表，返回解析后的 JSON（可能含 errCode）。请求层异常抛 RuntimeError。

        单一请求咽喉：每次调用计入当日配额（WEREAD_DAILY_QUOTA），耗尽抛
        WereadQuotaExhausted 熔断（调用方按场景跳过或停止续批）。
        """
        blocked = weread_block_message()
        if blocked:
            raise WereadQuotaExhausted(blocked)
        quota_record(1)
        api = LIST_API.format(book_id=book_id, offset=offset)
        res = self.page.evaluate(_FETCH_JS, [api, max_len])
        if not isinstance(res, dict) or not res.get("ok"):
            err = (res or {}).get("err", "evaluate 返回空") if isinstance(res, dict) else res
            raise RuntimeError(f"页内 fetch 失败: {err}")
        if res.get("status") != 200:
            raise RuntimeError(f"列表接口 HTTP {res.get('status')}")
        try:
            return json.loads(res.get("body") or "{}")
        except json.JSONDecodeError:
            raise RuntimeError(f"列表响应非 JSON（前 200 字）：{(res.get('body') or '')[:200]}")


# ---------------------------------------------------------------------------
# 登录态判别与扫码续期
# ---------------------------------------------------------------------------

LOGIN_URL = "https://weread.qq.com/#login"
# 登录二维码截图落点（与旧代理源 login_qr.png 同目录同风格，RELOGIN_QR 供上层弹窗）
RELOGIN_QR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weread_login_qr.png")


def has_login_cookie(session) -> bool:
    """weread 域是否存在登录 cookie wr_skey（只看名，不读值）。"""
    try:
        cookies = session.context.cookies("https://weread.qq.com")
        return any(c.get("name") == "wr_skey" for c in cookies)
    except Exception:
        return False


def trigger_weread_relogin(session, timeout: int, qr_path: str = None) -> bool:
    """登录态失效续期：打开登录页 → 截二维码 → 等扫码 → 轮询登录态恢复。

    - 二维码截图落 qr_path 并打印 RELOGIN_QR:<path>（上层会话/用户据此扫码）。
    - 列表请求在页面内发、cookie 由浏览器实时携带：**扫码成功即新 cookie 自动生效**，
      无任何落盘凭据需要更换。
    - 返回 True=登录态已恢复（wr_skey 在且列表接口探活 200）；False=超时/失败。
    """
    qr_path = qr_path or RELOGIN_QR_PATH
    if has_login_cookie(session):
        # wr_skey 仍在：登录没丢，-2041 是人机检测/风控而非过期——扫码无意义
        print("[relogin] 登录 cookie 仍在（wr_skey），-2041 非过期所致；"
              "请走人机检测过码（python scripts/weread_captcha.py --shot）", file=sys.stderr)
        return False
    page = _find_or_open_weread_page(session)
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(4000)
        # SPA 可能停在书架再显示登录面板：有「登录」按钮就点开
        try:
            page.click("text=登录", timeout=3000)
            page.wait_for_timeout(2500)
        except Exception:
            pass
        if detect_captcha(page):
            path = screenshot_captcha(page)
            print(f"[relogin] 登录页出现安全检测/验证码，已截图 {path or '?'}；"
                  f"请先过码（python scripts/weread_captcha.py --grid …）再重跑", file=sys.stderr)
            return False
        os.makedirs(os.path.dirname(qr_path), exist_ok=True)
        page.screenshot(path=qr_path)
        print(f"RELOGIN_QR:{qr_path}", file=sys.stderr)
        print(f"[relogin] 二维码已截图（{qr_path}），请用微信扫码登录微信读书"
              f"（最长等 {timeout}s）…", file=sys.stderr)
    except Exception as e:
        print(f"[relogin] 打开登录页失败: {e}", file=sys.stderr)
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        if not has_login_cookie(session):
            continue  # wr_skey 尚未出现
        # cookie 已出现：打 1 次列表探活确认服务端会话有效（不额外消耗业务配额）
        try:
            lister = WereadLister(session)
            resp = lister.fetch_list_raw(BOOK_ID_FALLBACK["哥飞"], 0)
            if classify_list_response(resp) == "ok":
                print("[relogin] ✅ 扫码成功，登录态已恢复", file=sys.stderr)
                return True
        except WereadQuotaExhausted:
            print(f"[relogin] {quota_block_message()}（探活未执行）", file=sys.stderr)
            return False
        except Exception:
            continue
    print(f"[relogin] ⏰ 等待扫码超时（{timeout}s）", file=sys.stderr)
    return False


def _window_cutoff(state, book_id: str, is_first: bool) -> int:
    """增量时间窗起点（epoch 秒）。WEREAD_WINDOW_DAYS=0 关闭（返回 0=不过滤）。

    断跑自动补齐：eff = max(基础窗, 距上次成功运行天数+1)，封顶 WEREAD_MAX_WINDOW_DAYS。
    """
    if WEREAD_WINDOW_DAYS <= 0:
        return 0
    if is_first:
        eff = WEREAD_WINDOW_DAYS
    else:
        last = state.get("sources", {}).get(_source_key(book_id), {}).get("last_check", 0)
        eff = effective_window_days(WEREAD_WINDOW_DAYS, last, WEREAD_MAX_WINDOW_DAYS)
    return int(time.time() - eff * 86400)


# ---------------------------------------------------------------------------
# 一轮发现
# ---------------------------------------------------------------------------

def _process_one_account(lister, state, e: dict, cutoff: int, max_pages: int,
                         baseline_only: bool) -> tuple:
    """单号列表→去重→条目。返回 (items, rids_fetched, result_dict)。

    result_dict: {"status": "ok"|"auth"|"captcha"|"fetch_err"|"unknown_err",
                  "detail": str, "new_n": int, "is_first": bool}
    """
    name, book_id = e["name"], e["book_id"]
    try:
        reviews, cat, last_resp = paginate(lister.fetch_list_raw, book_id,
                                           max_pages=max_pages, cutoff_ts=cutoff)
    except WereadQuotaExhausted as ex:
        return [], [], {"status": "quota", "detail": str(ex),
                        "new_n": 0, "is_first": False}
    except RuntimeError as ex:
        return [], [], {"status": "fetch_err", "detail": str(ex)[:160],
                        "new_n": 0, "is_first": False}
    if detect_captcha(lister.page):
        path = screenshot_captcha(lister.page)
        return [], [], {"status": "captcha",
                        "detail": f"列表请求后页面出现安全检测/验证码，已截图 {path or '(截图失败)'}",
                        "new_n": 0, "is_first": False}
    if cat != "ok":
        code = last_resp.get("errCode") if isinstance(last_resp, dict) else "?"
        return [], [], {"status": cat, "detail": f"errCode={code}",
                        "new_n": 0, "is_first": False}
    seen = get_seen(state, _source_key(book_id))
    rids = [r.get("reviewId", "") for r in reviews if r.get("reviewId")]
    is_first = not seen
    if is_first or baseline_only:
        new_reviews = []
    else:
        new_reviews = [r for r in reviews if r.get("reviewId") not in seen]
    items, new_n, unreachable_rids, unreachable_titles = [], 0, set(), []
    for r in new_reviews:
        if cutoff and int(r.get("createTime", 0) or 0) < cutoff:
            continue  # 页边界骑跨窗口起点的兜底过滤
        token = extract_token(r.get("reviewId", ""))
        if not _MP_TOKEN_RE.match(token):
            # 非 /s/ 型 token（含 ~ 等）：mp 直链不可达（实测「参数错误」），
            # 不入队、不标 seen——下轮继续出现在健康度行，直到用户反馈处置
            info = r.get("mpInfo") or {}
            unreachable_rids.add(r.get("reviewId", ""))
            unreachable_titles.append(f"{info.get('title', '')[:30]}({r.get('reviewId', '')})")
            continue
        it = review_to_item(r, name, e.get("category", ""))
        if it:
            items.append(it)
            new_n += 1
    # 本页 reviewId 进 seen（含首跑基线），直链不可达条目除外（不标 seen，每轮重新暴露）
    mark_seen(state, _source_key(book_id),
              [rid for rid in rids if rid not in unreachable_rids],
              last_check=int(time.time()))
    return items, rids, {"status": "ok", "detail": "", "new_n": new_n,
                         "is_first": is_first, "unreachable": unreachable_titles}


def discover_weread(state: dict, entries: list, session=None,
                    pages: int = None) -> tuple:
    """weread 直连源一轮发现。返回 (new_items, health)。

    - session=None：自建 SharedCdpSession 并在结束时关闭（预览/单跑模式）；
      传入共享会话（run.py --apply 轮次）：只使用不关闭（one-kill 归调用方）。
    - state：调用方持有并负责落盘时机（与 run.py「仅 --apply 落盘 seen」守卫一致，
      本函数只内存内变更，预览不落盘）。
    - 时间窗：只抓窗口内发布的文章（翻页翻到窗口起点即停）；首跑只建基线不总结。
    - 登录失效（-2041 等）：自动打开登录页截二维码等扫码（WEREAD_AUTO_RELOGIN，
      扫到即继续抓该号及后续号；二维码落 monitors/weread_login_qr.png）；
      页面出现验证码 → 停手上报（过码走 scripts/weread_captcha.py，识别需模型在场）。
    """
    _blocked = weread_block_message()
    if _blocked:
        # 熔断（多源场景）：公众号源直接跳过，B站/scys 照跑，不建会话不杀 Chrome
        health = {"ok": 0, "new": 0, "captcha": False,
                  "errors": [("all", "blocked", _blocked)],
                  "skipped": [e["name"] for e in entries], "baseline": [],
                  "relogin": False, "quota_exhausted": True}
        return [], health
    own_session = session is None
    if own_session:
        from shared.cdp_session import SharedCdpSession
        session = SharedCdpSession()
    health = {"ok": 0, "new": 0, "captcha": False, "errors": [], "skipped": [],
              "baseline": [], "relogin": False, "quota_exhausted": False}
    items: list = []
    try:
        lister = WereadLister(session)
        if detect_captcha(lister.page):
            path = screenshot_captcha(lister.page)
            health["captcha"] = True
            health["errors"].append(("page", "captcha", f"页面出现安全检测/验证码，已截图 {path or '(截图失败)'}"))
            health["skipped"] = [e["name"] for e in entries]
            return items, health
        stop = False
        relogin_used = False  # 每轮最多自动弹码一次，避免死循环扫码
        for e in entries:
            name, book_id = e["name"], e["book_id"]
            if stop:
                health["skipped"].append(name)
                continue
            is_first = not get_seen(state, _source_key(book_id))
            cutoff = _window_cutoff(state, book_id, is_first)
            its, rids, res = _process_one_account(lister, state, e, cutoff,
                                                  WEREAD_MAX_PAGES, baseline_only=False)
            if res["status"] in ("auth", "unknown_err") and not relogin_used \
                    and WEREAD_AUTO_RELOGIN and WEREAD_RELOGIN_WAIT > 0 \
                    and not has_login_cookie(session):
                # 登录 cookie 缺失 = 真过期 → 自动弹码等扫码（页面内 fetch 的 cookie
                # 由浏览器实时携带，扫码成功即自动生效）。
                # wr_skey 仍在 → 大概率人机检测（风控），不走扫码（白等），
                # 落到下方停手上报，由会话内模型过码。
                if trigger_weread_relogin(session, WEREAD_RELOGIN_WAIT):
                    health["relogin"] = True
                    relogin_used = True
                    its, rids, res = _process_one_account(
                        lister, state, e, cutoff, WEREAD_MAX_PAGES, baseline_only=False)
            if res["status"] != "ok":
                if res["status"] == "captcha":
                    health["captcha"] = True
                if res["status"] == "quota":
                    health["quota_exhausted"] = True
                detail = res["detail"]
                if res["status"] in ("auth", "unknown_err"):
                    detail += f"（若为安全检测，过码：python scripts/weread_captcha.py --shot）"
                health["errors"].append((name, res["status"], detail))
                stop = True  # 处置不了：停手（剩余号跳过），绝不硬闯
                continue
            items.extend(its)
            health["ok"] += 1
            health.setdefault("unreachable", []).extend(res.get("unreachable") or [])
            if res["is_first"]:
                health["baseline"].append(name)
            else:
                health["new"] += res["new_n"]
            time.sleep(LIST_GAP + random.uniform(0, 1))  # 号间退避；最后一号多睡一次无害
    finally:
        if own_session:
            try:
                session.close()
            except Exception:
                pass
    return items, health


def discover_weread_backfill(state: dict, entries: list, since_ts: int,
                             max_pages: int = 20, session=None) -> tuple:
    """weread 历史补全（「补到什么时候」）：从最新往老翻到 since_ts 为止，
    窗口内未抓过的全部入队。返回 (new_items, health)。

    - 与日常增量同一条入队管线（route=article → apply_summaries 抓正文入队列）。
    - 翻到 since_ts 或 max_pages 截断；截断未到 since_ts 时 health 记 reached_since=False，
      再跑一次本命令即续（seen 去重保证不重复入队）。
    - weread 请求量 = 每号翻的页数（每页 20 条、间隔 ≥2s）；补一年 ≈ 每号 15~25 页，
      建议分多次跑或接受一次较多请求（一次性任务，非日常节奏）。
    - 扫码续期与日常增量同款（每轮最多弹码一次）。
    """
    _blocked = weread_block_message()
    if _blocked:
        # 熔断（补全场景）：任务消息直接返回，已入队进度靠 seen 保留，明天续批
        health = {"ok": 0, "new": 0, "captcha": False,
                  "errors": [("all", "blocked", _blocked)],
                  "skipped": [], "relogin": False, "reached_since": False, "pages": {},
                  "quota_exhausted": True}
        return [], health
    own_session = session is None
    if own_session:
        from shared.cdp_session import SharedCdpSession
        session = SharedCdpSession()
    health = {"ok": 0, "new": 0, "captcha": False, "errors": [], "skipped": [],
              "relogin": False, "reached_since": True, "pages": {},
              "quota_exhausted": False}
    items: list = []
    try:
        lister = WereadLister(session)
        stop = False
        relogin_used = False
        for e in entries:
            name, book_id = e["name"], e["book_id"]
            if stop:
                health["skipped"].append(name)
                continue
            offset = 0
            n_pages = 0
            its_all: list = []
            oldest = None
            while n_pages < max_pages:
                fetch = lambda b, o, _off=offset: lister.fetch_list_raw(b, o + _off)
                try:
                    reviews, cat, last_resp = paginate(fetch, book_id, max_pages=1)
                except WereadQuotaExhausted:
                    health["quota_exhausted"] = True
                    health["reached_since"] = False  # 配额熔断：明天再跑一次即续批
                    stop = True
                    break
                except RuntimeError as ex:
                    health["errors"].append((name, "fetch_err", str(ex)[:160]))
                    stop = True
                    break
                if detect_captcha(lister.page):
                    path = screenshot_captcha(lister.page)
                    health["captcha"] = True
                    health["errors"].append((name, "captcha",
                                             f"页面出现安全检测/验证码，已截图 {path or '?'}"))
                    stop = True
                    break
                if cat != "ok":
                    code = last_resp.get("errCode") if isinstance(last_resp, dict) else "?"
                    if cat in ("auth", "unknown_err") and not relogin_used \
                            and WEREAD_AUTO_RELOGIN and WEREAD_RELOGIN_WAIT > 0:
                        if trigger_weread_relogin(session, WEREAD_RELOGIN_WAIT):
                            health["relogin"] = True
                            relogin_used = True
                            continue  # 同一 offset 重试（扫码后 cookie 自动生效）
                    health["errors"].append((name, cat, f"errCode={code}"))
                    stop = True
                    break
                if not reviews:
                    break  # 翻到底
                n_pages += 1
                offset += len(reviews)
                seen = get_seen(state, _source_key(book_id))
                in_range = [r for r in reviews
                            if r.get("reviewId") not in seen
                            and int(r.get("createTime", 0) or 0) >= since_ts]
                for r in in_range:
                    token = extract_token(r.get("reviewId", ""))
                    if not _MP_TOKEN_RE.match(token):
                        continue  # ~token 直链不可达，跳过（不标 seen，待用户反馈）
                    it = review_to_item(r, name, e.get("category", ""))
                    if it:
                        its_all.append(it)
                # 本批全部 reviewId 标 seen（含早于 since 的，防反复重扫）；~token 除外
                mark_seen(state, _source_key(book_id),
                          [r.get("reviewId") for r in reviews if r.get("reviewId")
                           and _MP_TOKEN_RE.match(extract_token(r.get("reviewId", "")))],
                          last_check=int(time.time()))
                oldest = min(int(r.get("createTime", 0) or 0) for r in reviews)
                if oldest < since_ts:
                    break  # 已翻过 since 起点，该号补全完成
                time.sleep(LIST_GAP + random.uniform(0, 1))
            items.extend(its_all)
            if not stop or its_all:
                health["ok"] += 1
                health["new"] += len(its_all)
            health["pages"][name] = n_pages
            if n_pages >= max_pages and oldest is not None and oldest >= since_ts:
                health["reached_since"] = False  # 截断未到 since，再跑一次续
    finally:
        if own_session:
            try:
                session.close()
            except Exception:
                pass
    return items, health


def _source_key(book_id: str) -> str:
    return f"weread:{book_id}"
