"""videos/fetch.py — 视频字幕获取层（P2.1 获取层，架构解耦）

职责（PRD 架构约束 1：获取层与总结层解耦）：只负责"拿到字幕"，不负责总结。
- YouTube：youtube-transcript-api 拿 CC 字幕
- Bilibili：yt-dlp 抽 CC/自动字幕（.vtt）→ 解析为片段
- playlist / 分P：yt-dlp 列出 entries，逐条 URL 交给上层迭代

所有外部依赖缺失或网络失败均优雅返回 None，绝不抛异常阻断主流程。
"""

import os
import re
import json
import random
import time
import tempfile
import subprocess
import threading
from datetime import datetime
from typing import Optional, List, Dict, Tuple

YT_RE = re.compile(r"(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)([\w-]{11})")
BILI_RE = re.compile(r"(?:bilibili\.com/video/|b23\.tv/)([BV][A-Za-z0-9]+)")

# B：字幕轻量清洗（保守、不伤实义），统一在获取层出口应用
from shared.subtitle_clean import preprocess_segments, preprocess_text

# B站风控（HTTP 412）探测：请求函数命中 412 时置位，供上层「兜底短路」判断。
# 标记行 RISK_CONTROL_412_STOP 同时输出到 stdout，批量编排脚本据此熔断整批。
RISK_412_MARKER = "RISK_CONTROL_412_STOP"
_risk_412_hit = False


def _mark_risk_412() -> None:
    global _risk_412_hit
    _risk_412_hit = True
    print(f"   {RISK_412_MARKER} 命中B站风控(HTTP 412)，本次请求链提前终止")


def _risk_412_failfast() -> bool:
    """风控命中且调用方要求快速失败（批量场景）→ 跳过一切兜底请求。"""
    return _risk_412_hit and os.environ.get("BILI_FAILFAST_412") == "1"


def _is_http_412(e: Exception) -> bool:
    from urllib.error import HTTPError
    return isinstance(e, HTTPError) and getattr(e, "code", None) == 412


# ---------------------------------------------------------------------------
# 请求级追踪（2026-09-03）：批量补齐的结构化日志依赖。
# _bili_urlopen 包装所有 B站 HTTP 调用，累积 _HTTP_TRACE（ts/endpoint/status/ms/ok），
# run.py --batch-file 结束时随结果一起吐给编排层，写入运行日志。据此可分析：
# 「请求是否密集」（相邻请求时间差）、「412/413/429/超时」各出现在哪条、持续多久。
# ---------------------------------------------------------------------------
_HTTP_TRACE: List[Dict] = []


def _bili_urlopen(url: str, headers: Dict, timeout: float, tag: str = ""):
    """urllib.request.urlopen 的 B站专用包装：发请求 + 记录追踪（不改变原语义）。

    成功返回 response；HTTPError/网络异常在记录后原样抛出（与裸 urlopen 一致）。
    """
    import time as _t
    import urllib.error as _ue
    import urllib.request as _req
    host = re.sub(r"^https?://([^/]+).*$", r"\1", url or "") or ""
    t0 = _t.time()
    rec: Dict = {"ts": round(_t.time(), 3), "endpoint": tag, "host": host,
                 "status": None, "ms": 0, "ok": False}
    try:
        _rate_limit_acquire()  # 请求级滑动 1h 预算（所有 B站管线共用的单一咽喉）
        r = _req.Request(url, headers=headers)
        resp = _req.urlopen(r, timeout=timeout)
        rec["status"] = resp.getcode()
        rec["ms"] = int((_t.time() - t0) * 1000)
        rec["ok"] = True
        _HTTP_TRACE.append(rec)
        return resp
    except _ue.HTTPError as e:
        rec["status"] = getattr(e, "code", None)
        rec["ms"] = int((_t.time() - t0) * 1000)
        rec["err"] = f"HTTP {e.code}"
        _HTTP_TRACE.append(rec)
        if getattr(e, "code", None) == 412:
            _rate_limit_backoff_on_412()  # 越被风控越保守：本轮预算自动降档
        raise
    except Exception as e:
        rec["ms"] = int((_t.time() - t0) * 1000)
        rec["err"] = type(e).__name__
        _HTTP_TRACE.append(rec)
        raise


def get_http_trace() -> List[Dict]:
    """返回本进程累计的请求追踪（浅拷贝）。"""
    return list(_HTTP_TRACE)


def clear_http_trace() -> None:
    _HTTP_TRACE.clear()


def risk_412_hit() -> bool:
    """本进程内是否已命中过 412 风控（供批量编排按条标记 risk412）。"""
    return _risk_412_hit


# ---------------------------------------------------------------------------
# 请求级限流（2026-09-03，Q2/Q3）：预算以「请求数」而非「视频数」计，挂在
# _bili_urlopen 这个 HTTP 单一咽喉上——单视频补齐 / 系列整季 / 监控 / 救回等
# 所有 B站管线自动共用，不再出现「单视频有限流、系列批量零限流」的空洞。
#
# 预算是可算的（用户 Q2）：
#   每视频 ≈ 3 请求（view + dm_view + subtitle_cdn）
#   假设安全上限 200 请求/小时（经验值，待运行日志校准：密度/412 出现点），
#   留 15% 余量 → 默认 170 请求/小时 ≈ 56 视频/小时。
#   仅靠条间延迟 15~30s 只能压到 ≈440 请求/小时，压不进安全区 → 必须有请求级硬顶。
# 动态调整（用户 Q3）：命中 412 自动降预算 ×0.7（下限 30/小时），本轮内生效——
#   越被风控越保守；恢复需人工调 env 或下轮重新开始。
# env: BILI_MAX_REQ_PER_HOUR（默认 170；0=不限，仅调试用）。
# ---------------------------------------------------------------------------
_RATE = {"limit": None, "window": []}


def _rate_limit_max() -> int:
    if _RATE["limit"] is None:
        try:
            _RATE["limit"] = int(os.environ.get("BILI_MAX_REQ_PER_HOUR", "170"))
        except ValueError:
            _RATE["limit"] = 170
    return _RATE["limit"]


def _rate_limit_acquire() -> None:
    """滑动 1h 请求预算：满额则睡眠到最老请求出窗（+1~5s 抖动）。"""
    limit = _rate_limit_max()
    if limit <= 0:
        return
    now = time.time()
    w = _RATE["window"]
    while w and now - w[0] >= 3600:
        w.pop(0)
    if len(w) >= limit:
        sleep_s = max(1.0, w[0] + 3600 - now + random.uniform(1, 5))
        print(f"[rate] 请求预算 {limit}/小时已满，睡眠 {sleep_s:.0f}s 后继续", flush=True)
        time.sleep(sleep_s)
        now = time.time()
        while w and now - w[0] >= 3600:
            w.pop(0)
    w.append(time.time())


