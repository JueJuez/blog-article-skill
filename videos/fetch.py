"""videos/fetch.py — 视频字幕获取层（P2.1 获取层，架构解耦）

职责（PRD 架构约束 1：获取层与总结层解耦）：只负责"拿到字幕"，不负责总结。
- YouTube：youtube-transcript-api 拿 CC 字幕
- Bilibili：yt-dlp 抽 CC/自动字幕（.vtt）→ 解析为片段
- playlist / 分P：yt-dlp 列出 entries，逐条 URL 交给上层迭代

所有外部依赖缺失或网络失败均优雅返回 None，绝不抛异常阻断主流程。
"""

import os
import re
import tempfile
import subprocess
from typing import Optional, List, Dict, Tuple

YT_RE = re.compile(r"(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)([\w-]{11})")
BILI_RE = re.compile(r"(?:bilibili\.com/video/|b23\.tv/)([BV][A-Za-z0-9]+)")


def is_youtube(url: str) -> bool:
    return bool(YT_RE.search(url or ""))


def is_bilibili(url: str) -> bool:
    return bool(BILI_RE.search(url or ""))


def _yt_video_id(url: str) -> Optional[str]:
    m = YT_RE.search(url or "")
    return m.group(1) if m else None


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

def fetch_youtube_transcript(url: str, languages: Tuple[str, ...] = ("zh-Hans", "zh", "en")) -> Optional[Tuple[str, List[Dict]]]:
    """获取 YouTube 视频 CC 字幕。

    Returns:
        (title, segments) 或 None
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        print("⚠️ 未安装 youtube-transcript-api（pip install youtube-transcript-api）")
        return None

    vid = _yt_video_id(url)
    if not vid:
        print("❌ 无法从 URL 解析 YouTube 视频 ID")
        return None

    try:
        # v1.x API：实例方法 fetch() 返回 FetchedTranscript；.to_raw_data()
        # 还原为旧的 [{"text","start","duration"}] 列表，下方解析逻辑无需改动。
        # 代理：底层 requests 自动读取系统 HTTP(S)_PROXY。若设置了 YT_PROXY，
        # 则映射到 HTTP(S)_PROXY 让请求走代理（仅本地有可复用端口时有意义，
        # 如开启 Clash 系统代理；纯浏览器插件代理无本地端口则仍需 CDP 方案）。
        _apply_yt_proxy_env()
        api = YouTubeTranscriptApi()
        fetched = api.fetch(vid, languages=list(languages))
        raw = fetched.to_raw_data()
        segments = [{"text": s.get("text", ""), "start": s.get("start", 0.0),
                     "duration": s.get("duration", 0.0)} for s in raw]
        if not segments:
            print("❌ 该视频无可用 CC 字幕")
            return None
        title = _yt_title(url) or vid
        print(f"   ✅ YouTube 字幕获取成功（{len(segments)} 条）")
        return (title, segments)
    except Exception as e:
        print(f"❌ YouTube 字幕获取失败: {e}")
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


def _bili_get_video_info(bvid: str) -> Optional[Dict]:
    import json as _j, urllib.request as _req
    api = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    r = _req.Request(api, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com/",
    })
    try:
        resp = _req.urlopen(r, timeout=15)
        data = _j.loads(resp.read())
        if data.get("code") == 0:
            d = data["data"]
            return {"aid": d["aid"], "cid": d["cid"], "title": d.get("title", "")}
    except Exception:
        pass
    return None


def _bili_get_subtitle_list(aid: int, cid: int) -> Optional[List[Dict]]:
    import json as _j, urllib.request as _req
    url = f"https://api.bilibili.com/x/v2/dm/view?aid={aid}&oid={cid}&type=1"
    r = _req.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com/",
    })
    try:
        resp = _req.urlopen(r, timeout=15)
        data = _j.loads(resp.read())
        subs = data.get("data", {}).get("subtitle", {}).get("subtitles", [])
        if subs:
            return subs
    except Exception:
        pass
    return None


def _bili_download_subtitle_body(subtitle_url: str, cookies: str = None) -> Optional[List[Dict]]:
    """下载字幕正文。

    subtitle_url 来自 dm/view API 返回的完整 CDN 地址（含 prod/ 前缀与 auth_key
    查询参数）。该 URL 自带签名、无需登录即可访问；自行拼装 URL 会因缺少
    auth_key 而 403。cookies 参数保留以备未来签名过期 Scenario。
    """
    import json as _j, urllib.request as _req
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com/",
    }
    if cookies:
        hdrs["Cookie"] = cookies
    # http(s) 兼容：CDN 返回的是 http 地址，直接复用即可
    url = subtitle_url
    r = _req.Request(url, headers=hdrs)
    try:
        resp = _req.urlopen(r, timeout=20)
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
    import json as _j, urllib.request as _req
    if not cookie_str:
        return False
    url = "https://api.bilibili.com/x/web-interface/nav"
    r = _req.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com/",
        "Cookie": cookie_str,
    })
    try:
        resp = _req.urlopen(r, timeout=15)
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


def _bili_auto_extract_cookies() -> Optional[str]:
    """用 Playwright 挂载本机 Chrome *真实* 用户配置，读取其中已登录的 B 站 cookie。

    为什么必须挂载真实配置、而不是直接读库：
    Chrome 127+ 启用了 App-Bound Encryption，cookie 明文只能由 Chrome 自身解密
    （通过 CDP）。直接读 Cookies SQLite 库会因密钥受应用绑定保护而解密失败；
    复制 profile 再读则会被 Chrome 当作新配置重新加密、旧 cookie 全部丢弃。

    前置条件：本机 Chrome 必须**完全退出**（含后台进程）。若仍在后台运行，
    配置目录被锁，挂载会失败并返回 None。成功后写入 .cache 缓存，后续复用。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("   ℹ️ 未安装 playwright（pip install playwright && playwright install chromium），跳过自动提取")
        return None

    chrome_user_data = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")
    if not os.path.isdir(chrome_user_data):
        print("   ℹ️ 未检测到本机 Chrome 用户目录，跳过自动提取")
        return None

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=chrome_user_data,
                headless=True,
                channel="chrome",
                args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
                timeout=20000,
            )
            try:
                cookies = ctx.cookies("https://www.bilibili.com")
            finally:
                ctx.close()
        if not cookies:
            return None
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        _bili_cache_cookies(cookie_str)
        print(f"   ✅ 已自动从本机 Chrome 提取 B 站 cookie（{len(cookies)} 项，已缓存到 .cache）")
        return cookie_str
    except Exception as e:
        print("   ℹ️ 自动提取 B 站 cookie 失败：本机 Chrome 可能仍在后台运行（配置被锁）。")
        print("      → 请完全退出 Chrome（任务栏右键图标→退出，或任务管理器结束 chrome.exe）后重试；")
        print("        首次成功后 cookie 会缓存到 .cache，之后无需再开 Chrome。")
        return None


