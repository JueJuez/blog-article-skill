"""monitors/weread.py — 微信读书直连公众号源（PLAN-20260919，wewe-rss 死代理的接替方案）。

事实依据全部来自 2026-09-18/19 登录态实测（references/weread-direct-source.md，勿凭记忆改）：
  - 列表 GET /web/mp/articles?bookId=&offset=N：登录态 200；未登录/安全检测 → {"errCode":-2041}
  - 签名：页面内 window.__WRPA__.sr(api).join(',') 作 x-wrpa-0 头；登录 cookie 是真正门槛
  - 翻页：每页固定 20 条，offset += len(reviews)，空列表即到底（50 步长会漏页，禁用）
  - reviewId = MP_WXS_<bid>_<token>，末段即 mp.weixin.qq.com/s/<token> 原文直链

生产纪律（红线，内置非可选）：
  - 列表只在页面内 fetch（同源带登录 cookie + 页面签名）；requests 直调仅限免签名接口
  - 每号每天 1 次列表（默认 offset=0 一页 20 条，足够日增量检测）；翻页仅手动补齐任务用
  - 列表间隔 ≥2s + 抖动；单日十几请求封顶；个人单账号低频自用，不做号池
  - cookie 策略（用户定策）：不猜、不自动刷新。任何登录态/风控类错误码或页面出现
    验证码 → 立即停手 + 截图 + 原样记录，剩余号本轮跳过，报给用户等反馈
  - 正文不在此模块抓：条目与旧代理源同形（route=article）→ 既有 apply_summaries
    管线走 mp.weixin.qq.com 直链抓正文（风险隔离：weread 每号每天只 1 次列表请求）

开关：WEREAD_SOURCE_ENABLED（run.py 读，默认 0）。订阅名单复用 subscriptions.json 的
wechat 列表，bookId 映射独立维护（BOOK_ID_FALLBACK + monitors/.mp_cache.json 反查）。
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time

from monitors.state import get_seen, mark_seen

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


def paginate(fetch_page, book_id: str, max_pages: int = 1):
    """翻页累加器（纯逻辑，fetch_page 由调用方注入）。

    offset += len(reviews)（实测每页固定 20 条、页间无缝衔接；issue #442 的 50 步长
    实测会跳过每页之间约 30 条，禁用）。空列表即到底；中途遇 auth/unknown_err
    立即中断上报。返回 (reviews, end_category, last_resp)。
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
        """拉一页列表，返回解析后的 JSON（可能含 errCode）。请求层异常抛 RuntimeError。"""
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
# 一轮发现
# ---------------------------------------------------------------------------

def discover_weread(state: dict, entries: list, session=None,
                    pages: int = None) -> tuple:
    """weread 直连源一轮发现。返回 (new_items, health)。

    - session=None：自建 SharedCdpSession 并在结束时关闭（预览/单跑模式）；
      传入共享会话（run.py --apply 轮次）：只使用不关闭（one-kill 归调用方）。
    - state：调用方持有并负责落盘时机（与 run.py「仅 --apply 落盘 seen」守卫一致，
      本函数只内存内变更，预览不落盘）。
    - cookie 策略：不猜、不自动刷新。任一号返回登录态/风控类错误码、或页面出现
      验证码 → 立即停手（剩余号记 skipped），截图 + 原样记录错误码报给用户。
    - 首跑语义（PLAN §5）：seen 为空的号只把本页 reviewId 写入 seen 建立基线，
      不回填总结历史。
    """
    own_session = session is None
    if own_session:
        from shared.cdp_session import SharedCdpSession
        session = SharedCdpSession()
    health = {"ok": 0, "new": 0, "captcha": False, "errors": [], "skipped": [], "baseline": []}
    items: list = []
    pages = pages or DAILY_PAGES
    try:
        lister = WereadLister(session)
        if detect_captcha(lister.page):
            path = screenshot_captcha(lister.page)
            health["captcha"] = True
            health["errors"].append(("page", "captcha", f"页面出现安全检测/验证码，已截图 {path or '(截图失败)'}"))
            health["skipped"] = [e["name"] for e in entries]
            return items, health
        stop = False
        for e in entries:
            name, book_id = e["name"], e["book_id"]
            if stop:
                health["skipped"].append(name)
                continue
            try:
                reviews, cat, last_resp = paginate(lister.fetch_list_raw, book_id, max_pages=pages)
            except RuntimeError as ex:
                health["errors"].append((name, "fetch_err", str(ex)[:160]))
                stop = True  # 请求层异常（签名缺失/非 200/非 JSON）：不硬闯，剩余号跳过
                continue
            if detect_captcha(lister.page):
                path = screenshot_captcha(lister.page)
                health["captcha"] = True
                health["errors"].append((name, "captcha", f"列表请求后页面出现安全检测/验证码，已截图 {path or '(截图失败)'}"))
                stop = True
                continue
            if cat != "ok":
                code = last_resp.get("errCode") if isinstance(last_resp, dict) else "?"
                health["errors"].append((name, cat, f"errCode={code}（若为安全检测，"
                                         f"过码：python scripts/weread_captcha.py --shot）"))
                stop = True  # 登录态/风控类错误：原样上报，等用户反馈，不自动处置
                continue
            seen = get_seen(state, _source_key(book_id))
            rids = [r.get("reviewId", "") for r in reviews if r.get("reviewId")]
            is_first = not seen
            new_reviews = [] if is_first else \
                [r for r in reviews if r.get("reviewId") not in seen]
            new_n = 0
            unreachable_rids = set()
            for r in new_reviews:
                token = extract_token(r.get("reviewId", ""))
                if not _MP_TOKEN_RE.match(token):
                    # 非 /s/ 型 token（含 ~ 等）：mp 直链不可达（实测「参数错误」），
                    # 不入队、不标 seen——下轮继续出现在健康度行，直到用户反馈处置
                    info = r.get("mpInfo") or {}
                    unreachable_rids.add(r.get("reviewId", ""))
                    health.setdefault("unreachable", []).append(
                        f"{info.get('title', '')[:30]}({r.get('reviewId', '')})")
                    continue
                it = review_to_item(r, name, e.get("category", ""))
                if it:
                    items.append(it)
                    new_n += 1
            # 本页 reviewId 进 seen（含首跑基线），直链不可达条目除外（不标 seen，每轮重新暴露）
            mark_seen(state, _source_key(book_id),
                      [rid for rid in rids if rid not in unreachable_rids],
                      last_check=int(time.time()))
            health["ok"] += 1
            if is_first:
                health["baseline"].append(name)
            else:
                health["new"] += new_n
            time.sleep(LIST_GAP + random.uniform(0, 1))  # 号间退避；最后一号多睡一次无害
    finally:
        if own_session:
            try:
                session.close()
            except Exception:
                pass
    return items, health


def _source_key(book_id: str) -> str:
    return f"weread:{book_id}"
