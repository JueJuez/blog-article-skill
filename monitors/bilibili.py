"""monitors/bilibili.py — B站UP主源（方案A：带登录 Cookie）。

经 B站官方 API + WBI 签名获取 UP主 的「视频」与「动态」，做增量去重。
不依赖任何第三方（RSSHub 公共实例已普遍被墙）。

- 视频：x/space/wbi/arc/search（WBI 签名）
- 动态：x/polymer/web-dynamic/v1/feed/space（WBI 签名）
- 必须带登录 Cookie（BILI_COOKIE）：动态接口硬性要求登录态，否则 -352；
  视频列表游客态偶发 -352，带 Cookie 后稳定。
- 必须带 dm_img_* WebGL 风控指纹：否则 -352（B站新风控）。
- 频率控制：同源视频/动态之间退避(_INTRA_GAP)；跨源退避(_SOURCE_GAP)；重试退避(_RETRY_BACKOFF)。
- 充电专属视频(is_charging_arc)照常出现在列表，标记 is_charging；apply 时跳过正文抓取
  （付费内容抓不到 transcript，仅监控「发过」，不浪费 API）。
- 抓取策略：按内容发布时间做「时间窗口」过滤（首跑 BILI_FIRST_WINDOW_DAYS=30 天 / 每日
  BILI_DAILY_WINDOW_DAYS=1 天），单页拉满 BILI_PAGE_SIZE=50；无正文/无干货的动态（系统通知、
  充电问答回复等）直接屏蔽；每条内容带原始发布时间(publish_time)落盘，笔记自动标新鲜度标签。
- 自动补齐（2026-07-28 新增）：每日窗口不再是死值——`auto` 非首次运行时按「距上次成功运行的天数
  + 缓冲」动态拉长窗口（封顶 BILI_MAX_WINDOW_DAYS=30），断跑数日再跑也能抓回中间漏掉的内容；
  平时每日按时跑 gap≈1 天，窗口依旧 1 天，行为不变。由 state.json 的 per-source last_check 驱动，
  seen 去重保证多跑/漏跑都不会重复总结。

动态去重说明：DYNAMIC_TYPE_AV（视频转发）/ DYNAMIC_TYPE_ARTICLE（专栏转发）
已在 video/cv 路由覆盖，动态抓取默认跳过这两类，避免重复处理。
"""
import hashlib
import os
import re
import sys
import time
import urllib.parse
from typing import Dict, List, Optional

import requests

from .state import get_seen, mark_seen, effective_window_days

_BILI = "https://api.bilibili.com"

# 完整浏览器头（贴近真实浏览器，避免被风控判定非浏览器 -> -352/4101129）
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")
_BROWSER_HEADERS = {
    "User-Agent": _UA,
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7,ja;q=0.6",
    "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "priority": "u=1, i",
}

# WebGL 风控指纹（取自真实浏览器请求，非占位空值；空值会触发动态接口 4101129）
_DM_IMG_LIST = ('[{"x":2182,"y":686,"z":0,"timestamp":152,"k":64,"type":0},'
                '{"x":2300,"y":43,"z":101,"timestamp":786,"k":64,"type":0},'
                '{"x":2441,"y":989,"z":58,"timestamp":889,"k":103,"type":0},'
                '{"x":2510,"y":1164,"z":200,"timestamp":1030,"k":97,"type":0}]')
_DM_IMG_STR = "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ"
_DM_COVER_IMG_STR = ("QU5HTEUgKE5WSURJQSwgTlZJRElBIEdlRm9yY2UgUlRYIDQwNjAg"
                     "TGFwdG9wIEdQVSAoMHgwMDAwMjhFMCkgRGlyZWN0M0QxMSB2c181XzAg"
                     "cHNfNV8wLCBEM0QxMSlHb29nbGUgSW5jLiAoTlZJRElBKQ")