def _rate_limit_backoff_on_412() -> None:
    """命中 412 → 本轮请求预算自动下调 ×0.7（下限 30/小时）。"""
    limit = _rate_limit_max()
    if limit <= 0:
        return
    _RATE["limit"] = max(30, limit * 7 // 10)  # 整数运算，避免 170*0.7=118.999 浮点截断
    print(f"[rate] 命中412，请求预算自动下调：{limit} → {_RATE['limit']}/小时", flush=True)


def is_youtube(url: str) -> bool:
    return bool(YT_RE.search(url or ""))


def is_bilibili(url: str) -> bool:
    return bool(BILI_RE.search(url or ""))


def _yt_video_id(url: str) -> Optional[str]:
    m = YT_RE.search(url or "")
    return m.group(1) if m else None


def _run_with_timeout(fn, timeout: float, default=None):
    """在线程里跑 fn，超时返回 default（用于给 youtube-transcript-api 的网络尝试加超时，
    避免本机无 YouTube 出口时长时间挂起阻断主流程）。"""
    res = {"v": default}

    def _t():
        try:
            res["v"] = fn()
        except Exception:
            res["v"] = default

    th = threading.Thread(target=_t, daemon=True)
    th.start()
    th.join(timeout)
    return res["v"]


def _apply_yt_proxy_env() -> None:
    """若设置了 YT_PROXY，则映射到 HTTP(S)_PROXY 供底层 requests 使用。

    仅在「本机有可复用代理端口」时才有意义（如开启 Clash 系统代理）。
    纯浏览器插件代理（无本地端口）无法复用，此时需在 WorkBuddy 沙箱运行
    或使用 videos/ 的 CDP 方案走浏览器。
    """
    p = os.environ.get("YT_PROXY")
    if p:
        os.environ["HTTP_PROXY"] = p
        os.environ["HTTPS_PROXY"] = p


# ---------------------------------------------------------------------------
# VTT 解析（纯函数，离线可测）
# ---------------------------------------------------------------------------

def parse_vtt(content: str) -> List[Dict[str, float]]:
    """解析 WebVTT 字幕为片段列表 [{'start','duration','text'}]。"""
    segments: List[Dict[str, float]] = []
    blocks = re.split(r"\n\s*\n", content.strip())
    cue_re = re.compile(r"(\d{2}:\d{2}:\d{2})\.(\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2})\.(\d{3})")
    for block in blocks:
        lines = [l for l in block.splitlines() if l.strip() and not l.strip().isdigit()
                 and not l.strip().upper().startswith("WEBVTT")]
        if not lines:
            continue
        m = cue_re.search(lines[0])
        if not m:
            # 可能 cue 标识符单独成行，时间轴在第二行
            if len(lines) >= 2:
                m = cue_re.search(lines[1])
                lines = lines[2:] if m else lines[1:]
            if not m:
                continue
        else:
            lines = lines[1:]
        start = _ts_to_sec(m.group(1), m.group(2))
        end = _ts_to_sec(m.group(3), m.group(4))
        text = " ".join(l.strip() for l in lines).strip()
        if text:
            segments.append({"start": start, "duration": max(0.0, end - start), "text": text})
    return segments


def _ts_to_sec(hms: str, ms: str) -> float:
    h, m, s = hms.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------

def fetch_youtube_transcript_cdp(url: str, port: Optional[int] = None,
                                 wait: int = 45) -> Optional[Tuple[str, str]]:
    """CDP 方案：驱动本机带代理插件的 Chrome 抓字幕（绕过 API 的网络限制）。

    返回 (title, transcript_text)；失败返回 None。
    port=None 时经 shared.cdp_session.ensure_endpoint() 自动编排端点
    （共享克隆目录 + 随机端口 + 端口文件；仅 clone 陈旧需复制时才关 Chrome）。
    """
    try:
        from videos.cdp_capture import capture_transcript
        from shared.cdp_session import ensure_endpoint
    except Exception as e:
        print(f"   ⚠️ CDP 依赖不可用: {e}")
        return None
    if port is None:
        try:
            port = ensure_endpoint().port
        except Exception as e:
            print(f"   ⚠️ CDP 端点编排失败（Chrome 未就绪？）: {e}")
            return None
    try:
        title, text = capture_transcript(url, port=port, wait=wait)
        if text:
            print(f"   ✅ YouTube 字幕(CDP)获取成功（{len(text)} 字）")
            return (title or _yt_video_id(url) or "", text)
        print("   ⚠️ CDP 未捕获到字幕（页面已加载但 captionTracks 为空，此视频无 CC/自动字幕）")
        return None
    except Exception as e:
        print(f"   ⚠️ CDP 字幕抓取异常: {e}")
        return None


def fetch_youtube_transcript(url: str, languages: Tuple[str, ...] = ("zh-Hans", "zh", "en"),
                             use_cdp_fallback: bool = True) -> Optional[Tuple]:
    """获取 YouTube 视频字幕。

    策略（自适应环境）：
      1. 先试 youtube-transcript-api（WorkBuddy 沙箱可直连、无需浏览器；加超时防止
         本机无 YouTube 出口时长时间挂起）。
      2. 失败/超时则回退 CDP 方案（驱动本机带代理插件的 Chrome 抓字幕，本机首选）。

    Returns:
        (title, segments) 或 (title, transcript_text) 或 None
        —— 调用方（videos.main）对 List[Dict] 与 str 两种形态都已兼容。
    """
    vid = _yt_video_id(url)
    if not vid:
        print("❌ 无法从 URL 解析 YouTube 视频 ID")
        return None

    # 1) API 路径（带超时）
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        def _api():
            _apply_yt_proxy_env()
            api = YouTubeTranscriptApi()
            raw = api.fetch(vid, languages=list(languages)).to_raw_data()
            segs = [{"text": s.get("text", ""), "start": s.get("start", 0.0),
                     "duration": s.get("duration", 0.0)} for s in raw]
            return (segs, _yt_title(url) or vid)
        result = _run_with_timeout(_api, timeout=25)
        if result and result[0]:
            segs, title = result
            segs = preprocess_segments(segs)  # B：字幕轻量清洗
            print(f"   ✅ YouTube 字幕获取成功（清洗后 {len(segs)} 条）")
            return (title, segs)
    except Exception as e:
        print(f"   ℹ️ YouTube API 路径失败: {e}")

    # 2) CDP 回退（本机带代理插件的 Chrome）
    if use_cdp_fallback:
        cdp = fetch_youtube_transcript_cdp(url)
        if cdp:
            title, text = cdp
            text = preprocess_text(text)  # B：纯文本字幕清洗
            return (title, text) if text else None

    print("❌ 该视频无可用字幕（页面已加载，但无 CC/自动字幕轨道）。")
    print("   → 按项目约定直接回复用户：【此视频暂无 CC 字幕，无法为你抓取字幕总结内容。】")
    return None


def _yt_title(url: str) -> str:
    """best-effort 用 yt-dlp 取标题；失败或超时则回退空串（调用方会回退到视频 ID）。

    带 socket 超时，避免网络不可达时无限挂起阻断主流程。
    """
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "socket_timeout": 10,
        }) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("title") or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Bilibili（原生 AI 字幕 API 链路）