def _bili_build_cookies_from_env() -> Optional[str]:
    """返回 B 站 cookie 头字符串，按优先级：
    1. 环境变量 BILIBILI_COOKIES（完整 cookie 串）
    2. 环境变量 SESSDATA / BILIBILI_SESSDATA
    3. 本地缓存（.cache/bilibili_cookies.txt）
    4. 自动从本机 Chrome 提取（Playwright，仅首次，会缓存）
    """
    full = os.environ.get("BILIBILI_COOKIES", "")
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
    return _bili_auto_extract_cookies()


def fetch_bilibili_transcript(url: str, lang: str = "zh") -> Optional[Tuple[str, List[Dict]]]:
    """获取 Bilibili 视频字幕（AI 自动生成 + 用户 CC）。

    策略（按优先级）：
    1. 原生 API 链路（view -> dm/view -> aisubtitle），支持 SESSDATA/BILIBILI_COOKIES 认证
    2. yt-dlp 兜底（当用户配置了 cookies-from-browser / --cookies 时）
    """
    bvid = _bili_extract_bvid(url)
    if not bvid:
        print("无法从 URL 提取 Bilibili BV 号")
        return None

    info = _bili_get_video_info(bvid)
    if info:
        aid, cid, title = info["aid"], info["cid"], info["title"]
        sub_list = _bili_get_subtitle_list(aid, cid)
        if sub_list:
            lang_priority = [f"ai-{lang}", lang, "ai-zh", "ai-en", ""]
            chosen = None
            for lp in lang_priority:
                matches = [s for s in sub_list if s.get("lan") == lp]
                if matches:
                    chosen = matches[0]
                    break
            if not chosen and sub_list:
                chosen = sub_list[0]

            if chosen:
                # dm/view 已返回带 auth_key 的完整 CDN 地址，无需登录即可直链下载
                sub_url = chosen.get("subtitle_url")
                if not sub_url:
                    print("   ⚠️ dm/view 未返回 subtitle_url，降级使用 cookie 认证重拼 URL")
                    cookies = _bili_build_cookies_from_env()
                    segs = _bili_download_subtitle_body(
                        f"https://aisubtitle.hdslb.com/bfs/ai_subtitle/{aid}/{cid}/{chosen['id']}.json",
                        cookies=cookies,
                    )
                else:
                    segs = _bili_download_subtitle_body(sub_url)
                if segs:
                    print(f"   OK Bilibili 字幕获取成功（{len(segs)} 条，API 原生链路）")
                    return (title, segs)
                else:
                    print("   ⚠️ Bilibili 有字幕但下载失败（auth_key 可能已过期，重试或提供 cookie）。")
        else:
            print("   WARN 该视频无 AI 字幕")

    # fallback: yt-dlp
    try:
        import yt_dlp
    except ImportError:
        print("   FAIL 未安装 yt-dlp 且原生 API 也未成功")
        return None

    tmpdir = tempfile.mkdtemp(prefix="bili_sub_")
    outtmpl = os.path.join(tmpdir, "%(id)s")
    ydl_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [lang],
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 15,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            vinfo = ydl.extract_info(url, download=False)
            title2 = vinfo.get("title", "") or title or ""
            subs_found = []
            for f in os.listdir(tmpdir):
                if f.endswith((".vtt", ".srv3", ".json3")):
                    subs_found.append(os.path.join(tmpdir, f))
            if not subs_found:
                print("   FAIL Bilibili 字幕获取失败（所有方式均未拿到字幕）")
                return None
            segs: List[Dict] = []
            for sub in subs_found:
                with open(sub, "r", encoding="utf-8", errors="ignore") as fh:
                    segs.extend(parse_vtt(fh.read()))
            for f in os.listdir(tmpdir):
                try:
                    os.remove(os.path.join(tmpdir, f))
                except Exception:
                    pass
            try:
                os.rmdir(tmpdir)
            except Exception:
                pass
            if not segs:
                print("   FAIL Bilibili 字幕解析后为空")
                return None
            print(f"   OK Bilibili 字幕获取成功（{len(segs)} 条，yt-dlp 兜底）")
            return (title2, segs)
    except Exception as e:
        print(f"   FAIL Bilibili yt-dlp 兜底也失败: {e}")
        return None

# ---------------------------------------------------------------------------
# 统一入口 + playlist
# ---------------------------------------------------------------------------

def fetch_transcript(url: str) -> Optional[Tuple[str, List[Dict]]]:
    """根据 URL 自动分发到对应平台字幕获取。"""
    if is_youtube(url):
        return fetch_youtube_transcript(url)
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