_DM_IMG_INTER = '{"ds":[{"t":2,"c":"","p":[75,25,25],"s":[482,6651,4476]}],"wh":[4255,6100,91],"of":[298,596,298]}'

# 动态接口专属：设备请求 JSON（真实浏览器必带，含 spmid，需参与 WBI 签名）
_X_BILI_DEVICE_REQ_JSON = '{"platform":"web","device":"pc","spmid":"333.1387"}'

# 动态 feed/space 的 features 参数（真实浏览器完整列表）
_FEATURES = ("itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote,forwardListHidden,"
             "decorationCard,commentsNewVersion,onlyfansAssetsV2,ugcDelete,onlyfansQaCard,"
             "avatarAutoTheme,sunflowerStyle,cardsEnhance,eva3CardOpus,eva3CardVideo,"
             "eva3CardComment,eva3CardUser")

# 频率控制（均为秒，均可通过环境变量覆盖）
# 注意：B站风控看的是「请求频率」而非「抓取条数」——放慢跨源间隔 + 抖动比限制条数更有效。
_SOURCE_GAP = float(os.environ.get("BILI_GAP", "30"))      # 跨源退避（默认 30s，run.py 另加 ±5s 抖动）
_INTRA_GAP = float(os.environ.get("BILI_INTRA_GAP", "2"))  # 同源视频/动态间退避
_RETRY_BACKOFF = float(os.environ.get("BILI_BACKOFF", "5"))  # 重试退避基数

# 时间窗口抓取（替代纯 count cap）：只处理 N 天内发布的 content，不论条数。
# 首跑回填窗口较大（默认 30 天），每日增量窗口较小（默认 1 天 = 当天）。
_BILI_FIRST_WINDOW_DAYS = float(os.environ.get("BILI_FIRST_WINDOW_DAYS", "30"))
_BILI_DAILY_WINDOW_DAYS = float(os.environ.get("BILI_DAILY_WINDOW_DAYS", "1"))
# 单次拉取页数上限（B站接口单页上限约 50）：拉满以覆盖整个时间窗口。
_BILI_PAGE_SIZE = int(os.environ.get("BILI_PAGE_SIZE", "50"))
# 每类型安全上限（防止极端 UP 单窗口内发几百条把笔记刷爆；正常由窗口+页数约束）。
_BILI_SAFETY_CAP = int(os.environ.get("BILI_SAFETY_CAP", "50"))

# 可选代理：走用户本机代理绕过沙箱出口 IP 风控。格式 http://host:port 或 socks5://host:port
_PROXY = os.environ.get("BILI_PROXY", "").strip()
_PROXIES = {"http": _PROXY, "https": _PROXY} if _PROXY else {}

# 登录 Cookie（方案A）。缺失则降级游客态（动态基本不可用，视频偶发 -352）。
_COOKIE = (os.environ.get("BILI_COOKIE") or "").strip()
_cookie_warned = False

_DYNAMIC_SKIP_TYPES = {"DYNAMIC_TYPE_AV", "DYNAMIC_TYPE_ARTICLE"}

# 无干货 / 系统自动生成的通知类动态（看不到正文、无可总结内容），discover 时屏蔽。
# 例：「我回复了@xxx的充电专属问答，快来围观吧～」——B站自动生成的回复通知，非 UP主原创干货。
_DYNAMIC_NOISE_PATTERNS = (
    "充电专属问答", "我回复了@", "快来围观吧", "为我充电", "充电专属",
)
_DYNAMIC_MIN_BODY = 15  # 去掉链接后正文仍少于此长度，视为空壳/无干货


def _dynamic_is_substantive(text: str) -> bool:
    """判断动态是否有可读干货（供监控/总结），过滤系统通知与空壳动态。

    - 去掉链接后正文过短 -> 无干货
    - 命中系统通知模板（回复/充电问答/围观等）-> 无干货
    """
    t = (text or "").strip()
    if len(t) < _DYNAMIC_MIN_BODY:
        return False
    body = re.sub(r"https?://\S+", "", t).strip()
    if len(body) < _DYNAMIC_MIN_BODY:
        return False
    if any(p in t for p in _DYNAMIC_NOISE_PATTERNS):
        return False
    return True