#
# B站字幕链路（2026-07 验证，无需登录 cookie）：
#   1. view API       → aid / cid / title
#   2. dm/view API    → subtitle.subtitles 列表（含 ai-zh / ai-en）
#   3. subtitle_url   → 字幕正文 CDN 地址（含 prod/ 前缀 + auth_key 签名，
#                       直链可下载，无需 SESSDATA）。注意：自行拼
#                       https://aisubtitle.hdslb.com/bfs/ai_subtitle/{aid}/{cid}/{id}.json
#                       会因缺 auth_key 而 403，必须用 dm/view 返回的完整 URL。
#   4. yt-dlp         → 兜底（仅当用户显式提供了 --cookies 时才有意义）
#
# 结论：B站 AI 字幕下载**无需任何登录态**，此前以为要 cookie 是误判。
#       下方自动提取 cookie 的逻辑保留作未来扩展（如鉴权 API），但主链路不再依赖它。
# ---------------------------------------------------------------------------

def _bili_extract_bvid(url: str) -> Optional[str]:
    m = BILI_RE.search(url or "")
    return m.group(1) if m else None


# 最近一次抓到的 B 站视频发布时间（epoch 秒，2026-09-07 生成侧发布时间链路）：
# fetch_subtitle_only 拿到 view 信息后写入，_handle_single_video 读取注入保存链路。
LAST_PUBDATE = 0

# 最近一次抓取是否命中「充电专属·仅试看」（2026-09-20）：
# fetch_subtitle_only 读 view API 后写入。⚠️ 它**只用于打印更准确的原因**，不再作为拦截依据
# ——实证「充电」限制的是媒体流，字幕流可能完整（详见 bili_is_charging_exclusive 的说明）。
LAST_CHARGING_EXCLUSIVE = False


def bili_is_charging_exclusive(info: Optional[Dict]) -> bool:
    """判断该 B站视频是否为「充电专属**且当前账号只能看试看片段**」（2026-09-20 新增）。

    ⚠️ **它不等于「源残缺」，不要拿它直接拦内容**（2026-09-20 实测更正）：
    upower 限制的是**媒体流**（音频只给试看片段），**字幕流不一定要限**。实证两集走向相反：
      - `BV1eL4k6jEii`：音频只给 1/41 分钟，**字幕却给了全片**（源 11352 字 / 4.57 字/秒），笔记合法；
      - `BV1fr8P6REBW`：音频只给 32/104 分钟（源真残）。
    ⇒ 正确策略：**有字幕就用字幕直接过**；没字幕才走 ASR，由**音频覆盖率校验**（<90% 拒绝，
    见 `videos/asr.py::transcribe_video`）裁决。本函数只用于把日志原因打得更准，以及让上层
    决定要不要多打印一句提示。

    判据：view API 的 `is_upower_exclusive` 与 `is_upower_preview` **同时为真**
    （只看 `is_upower_exclusive` 会在「账号已充电、可看全片」时误判）；
    兼容投稿列表 API 的 `is_charging_arc`（老入口没有 upower 字段时用它兜底）。
    """
    if not info:
        return False
    if info.get("is_upower_exclusive") and info.get("is_upower_preview"):
        return True
    return bool(info.get("is_charging_arc"))


def _bili_get_video_info(bvid: str) -> Optional[Dict]:
    """返回视频基础信息 + 所有分P（多P系列课）列表。

    Returns:
        {"aid", "cid"(首P), "title", "author"(UP主名),
         "pages": [{"cid", "page", "part"}, ...]} 或 None
    """
    import json as _j
    api = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    try:
        resp = _bili_urlopen(api, {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com/",
        }, timeout=15, tag="view")
        data = _j.loads(resp.read())
        if data.get("code") == 0:
            d = data["data"]
            pages = [
                {"cid": p["cid"], "page": p.get("page", i + 1), "part": p.get("part", "")}
                for i, p in enumerate(d.get("pages", []))
            ]
            return {
                "aid": d["aid"],
                "cid": d["cid"],
                # 视频发布时间（epoch 秒，2026-09-07 生成侧发布时间链路）
                "pubdate": int(d.get("pubdate") or 0),
                "title": d.get("title", ""),
                # 视频简介/描述（project_import 用它优先找仓库链接）
                "desc": d.get("desc", ""),
                # UP主名（view API 的 owner.name），上层据此填充笔记作者
                "author": d.get("owner", {}).get("name", ""),
                "pages": pages or [{"cid": d["cid"], "page": 1, "part": ""}],
                # 系列课（UP主聚合的多个独立视频）：含 sections[].episodes[]
                "ugc_season": d.get("ugc_season"),
                # 视频总时长（秒）。ASR 侧用它做「源完整性」校验：本地音频时长若
                # 明显短于它，说明拿到的是残缺源（充电专属试看 / 下载截断），
                # 此时必须拒绝产出笔记（2026-09-20 实踩，原为静默产出残缺品）。
                "duration": int(d.get("duration") or 0),
                # 充电专属（UP 主付费内容）：B站只给试看片段，任何下载方式都拿不到全片。
                # 命中时 ASR 直接跳过并给出明确原因，避免把试看片段当全片总结。
                "is_upower_exclusive": bool(d.get("is_upower_exclusive")),
                "is_upower_preview": bool(d.get("is_upower_preview")),
            }
    except Exception as e:
        if _is_http_412(e):
            _mark_risk_412()
    return None