# 进程级复用 session
_SESSION = None


def _get_session() -> requests.Session:
    global _SESSION, _cookie_warned
    if _SESSION is None:
        s = requests.Session()
        s.headers.update(_BROWSER_HEADERS)
        if _COOKIE:
            s.headers["Cookie"] = _COOKIE
        else:
            if not _cookie_warned:
                print("[bili-warn] 未设置 BILI_COOKIE，降级游客态：动态接口大概率 -352，"
                      "视频列表偶发风控。建议把浏览器 Cookie 填入 .env 的 BILI_COOKIE。",
                      file=sys.stderr)
                _cookie_warned = True
        if _PROXIES:
            s.proxies.update(_PROXIES)
        # 主页预热：建立 buvid3 等设备信任（带 Cookie 时同步写入设备指纹）
        try:
            s.get("https://www.bilibili.com", timeout=10)
        except Exception:
            pass
        _SESSION = s
    return _SESSION


# B站官方 WBI 混淆表
_ENC = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
        33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
        61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
        36, 20, 34, 44, 52]


class _Wbi:
    """WBI 签名助手，进程内缓存 mixin_key（1 小时）。"""
    _cache: Dict[str, float] = {}

    @classmethod
    def mixin_key(cls) -> str:
        now = time.time()
        if cls._cache and now - next(iter(cls._cache.values())) < 3600:
            return next(iter(cls._cache.keys()))
        r = _get_session().get(f"{_BILI}/x/web-interface/nav", timeout=12)
        d = r.json().get("data", {}).get("wbi_img", {})
        img = d["img_url"].split("/")[-1].split(".")[0]
        sub = d["sub_url"].split("/")[-1].split(".")[0]
        mk = "".join((img + sub)[i] for i in _ENC)[:32]
        cls._cache = {mk: now}
        return mk

    @classmethod
    def sign(cls, params: Dict, with_device_json: bool = False) -> Dict:
        mk = cls.mixin_key()
        params = dict(params)
        params["wts"] = int(time.time())
        # 风控指纹参数必须参与签名（否则 w_rid 与服务器不一致 -> -352/4101129）
        params["dm_img_list"] = _DM_IMG_LIST
        params["dm_img_str"] = _DM_IMG_STR
        params["dm_cover_img_str"] = _DM_COVER_IMG_STR
        params["dm_img_inter"] = _DM_IMG_INTER
        if with_device_json:
            # 动态接口专属：真实浏览器必带的设备 JSON，需参与签名
            params["x-bili-device-req-json"] = _X_BILI_DEVICE_REQ_JSON
        params = dict(sorted(params.items()))
        q = urllib.parse.urlencode(params)
        params["w_rid"] = hashlib.md5((q + mk).encode()).hexdigest()
        return params


def _req_headers(uid: str, dynamic: bool = False) -> Dict:
    path = f"/{uid}/dynamic" if dynamic else f"/{uid}"
    return {
        "origin": "https://space.bilibili.com",
        "referer": f"https://space.bilibili.com{path}",
    }


def _fetch_vlist_page(uid: str, pn: int, ps: int, max_retry: int = 2):
    """返回单页视频 vlist；失败返回 ([], err_msg)。不可见(-404/-403)返回 ([], None) 表示停止分页。"""
    last_err = None
    last_resp = None
    for attempt in range(max_retry + 1):
        try:
            s = _get_session()
            p = _Wbi.sign({
                "mid": uid, "ps": ps, "pn": pn, "order": "pubdate",
                "web_location": "333.1387", "order_avoided": 1,
            })
            url = f"{_BILI}/x/space/wbi/arc/search?" + urllib.parse.urlencode(p)
            r = s.get(url, headers=_req_headers(uid), timeout=12)
            last_resp = r
            try:
                j = r.json()
            except Exception:
                # 返回空/HTML（网关拦截/风控页），非 JSON
                last_err = f"JSONDecodeError status={r.status_code} body={r.text[:300]!r}"
                time.sleep(_RETRY_BACKOFF * (attempt + 1))
                continue
            if j.get("code") == 0:
                return j.get("data", {}).get("list", {}).get("vlist", []), None
            code = j.get("code")
            last_err = f"code={code} msg={j.get('message')}"
            if code in (-404, -403):
                # 稿件不可见/无权限（粉丝可见等）：直接跳过，重试无意义
                print(f"[bili-skip] uid={uid} 视频不可见(code={code})，跳过", file=sys.stderr)
                return [], None
            if code == -352:
                time.sleep(_RETRY_BACKOFF * (attempt + 1))
                continue
            return [], last_err
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(_RETRY_BACKOFF * (attempt + 1))
    # 重试耗尽：打印调试信息（响应头/前 300 字节），返回空列表，不影响其他 UP
    if last_resp is not None:
        print(f"[bili-fail] uid={uid} 视频拉取失败: {last_err} | "
              f"status={last_resp.status_code} body={last_resp.text[:300]!r}", file=sys.stderr)
    else:
        print(f"[bili-fail] uid={uid} 视频拉取失败: {last_err}", file=sys.stderr)
    return [], last_err


def _fetch_vlist(uid: str, ps: int = 10, max_retry: int = 2, paginate: bool = False) -> List[Dict]:
    """返回视频 vlist。paginate=True 时翻页直到空页/不足一页（用于「抓全部视频」）。

    失败处理：单页失败且非不可见 → 返回 ([], err)；首页即失败向上返回 []（不影响其他 UP），
    后续页失败则保留已累积部分并停止分页。正常单页路径（paginate=False）行为与原实现一致。
    """
    out: List[Dict] = []
    pn = 1
    while True:
        page, err = _fetch_vlist_page(uid, pn, ps, max_retry)
        if not page:
            if pn == 1:
                # 首页失败/为空：向上返回空（与旧行为一致，不影响其他 UP）
                return []
            break
        out.extend(page)
        if not paginate:
            break
        if len(page) < ps:
            break
        pn += 1
        time.sleep(_INTRA_GAP)
    return out


def _normalize_dynamics(items: List[Dict]) -> List[Dict]:
    """把 B站动态原始 items 标准化，并跳过视频/专栏转发与无文本动态。

    返回可总结的动态列表：{id, type, author, pub_ts, text}。

    注意：真实返回里正文并不在 module_dynamic.desc.text（该字段常为 null），
    而是在 module_dynamic.major.opus.summary.text（图文/专栏）或
    major.archive.title（视频转发）——需按 major.type 取值。
    """
    out: List[Dict] = []
    for it in items or []:
        dtype = it.get("type", "")
        if dtype in _DYNAMIC_SKIP_TYPES:
            continue  # 视频/专栏转发已由 video/cv 覆盖
        mods = it.get("modules", {}) or {}
        author_mod = mods.get("module_author") or {}
        author = author_mod.get("name", "")
        try:
            pub_ts = int(author_mod.get("pub_ts", 0) or 0)
        except (TypeError, ValueError):
            pub_ts = 0
        dyn_mod = mods.get("module_dynamic") or {}
        # 1) 纯文字动态：desc.text
        text = ""
        desc = dyn_mod.get("desc") or {}
        if isinstance(desc, dict):
            text = (desc.get("text") or "").strip()
        # 2) 图文/专栏动态：major.opus.summary.text（+ title）
        if not text:
            major = dyn_mod.get("major") or {}
            mtype = major.get("type", "")
            if mtype == "MAJOR_TYPE_OPUS":
                opus = major.get("opus") or {}
                summary = opus.get("summary") or {}
                stext = (summary.get("text") or "").strip()
                title = (opus.get("title") or "").strip()
                text = (title + "\n" + stext).strip()
            elif mtype == "MAJOR_TYPE_ARCHIVE":
                arch = major.get("archive") or {}
                text = (arch.get("title") or "").strip()
        text = text.strip()
        if not text:
            continue  # 无文本动态（如纯图无文案/充电专属被屏蔽）无可总结内容，跳过
        out.append({
            "id": str(it.get("id_str") or it.get("id") or ""),
            "type": dtype,
            "author": author,
            "pub_ts": pub_ts,
            "text": text,
        })
    return out