def get_bilibili_pages(url: str) -> Optional[List[Dict]]:
    """轻量判断：该 B 站视频是否为多P（系列课）。

    Returns:
        pages 列表（含 cid/page/part）或 None（失败/非B站链接）
    """
    bvid = _bili_extract_bvid(url)
    if not bvid:
        return None
    info = _bili_get_video_info(bvid)
    return info.get("pages") if info else None


def _bili_get_subtitle_list(aid: int, cid: int, retries: int = None) -> Optional[List[Dict]]:
    """获取分P字幕列表（dm/view API）。

    B站 dm/view 接口偶发限流（code=-429）或瞬时返回空字幕列表，故加带退避的
    重试，避免把"瞬时限流"误判为"视频无字幕"。
    retries 默认取环境变量 BILI_SUB_RETRIES（默认 3）；命中 HTTP 412 风控时立即
    放弃重试并置位风控标记（重试只会加深风控，不会成功）。
    """
    import json as _j, time as _t
    if retries is None:
        retries = max(1, int(os.environ.get("BILI_SUB_RETRIES", "3")))
    url = f"https://api.bilibili.com/x/v2/dm/view?aid={aid}&oid={cid}&type=1"
    for attempt in range(retries):
        try:
            resp = _bili_urlopen(url, {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.bilibili.com/",
            }, timeout=15, tag="dm_view")
            data = _j.loads(resp.read())
            # code != 0 多为限流（-429）等瞬时故障，重试
            if data.get("code") != 0:
                if attempt < retries - 1:
                    _t.sleep(1.5 * (attempt + 1))
                    continue
                return None
            subs = data.get("data", {}).get("subtitle", {}).get("subtitles", [])
            if subs:
                return subs
            # 字幕列表为空：B站偶发返回空，重试一次
            if attempt < retries - 1:
                _t.sleep(1.5 * (attempt + 1))
                continue
        except Exception as e:
            if _is_http_412(e):
                _mark_risk_412()
                return None
            if attempt < retries - 1:
                _t.sleep(1.5 * (attempt + 1))
                continue
            return None
    return None


def _bili_download_subtitle_body(subtitle_url: str, cookies: str = None) -> Optional[List[Dict]]:
    """下载字幕正文。

    subtitle_url 来自 dm/view API 返回的完整 CDN 地址（含 prod/ 前缀与 auth_key
    查询参数）。该 URL 自带签名、无需登录即可访问；自行拼装 URL 会因缺少
    auth_key 而 403。cookies 参数保留以备未来签名过期 Scenario。
    """
    import json as _j
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com/",
    }
    if cookies:
        hdrs["Cookie"] = cookies
    # http(s) 兼容：CDN 返回的是 http 地址，直接复用即可
    url = subtitle_url
    try:
        resp = _bili_urlopen(url, hdrs, timeout=20, tag="subtitle_cdn")
        body = _j.loads(resp.read())
        items = body.get("body", [])
        if not isinstance(items, list):
            return None
        segs = []
        for item in items:
            content = item.get("content", "").strip()
            if not content:
                continue
            t_from = item.get("from", 0)
            segs.append({
                "start": float(t_from),
                "duration": float(item.get("to", t_from + 3)) - float(t_from),
                "text": content,
            })
        return segs if segs else None
    except Exception:
        return None


def _bili_cache_dir() -> str:
    """cookie 缓存目录（.cache，已被 gitignore）。"""
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _bili_cache_path() -> str:
    return os.path.join(_bili_cache_dir(), "bilibili_cookies.txt")