def _fetch_dynamics(uid: str, ps: int = 10, max_retry: int = 2) -> List[Dict]:
    """获取 UP主 最近动态（文本/图文类）。返回标准化 dict 列表。

    跳过 DYNAMIC_TYPE_AV / DYNAMIC_TYPE_ARTICLE（已由 video/cv 覆盖），
    仅保留 WORD（纯文字）/ DRAW（图文）等带文本的动态。
    失败（风控/不可见/网络）时优雅返回 [] 并打印调试信息，不中断其他 UP。
    """
    last_err = None
    last_resp = None
    for attempt in range(max_retry + 1):
        try:
            s = _get_session()
            p = _Wbi.sign({
                "host_mid": uid,
                "offset": "",
                "timezone_offset": -480,
                "platform": "web",
                "features": _FEATURES,
                "web_location": "333.1387",
            }, with_device_json=True)
            url = f"{_BILI}/x/polymer/web-dynamic/v1/feed/space?" + urllib.parse.urlencode(p)
            r = s.get(url, headers=_req_headers(uid, dynamic=True), timeout=12)
            last_resp = r
            try:
                j = r.json()
            except Exception:
                last_err = f"JSONDecodeError status={r.status_code} body={r.text[:300]!r}"
                time.sleep(_RETRY_BACKOFF * (attempt + 1))
                continue
            code = j.get("code")
            if code != 0:
                last_err = f"code={code} msg={j.get('message')}"
                if code in (-404, -403):
                    # 动态不可见（粉丝可见/充电专属等）：直接跳过
                    print(f"[bili-skip] uid={uid} 动态不可见(code={code})，跳过", file=sys.stderr)
                    return []
                if code in (-352, 4101129, 4101133):
                    # 风控/动态接口偶发校验失败（"运气成分"）：退避重试
                    print(f"[bili-retry] uid={uid} 动态 code={code} "
                          f"({j.get('message')})，第 {attempt+1} 次重试", file=sys.stderr)
                    time.sleep(_RETRY_BACKOFF * (attempt + 1))
                    continue
                # 其他非预期错误码：打印出来，避免静默吞掉
                print(f"[bili-dyn-err] uid={uid} 动态未预期 code={code} "
                      f"msg={j.get('message')}", file=sys.stderr)
                return []
            items = (j.get("data") or {}).get("items") or []
            return _normalize_dynamics(items)
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(_RETRY_BACKOFF * (attempt + 1))
    # 重试耗尽：打印调试信息，返回空列表，不影响其他 UP
    if last_resp is not None:
        print(f"[bili-fail] uid={uid} 动态拉取失败: {last_err} | "
              f"status={last_resp.status_code} body={last_resp.text[:300]!r}", file=sys.stderr)
    else:
        print(f"[bili-fail] uid={uid} 动态拉取失败: {last_err}", file=sys.stderr)
    return []