def _bili_ydl_cookiefile() -> Optional[str]:
    """返回可用的 Netscape 格式 cookie 文件路径（无 cookie 时返回 None）。

    优先用现成缓存文件；否则把 BILI_COOKIE（header 字符串）转 Netscape 格式
    写入 .cache/bili_cookies_netscape.txt 供 yt-dlp 过 B站 412 风控。
    """
    netscape = os.path.join(_bili_cache_dir(), "bili_cookies_netscape.txt")
    if os.path.exists(netscape) and os.path.getsize(netscape) > 50:
        return netscape
    raw = (os.environ.get("BILI_COOKIE") or "").strip()
    if not raw:
        cached = _bili_load_cached_cookies()
        raw = (cached or "").split("\n")[0].strip()
    if not raw:
        return None
    lines = ["# Netscape HTTP Cookie File"]
    for pair in raw.split(";"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            k, v = k.strip(), v.strip()
            if k and k != "#":
                lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t0\t{k}\t{v}")
    if len(lines) < 3:
        return None
    try:
        with open(netscape, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return netscape
    except Exception:
        return None


def _bili_load_cached_cookies() -> Optional[str]:
    try:
        p = _bili_cache_path()
        if os.path.exists(p):
            return open(p, encoding="utf-8").read().strip() or None
    except Exception:
        pass
    return None


def _bili_cache_cookies(cookie_str: str) -> None:
    try:
        with open(_bili_cache_path(), "w", encoding="utf-8") as f:
            f.write(cookie_str)
    except Exception:
        pass


def validate_bilibili_cookies(cookie_str: str) -> bool:
    """快速校验 cookie 是否有效（B站是否已登录）。

    调用 nav API，依据 data.isLogin 判断。任一异常均视为无效，不阻断主流程。
    SESSDATA 无论 URL 编码（%2C）还是解码（,）形式，nav API 均可识别。
    """
    import json as _j
    if not cookie_str:
        return False
    url = "https://api.bilibili.com/x/web-interface/nav"
    try:
        resp = _bili_urlopen(url, {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com/",
            "Cookie": cookie_str,
        }, timeout=15, tag="nav_validate")
        data = _j.loads(resp.read())
        return bool(data.get("data", {}).get("isLogin"))
    except Exception:
        return False


def _bili_load_valid_cached_cookies() -> Optional[str]:
    """读取缓存 cookie，并校验其有效性。

    若已失效（过期/被挤下线），打印更换指引并清空缓存，返回 None。
    字幕主链路无需 cookie，故失效时不影响字幕获取，仅影响需要登录的功能。
    """
    cached = _bili_load_cached_cookies()
    if not cached:
        return None
    if validate_bilibili_cookies(cached):
        return cached
    print("   ℹ️ 缓存的 B 站 cookie 已失效（可能已过期或被挤下线）。")
    print("      → 重新获取：Chrome 登录 B站 → F12 → Application → Cookies → 复制 SESSDATA")
    print("      → 更新命令：python -m videos.set_cookie \"SESSDATA=...\"")
    try:
        os.remove(_bili_cache_path())
    except Exception:
        pass
    return None


def _bili_extract_cookies_cdp(wait_s: float = None) -> Optional[str]:
    """B站 cookie 获取的【唯一方法】（用户 2026-09-03 拍板「有且只有一个」）：

    CDP profile 克隆（关 Chrome → 克隆 → 非默认 dir 开调试端口，SharedCdpSession
    生产路径）→ **真实访问 bilibili.com** → 从活会话读 cookie。
    - 为什么必须访问：只读磁盘存量拿不到「最新」；带登录态访问后 B站校验/续发
      Set-Cookie，活会话里读到的才是当前有效值。
    - 未登录【不报错退出】：保持会话打开，每 5s 轮询 SESSDATA，等用户在可见窗口
      登录，最长 wait_s（默认 BILI_COOKIE_WAIT_S=300，即 5 分钟）。
    - Chrome 151+ 默认 dir 禁用一切远程调试（含 --remote-debugging-pipe），Playwright
      挂真实 profile 的老路已死（实测 TimeoutError），故删除、不再保留。
    代价：仅 clone 陈旧需复制时才关 Chrome（2026-09-10 条件化）；会话结束只注销持有者，
    最后持有者才关灯（见 shared/cdp_session.py D5/D6 契约）。
    """
    if wait_s is None:
        try:
            wait_s = float(os.environ.get("BILI_COOKIE_WAIT_S", "300"))
        except ValueError:
            wait_s = 300.0
    try:
        from shared.cdp_session import SharedCdpSession
    except Exception as e:
        print(f"   ℹ️ CDP 会话依赖不可用：{type(e).__name__}: {e}")
        return None
    sess = None
    try:
        sess = SharedCdpSession()
        page = sess._ctx.pages[0] if getattr(sess._ctx, "pages", None) else sess._ctx.new_page()
        page.goto("https://www.bilibili.com", wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(2500)
        deadline = time.time() + wait_s
        announced = False
        while True:
            cookies = sess._ctx.cookies("https://www.bilibili.com")
            if any(c.get("name") == "SESSDATA" for c in cookies):
                break
            if time.time() >= deadline:
                print(f"   ℹ️ 等待登录超时（{int(wait_s)}s 内未出现 SESSDATA），本轮轮换放弃。")
                return None
            if not announced:
                announced = True
                print(f"   ⏳ 未检测到 B站登录——请在弹出的浏览器窗口里登录（扫码/账密），"
                      f"最长等 {int(wait_s)}s（每 5s 检测）…")
            time.sleep(5)
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        _bili_cache_cookies(cookie_str)
        print(f"   ✅ 已从 CDP 活会话提取 B站 cookie（{len(cookies)} 项，含 SESSDATA，已缓存 .cache）")
        return cookie_str
    except Exception as e:
        print(f"   ℹ️ CDP 提取 cookie 失败：{type(e).__name__}: {e}")
        return None
    finally:
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass


def _persist_cookie_to_env(cookie_str: str) -> bool:
    """把新 cookie 写回项目 .env 的 BILI_COOKIE 行（先备份 .env.bili_cookie.bak）。

    为什么必须写：cookie 取用优先级是 env > .cache，.env 里留着过期 cookie 会让
    每次运行都判定失效、重复走昂贵的 CDP 克隆轮换。写回后一轮到位。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(root, ".env")
    try:
        lines = []
        if os.path.exists(env_path):
            with open(env_path, encoding="utf-8") as f:
                lines = f.read().splitlines()
            with open(env_path + ".bili_cookie.bak", "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        hit = False
        out = []
        for ln in lines:
            if ln.strip().startswith("BILI_COOKIE="):
                out.append(f"BILI_COOKIE={cookie_str}")
                hit = True
            else:
                out.append(ln)
        if not hit:
            out.append(f"BILI_COOKIE={cookie_str}")
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        print("   💾 新 cookie 已写回 .env（备份：.env.bili_cookie.bak）")
        return True
    except Exception as e:
        print(f"   ℹ️ 写回 .env 失败（可手动把新 cookie 粘到 BILI_COOKIE= 行）：{e}")
        return False


def _bili_build_cookies_from_env() -> Optional[str]:
    """返回 B 站 cookie 头字符串，按优先级：
    1. 环境变量 BILIBILI_COOKIES / BILI_COOKIE（完整 cookie 串，后者为本项目 .env 实际变量名）
    2. 环境变量 SESSDATA / BILIBILI_SESSDATA
    3. 本地缓存（.cache/bilibili_cookies.txt）
    4. 自动从本机 Chrome 提取（Playwright，仅首次，会缓存）
    """
    full = os.environ.get("BILIBILI_COOKIES") or os.environ.get("BILI_COOKIE", "")
    if full:
        return full
    sess = os.environ.get("SESSDATA", "") or os.environ.get("BILIBILI_SESSDATA", "")
    if sess:
        parts = [f"SESSDATA={sess}"]
        jct = os.environ.get("BILIBILI_JCT", "")
        if jct:
            parts.append(f"bili_jct={jct}")
        return "; ".join(parts)
    cached = _bili_load_valid_cached_cookies()
    if cached:
        return cached
    # 不在热路径里自动拉 CDP 会话（重 + 可能弹窗等登录）；cookie 轮换由
    # rotate_bili_cookie_if_dead 显式触发（补齐管线运行开始/命中412后 + 手动脚本）。
    return None


# ---------------------------------------------------------------------------
# B站 cookie 轮换状态记录（2026-09-06 加：记录换取时间 + 被动每7天 + 每次换必记录）
# ---------------------------------------------------------------------------
_BILI_ROTATION_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "bili_cookie_rotation.json",
)


def _bili_rotation_state_path() -> str:
    return _BILI_ROTATION_STATE_PATH


def _bili_load_rotation_state() -> dict:
    """读取轮换状态；不存在/损坏则返回默认值。"""
    default = {
        "interval_days": int(os.environ.get("BILI_COOKIE_INTERVAL_DAYS", "7")),
        "last_rotation_ts": None,          # 最近一次【成功】换 cookie 的时间(ISO)
        "last_interval_attempt_ts": None,  # 最近一次【尝试】被动(7天)轮换时间(含失败, ISO)
        "history": [],                     # 最近事件：{ts, trigger, result, method, note}
    }
    try:
        if os.path.exists(_BILI_ROTATION_STATE_PATH):
            with open(_BILI_ROTATION_STATE_PATH, encoding="utf-8") as f:
                data = json.load(f)
            for k in default:
                if k not in data:
                    data[k] = default[k]
            return data
    except Exception:
        pass
    return default


def _bili_save_rotation_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_BILI_ROTATION_STATE_PATH), exist_ok=True)
        with open(_BILI_ROTATION_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"   ℹ️ 轮换状态写回失败（不影响主流程）：{e}")


def _bili_last_event_dt(state: dict) -> Optional[datetime]:
    """取 last_rotation_ts / last_interval_attempt_ts 中较新者（用于7天闸门）。"""
    cands = [state.get("last_rotation_ts"), state.get("last_interval_attempt_ts")]
    dts = []
    for c in cands:
            if c:
                try:
                    # 统一转 naive（存的时候可能是带时区的 ISO），避免与 datetime.now() 相减报 TypeError
                    dts.append(datetime.fromisoformat(c).replace(tzinfo=None))
                except Exception:
                    pass
    return max(dts) if dts else None


def _bili_record_rotation_event(state: dict, trigger: str, result: str,
                                method: str = "none", note: str = None) -> None:
    """把一次轮换尝试记入 history（截断到最近 50 条）并落盘。

    result: 'rotated' | 'valid' | 'failed'
    method: 'cdp' | 'none'
    成功换 → 更新 last_rotation_ts；被动(interval)触发 → 同时更新 last_interval_attempt_ts
    （无论成功失败都更新，使失败也延迟7天再试，避免每天弹窗等登录）。
    """
    now = datetime.now().isoformat(timespec="seconds")
    state.setdefault("history", []).append({
        "ts": now, "trigger": trigger, "result": result,
        "method": method, "note": note,
    })
    state["history"] = state["history"][-50:]
    if result == "rotated":
        state["last_rotation_ts"] = now
    if trigger == "interval":
        state["last_interval_attempt_ts"] = now
    _bili_save_rotation_state(state)


def rotate_bili_cookie_if_dead(trigger: str = "manual", force: bool = False,
                               interval_days: int = None) -> Tuple[str, Optional[str]]:
    """cookie 轮换（2026-09-06 升级：支持被动7天 + 全程记录）。

    决策（按序）：
    1. force=True（主动换）→ 无条件走 CDP 重新提取；
    2. 当前 cookie 失效（nav isLogin=False）→ 走 CDP；
    3. trigger=="interval" 且距上次成功/尝试 ≥ interval_days（默认7）→ 走 CDP（被动换）；
    4. 否则 → ("valid", None)（不折腾）。

    CDP 提取成功且 nav 校验通过 → 写回 .env/.cache，记录 result='rotated'；
    提取/校验失败/登录超时 → 记录 result='failed'（被动场景仍延迟7天再试）。

    每次调用都会落盘一条 history（ts/trigger/result/method），满足「每一次换都要记录」。
    interval_days 可由环境变量 BILI_COOKIE_INTERVAL_DAYS 覆盖（默认7）。
    返回 (state, fresh_or_None)。
    """
    if interval_days is None:
        try:
            interval_days = int(os.environ.get("BILI_COOKIE_INTERVAL_DAYS", "7"))
        except ValueError:
            interval_days = 7
    state = _bili_load_rotation_state()
    state["interval_days"] = interval_days

    cur = _bili_build_cookies_from_env()
    cur_valid = bool(cur) and validate_bilibili_cookies(cur)

    last_evt = _bili_last_event_dt(state)
    due_by_interval = (
        trigger == "interval"
        and last_evt is not None
        and (datetime.now() - last_evt).total_seconds() / 86400.0 >= interval_days
    )

    if not force and cur_valid and not due_by_interval:
        # 仍有效且未到被动轮换周期 → 不换，仅记录一次检查
        _bili_record_rotation_event(state, trigger, "valid", "none",
                                    "当前 cookie 有效，未到轮换周期")
        return ("valid", None)

    print(f"   ⚠️ 触发 B站 cookie 轮换（trigger={trigger}, force={force}, "
          f"cur_valid={cur_valid}）→ 走 CDP 活会话提取…")
    fresh = _bili_extract_cookies_cdp()
    if not (fresh and validate_bilibili_cookies(fresh)):
        # 首次提取未拿到有效 cookie：克隆会话可能已被服务端作废（cookie 文件还在但
        # nav 判死）→ 立即强制重克隆默认 profile 取活会话，再重试一次提取。用户
        # 2026-09-24 拍板：克隆死就立即触发，不加任何守卫（自动监控轮次亦然）。
        print("   ⚠️ 首次 CDP 提取未拿到有效 cookie（克隆会话可能已死）"
              "→ 立即强制重克隆默认 profile 后重试…")
        try:
            from shared.cdp_session import force_refresh_clone
            if force_refresh_clone():
                fresh = _bili_extract_cookies_cdp()
            else:
                print("   ℹ️ 强制重克隆未成功，不再重试。")
        except Exception as e:
            print(f"   ℹ️ 强制重克隆触发失败：{type(e).__name__}: {e}")
    if fresh and validate_bilibili_cookies(fresh):
        print("   ♻️ cookie 已轮换（来源：CDP 活会话），后续请求使用新 cookie")
        _persist_cookie_to_env(fresh)
        _bili_record_rotation_event(state, trigger, "rotated", "cdp",
                                    "CDP 提取成功并写回 .env/.cache")
        return ("rotated", fresh)
    note = "CDP 提取超时或 nav 校验未通过（本机 Chrome 是否登录 B站？）"
    print(f"   ℹ️ cookie 轮换失败。{note}")
    _bili_record_rotation_event(state, trigger, "failed", "cdp", note)
    return ("failed", None)


def _bili_fetch_page_subtitle(aid: int, cid: int, lang: str = "zh") -> Optional[List[Dict]]:
    """抓取单个分P（page）的字幕，返回片段列表或 None。

    策略（按优先级）：
    1. 原生 API 链路（dm/view -> aisubtitle CDN，带 auth_key 直链下载）
    2. cookie 认证重拼 URL（当 dm/view 未返回 subtitle_url 时）
    """
    sub_list = _bili_get_subtitle_list(aid, cid)
    if not sub_list:
        return None
    lang_priority = [f"ai-{lang}", lang, "ai-zh", "ai-en", ""]
    chosen = None
    for lp in lang_priority:
        matches = [s for s in sub_list if s.get("lan") == lp]
        if matches:
            chosen = matches[0]
            break
    if not chosen and sub_list:
        chosen = sub_list[0]
    if not chosen:
        return None

    # dm/view 已返回带 auth_key 的完整 CDN 地址，无需登录即可直链下载
    sub_url = chosen.get("subtitle_url")
    if not sub_url:
        print("   ⚠️ dm/view 未返回 subtitle_url，降级使用 cookie 认证重拼 URL")
        cookies = _bili_build_cookies_from_env()
        segs = _bili_download_subtitle_body(
            f"https://aisubtitle.hdslb.com/bfs/ai_subtitle/{aid}/{cid}/{chosen['id']}.json",
            cookies=cookies,
        )
        return preprocess_segments(segs) if segs else None
    segs = _bili_download_subtitle_body(sub_url)
    return preprocess_segments(segs) if segs else None


def _bili_part_redundant(main: str, part: str) -> bool:
    """判定分P副标题是否冗余（避免拼出「标题 - 标题」式重复）。

    原修复只挡了 part == title 完全相等；实战发现副标题与主标题差一两个
    错字（如「演绎」vs「演経」）时仍会漏网。这里加三层判定：
      1) 完全相等；2) 归一（去空白/标点）后互为子串；3) difflib 相似度 ≥ 0.9。
    真实多P 的副标题（与主标题语义不同）三层均不命中 → 正常拼接。
    """
    if not part:
        return True
    if part == main:
        return True
    import re as _re, difflib as _dl
    _norm = lambda s: _re.sub(
        r"[\s\u3000—\-·:：，。？！、~～\"'‘’“”()（）\[\]【】]+", "", s or ""
    )
    nm, np_ = _norm(main), _norm(part)
    if np_ and (np_ in nm or nm in np_):
        return True
    if nm and np_ and _dl.SequenceMatcher(None, nm, np_).ratio() >= 0.9:
        return True
    return False


def fetch_subtitle_only(url: str, lang: str = "zh", page: int = None) -> Optional[Tuple[str, List[Dict], str]]:
    """【线性主干·步骤B：只抓字幕】原生 API 优先 → yt-dlp 自动字幕兜底，**不做 ASR**。

    字幕完全缺失时返回 None，交由上层调用方显式决定何时走 ASR 分支
    （而不是把 ASR 藏在本函数里让调用方看不见）。

    Returns: (title, segments, author) 或 None
    """
    bvid = _bili_extract_bvid(url)
    if not bvid:
        print("无法从 URL 提取 Bilibili BV 号")
        return None

    if page is None:
        m = re.search(r"[?&]p=(\d+)", url or "")
        if m:
            page = int(m.group(1))

    info = _bili_get_video_info(bvid)
    if not info:
        return None
    global LAST_PUBDATE, LAST_CHARGING_EXCLUSIVE
    LAST_PUBDATE = int(info.get("pubdate") or 0)
    aid = info["aid"]
    title = info["title"]
    pages = info.get("pages") or []

    # 充电专属（仅试看）：**不再一刀切跳过**（2026-09-20 用户定策，微调后）——
    # 实证「充电」限制的是**媒体流**（音频只给试看片段），**字幕流不一定要限**：
    #   BV1eL4k6jEii 音频只给 1/41 分钟，但字幕给了全片（源 11352 字 / 4.57 字/秒），笔记完全合法。
    # 故策略改为：**有字幕就用字幕直接过**；拿不到字幕才走 ASR，由「源完整性校验」决定收不收
    # （见 videos/asr.py::transcribe_video 的 90% 覆盖率闸门）。本标记只用于打印更准确的原因。
    LAST_CHARGING_EXCLUSIVE = bili_is_charging_exclusive(info)
    if LAST_CHARGING_EXCLUSIVE:
        print(f"   ℹ️ 该集为充电专属（当前账号仅可试看）：先按字幕抓取；若无字幕，"
              f"则由音频完整性校验决定是否收录。")

    target = None
    if page and pages:
        target = next((p for p in pages if p["page"] == page), None)
    if target is None and pages:
        target = pages[0]
    cid = target["cid"] if target else info["cid"]
    if target and target.get("part") and not _bili_part_redundant(title, target["part"]):
        title = f"{title} - {target['part']}"

    # 链路1：原生 API 字幕
    segs = _bili_fetch_page_subtitle(aid, cid, lang)
    if segs:
        print(f"   OK Bilibili 字幕获取成功（{len(segs)} 条，API 原生链路）")
        return (title, segs, info.get("author", ""))

    # 风控熔断（批量场景 BILI_FAILFAST_412=1）：API 链路已命中 412 时，
    # yt-dlp 兜底只会再吃一次 412 并加重风控，直接放弃本条。
    if _risk_412_failfast():
        print("   STOP 风控412命中，跳过 yt-dlp 兜底")
        return None

    # 批量补齐：跳过 yt-dlp 兜底（避免自动字幕下载触发 412 / 拖慢节奏），
    # 无 AI 字幕的视频留待后续单独 ASR 处理（BILI_BATCH_NO_ASR=1）。
    if os.environ.get("BILI_BATCH_NO_ASR") == "1":
        print("   SKIP 批量模式跳过 yt-dlp 兜底（无 AI 字幕，留待后续 ASR 单独处理）")
        return None

    # 链路2：yt-dlp 自动字幕兜底（仍属「字幕」范畴，非 ASR）
    print("   WARN 该分P无 AI 字幕，尝试 yt-dlp 兜底抓自动字幕")
    try:
        import yt_dlp
    except ImportError:
        yt_dlp = None
    if yt_dlp is not None:
        # 仅当视频确实多P（pages>1）且 page 落在有效区间内才拼 ?p=N；
        # ugc_season 每集是独立单P BV，page 只是系列集号元数据，拼 ?p=N 会指向
        # 不存在的分P → yt-dlp 报 "No video formats found"。单P 视频直接用基础 URL。
        multi_page = len(pages) > 1
        page_within = page and (1 <= page <= len(pages))
        if page and multi_page and page_within:
            page_url = f"{url.split('?')[0]}?p={page}"
        else:
            page_url = url.split('?')[0]
        tmpdir = tempfile.mkdtemp(prefix="bili_sub_")
        ydl_opts = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": [lang],
            "outtmpl": os.path.join(tmpdir, "%(id)s"),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "socket_timeout": 15,
        }
        # B站风控 412（Precondition Failed）：游客态 yt-dlp 抓页面会被拦，
        # 带 cookiefile（Netscape 格式）即可通过。cookie 来源：BILI_COOKIE 环境变量
        # （header 字符串，自动转 Netscape 写入 .cache/）或已存在的缓存文件。
        cookie_file = _bili_ydl_cookiefile()
        if cookie_file:
            ydl_opts["cookiefile"] = cookie_file
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                vinfo = ydl.extract_info(page_url, download=False)
                title2 = vinfo.get("title", "") or title or ""
                subs_found = [os.path.join(tmpdir, f) for f in os.listdir(tmpdir)
                              if f.endswith((".vtt", ".srv3", ".json3"))]
                segs2: List[Dict] = []
                for sub in subs_found:
                    with open(sub, "r", encoding="utf-8", errors="ignore") as fh:
                        segs2.extend(parse_vtt(fh.read()))
                for f in os.listdir(tmpdir):
                    try:
                        os.remove(os.path.join(tmpdir, f))
                    except Exception:
                        pass
                try:
                    os.rmdir(tmpdir)
                except Exception:
                    pass
                if segs2:
                    segs2 = preprocess_segments(segs2)  # B：字幕轻量清洗
                    print(f"   OK Bilibili 字幕获取成功（清洗后 {len(segs2)} 条，yt-dlp 兜底）")
                    return (title2, segs2, info.get("author", ""))
                print("   ℹ️ yt-dlp 也未拿到字幕")
        except Exception as e:
            print(f"   WARN yt-dlp 兜底失败（{e}）")
    else:
        print("   ℹ️ yt-dlp 未安装")
    return None


def fetch_bilibili_transcript(url: str, lang: str = "zh", page: int = None) -> Optional[Tuple[str, List[Dict], str]]:
    """获取 Bilibili 单集字幕（含 ASR 兜底的总入口，兼容旧调用方）。

    线性流程：先 fetch_subtitle_only（原生API→yt-dlp 字幕）；字幕缺失再 ASR 音频转写兜底。
    需要显式拆分「字幕/ASR」两步的场景（scripts/backfill_series.py 点名补齐）直接调
    fetch_subtitle_only + asr.transcribe_video，不走本函数——避免 ASR 藏在内部分支里。
    """
    sub = fetch_subtitle_only(url, lang=lang, page=page)
    if sub:
        return sub
    # 充电专属（仅试看）：**不阻断 ASR**（2026-09-20 用户定策）——「充电」限制媒体流，
    # 不代表字幕就一定拿不到；既然字幕这条路已经空手，就交给 ASR + 音频完整性校验裁决：
    # 拿到的音频若只覆盖视频 <90%（试看片段的典型形态），videos/asr.py::transcribe_video
    # 会直接拒绝转写，不会产出残缺笔记。这里只把原因打清楚，便于事后定位。
    if is_bilibili(url) and LAST_CHARGING_EXCLUSIVE:
        print("   ℹ️ 该集为充电专属（当前账号仅可试看）且无字幕：转 ASR 试取音频，"
              "由完整性校验（覆盖率 ≥90%）决定是否收录。")
    # 风控熔断（批量场景 BILI_FAILFAST_412=1）：412 命中后不再下载音频做 ASR
    if _risk_412_failfast():
        print("   STOP 风控412命中，跳过 ASR 兜底")
        return None
    # 批量补齐：跳过 ASR 音频转写（避免音频下载 412 / 长耗时），
    # 无字幕视频留待后续单独 ASR 处理（BILI_BATCH_NO_ASR=1）。
    if os.environ.get("BILI_BATCH_NO_ASR") == "1":
        print("   SKIP 批量模式跳过 ASR 兜底（无 AI 字幕，留待后续 ASR 单独处理）")
        return None
    # 字幕完全缺失 → ASR 兜底（保留旧行为，供非显式编排的调用方使用）
    try:
        from videos.asr import transcribe_video, check_asr_deps
        # 优化 F：依赖预检——缺依赖打印一行安装命令，别让 ASR 静默崩
        ok, missing = check_asr_deps()
        if not ok:
            print(f"   FAIL ASR 依赖缺失（{', '.join(missing)}），跳过 ASR 兜底。"
                  f"安装：pip install {' '.join(missing)}")
            return None
        print("   WARN 该分P无字幕，尝试 ASR 音频转写兜底")
        r = transcribe_video(url, lang=lang)
        if r:
            title3, payload, author3 = r
            text3 = payload if isinstance(payload, str) else "\n".join(
                s.get("text", "") for s in payload)
            if text3 and text3.strip():
                segs3 = [{"start": 0.0, "duration": 0.0, "text": text3}]
                print(f"   OK Bilibili ASR 转写成功（{len(text3)} 字）")
                return (title3 or title, segs3, author3 or "")
            print("   ℹ️ ASR 返回空文本，放弃")
    except Exception as e:
        print(f"   FAIL Bilibili ASR 兜底也失败: {e}")
    return None


# ---------------------------------------------------------------------------
# 统一入口 + playlist
# ---------------------------------------------------------------------------

def fetch_transcript(url: str) -> Optional[Tuple[str, List[Dict], str]]:
    """根据 URL 自动分发到对应平台字幕获取。

    Returns: (title, segments, author) 或 None
    """
    if is_youtube(url):
        r = fetch_youtube_transcript(url)
        if r is None:
            return None
        t, s = r
        return (t, s, "")
    if is_bilibili(url):
        return fetch_bilibili_transcript(url)
    print("❌ 暂不支持该平台链接（仅支持 YouTube / Bilibili；其他平台请本地文件 → ASR）")
    return None


def fetch_playlist(url: str, limit: Optional[int] = None) -> List[Dict[str, str]]:
    """列出 playlist / 合集 / 分P 的视频条目。

    Returns:
        [{'url':..., 'title':...}, ...]（按 limit 截断）
    """
    try:
        import yt_dlp
    except ImportError:
        print("⚠️ 未安装 yt-dlp（pip install yt-dlp）")
        return []
    ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "flat_playlist": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            entries = info.get("entries") or []
            results = []
            for e in entries:
                if not e:
                    continue
                eid = e.get("id")
                eurl = e.get("url")
                if not eurl and eid:
                    eurl = f"https://www.youtube.com/watch?v={eid}"
                if not eurl and is_bilibili(url):
                    eurl = f"https://www.bilibili.com/video/{eid}"
                if eurl:
                    results.append({"url": eurl, "title": e.get("title", "")})
            if limit:
                results = results[:limit]
            print(f"   ✅ playlist 解析到 {len(results)} 个条目")
            return results
    except Exception as e:
        print(f"❌ playlist 解析失败: {e}")
        return []