class BilibiliSource:
    def __init__(self, uid: str, types: Optional[List[str]] = None,
                 all_videos: bool = False, window_days: Optional[float] = None,
                 force_all: bool = False):
        self.uid = str(uid)
        # 默认同时抓视频和动态
        self.types = types or ["video", "dynamic"]
        # all_videos：抓该 UP 全部视频（超大窗口 + 翻页 + 取消安全上限），用于「抓所有视频」。
        # 仅「首次运行」生效（is_first 门禁）：系列课首跑抓整门课，后续跑 is_first 变 False，
        # 自动回退到增量窗口（默认 1 天、封顶 30 天）+ seen 去重，与其他 UP 行为一致、不重复全抓。
        self.all_videos = bool(all_videos)
        # window_days：每源自定义时间窗口（天），覆盖首跑/每日窗口；None=走默认逻辑
        self.window_days = window_days
        # force_all：无视 is_first / seen，强制全量翻页抓取。
        # 这是「事后显式要全量重抓」时的开关（CLI --all-videos 传入，例如 seen 已填充想补历史）。
        # 不持久化进 subscriptions.json——默认行为永远是「首跑全量 + 后续增量」。
        self.force_all = bool(force_all)

    def source_key(self) -> str:
        return f"bilibili:{self.uid}"

    def discover(self, state: Dict, first_run_limit: int = 50, mode: str = "auto") -> List[Dict]:
        seen = get_seen(state, self.source_key())
        fetched_ids: List[str] = []
        vids: List[Dict] = []
        dyns: List[Dict] = []

        # 时间窗口（替代纯 count cap）：首跑回填窗口大（默认 30 天），每日增量窗口小（默认 1 天）。
        # 只处理窗口内发布的 content，不论条数——抓取条数多少不影响风控，频率（请求次数）才影响。
        # 每源覆盖：all_videos=True → 超大窗口抓全站历史；window_days 显式指定 → 用该值；
        # force_all=True → 强制全量（用户显式「全部抓取」，无视首跑/seen 门禁）。
        is_first = (mode == "first") or (mode == "auto" and not seen)
        # all_videos 仅「首次运行」生效（用户语义：本次添加监控后的首次跑抓全部；
        # 后续跑回退正常频率）。seen 在首跑后填充，is_first 自然变 False，无需手动清 flag。
        # force_all 是显式全量开关：CLI --all-videos 触发，优先级高于 all_videos+is_first 组合，
        # 保证「用户说全部抓取」必然全量、且不会因 seen 已填充而被静默跳过。
        all_videos_this_run = bool(self.force_all) or (bool(self.all_videos) and is_first)
        if all_videos_this_run:
            # 「抓所有视频」：窗口拉到 ~10 年，等效不按时间过滤；配合 paginate 翻全部分页。
            win_days = float(os.environ.get("BILI_ALL_VIDEOS_DAYS", "3650"))
        elif self.window_days is not None:
            win_days = float(self.window_days)
        elif is_first:
            win_days = _BILI_FIRST_WINDOW_DAYS
        else:
            # 自动补齐：距上次成功运行超过每日窗口时，按 gap 拉长窗口，抓回中间漏掉的内容；
            # 平时每日按时跑 gap≈1 天，win_days 依旧是 1 天，行为不变。封顶 BILI_MAX_WINDOW_DAYS 防极端。
            last_check = state["sources"].get(self.source_key(), {}).get("last_check", 0)
            max_win = float(os.environ.get("BILI_MAX_WINDOW_DAYS", "30"))
            win_days = effective_window_days(_BILI_DAILY_WINDOW_DAYS, last_check, max_win)
        cutoff = int(time.time()) - int(win_days * 86400)
        # 每类型安全上限（防极端 UP 单窗口发几百条刷爆笔记）；正常情况下窗口+单页上限已约束。
        # all_videos_this_run（含 force_all 显式全量）时取消上限，否则取 first_run_limit 与 _BILI_SAFETY_CAP 的较小值。
        if all_videos_this_run:
            cap = int(os.environ.get("BILI_ALL_VIDEOS_CAP", "100000"))
        else:
            cap = min(int(first_run_limit), _BILI_SAFETY_CAP)

        if "video" in self.types:
            # 先收集全部视频（force_all 翻全部分页；普通增量单页）。force_all 时预读 dedup 索引：
            # seen 已填充后，--all-videos 补历史必须跳过 seen 门禁（否则抓回的旧视频全被 seen 跳过=白抓），
            # 但已总结过的（dedup 索引命中）要跳过，避免重新入队/重复落盘。
            vlist = list(_fetch_vlist(self.uid, ps=_BILI_PAGE_SIZE, paginate=all_videos_this_run))
            summarized_urls: set = set()
            if all_videos_this_run:
                try:
                    from articles import dedup as _dedup  # lazy import：run.py 已保证 sys.path + load_dotenv
                    summarized_urls = _dedup.batch_is_summarized(
                        f"https://www.bilibili.com/video/{v.get('bvid')}" for v in vlist if v.get("bvid")
                    )
                except Exception:
                    summarized_urls = set()  # dedup 不可用时不拦截（能抓回历史优先，不因去重失败全放弃）
            for v in vlist:
                bvid = v.get("bvid")
                if not bvid or bvid in fetched_ids:
                    continue
                # 时间窗口过滤：只保留窗口内发布的视频。
                # 窗口外的绝不 mark_seen（与微信对齐）——否则更新频率低的 UP 首页 50 条覆盖几个月，
                # 断跑后 effective_window_days 补齐窗口时，本该补抓的旧视频已被 seen 永久跳过=漏抓。
                if int(v.get("created", 0) or 0) < cutoff:
                    continue
                # seen 去重：日常增量只处理新增；force_all（--all-videos 补历史）跳过 seen 门禁
                if bvid in seen and not all_videos_this_run:
                    continue
                # force_all 补历史：已总结过的跳过（防重复入队/重复落盘），未总结的错标历史才能补回
                if f"https://www.bilibili.com/video/{bvid}" in summarized_urls:
                    continue
                fetched_ids.append(bvid)
                is_charging = bool(v.get("is_charging_arc"))
                vids.append({
                    "source": "bilibili",
                    "mp_name": v.get("author", ""),
                    "id": bvid,
                    "title": v.get("title", ""),
                    "publish_time": int(v.get("created", 0) or 0),
                    "url": f"https://www.bilibili.com/video/{bvid}",
                    "route": "video",
                    "is_charging": is_charging,
                    "charging_badge": v.get("elec_arc_badge") or "",
                })
            # 同源内视频 -> 动态 之间退避，避免短时间密集请求
            if "dynamic" in self.types:
                time.sleep(_INTRA_GAP)

        if "dynamic" in self.types:
            # 拉满单页（覆盖整个时间窗口），再按 时间窗口 + 无干货 双重过滤。
            for d in _fetch_dynamics(self.uid, ps=_BILI_PAGE_SIZE):
                did = f"dyn:{d['id']}"
                if did in fetched_ids:
                    continue
                # 时间窗口过滤：只保留窗口内发布的动态（窗口外不 mark_seen，与视频/微信对齐）
                if int(d.get("pub_ts", 0) or 0) < cutoff:
                    continue
                if did in seen:
                    continue
                # 无正文/无干货（系统通知、空壳动态）直接屏蔽，不进总结管线
                if not _dynamic_is_substantive(d.get("text", "")):
                    continue
                fetched_ids.append(did)  # 窗口内（含无干货）都记 seen，避免下次重复拉取判断
                dyns.append({
                    "source": "bilibili",
                    "mp_name": d.get("author", ""),
                    "id": did,
                    "title": (d.get("text", "")[:50] or "动态"),
                    "publish_time": d.get("pub_ts", 0),
                    "url": f"https://t.bilibili.com/{d['id']}",
                    "route": "dynamic",
                    "content": d.get("text", ""),  # 动态正文直接来自 API，apply 时净化
                })

        # 视频/动态各自独立安全上限：避免某一种把配额占满导致另一种被整体截断
        vids = vids[:cap]
        dyns = dyns[:cap]
        new = vids + dyns

        mark_seen(state, self.source_key(), fetched_ids,
                  last_check=int(time.time()))
        return new
