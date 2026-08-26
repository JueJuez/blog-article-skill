"""shared/fetch_title.py — 通过原文链接取真实标题（og:title）。

用途（用户 2026-08-25）：飞书节点名 / 正文 H1 都不可信——
节点名可能是抓取故障（寒暄首句、未命名*），H1 是模型自创的总结标题。
唯一可靠来源是原文页面 <meta property="og:title">。

验证事实：
- 公众号 mp.weixin.qq.com/s/<id> 直接 GET 返回 200，og:title 即真实文章标题。
- B站 bilibili.com/video/BVxxx 的 og:title = "视频标题 - 哔哩哔哩"，需剥站点后缀。
- 生财 scys.com 走登录墙，og:title 取不到 → 调用方回退到展示原文链接人工核。
"""
import re
import sys
import time
import requests
from pathlib import Path

# 让本模块能 import scripts/ 下的 CDP 工具（与 scys_batch_fetch.py 同款做法）
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

_TIMEOUT = 20

# 站点后缀：og:title 常带 " - 哔哩哔哩" / " - 微博" 之类，落盘标题不需要。
_SITE_SUFFIX_RE = re.compile(
    r"\s*[-_]\s*(哔哩哔哩|bilibili|微博|weibo|知乎|微信|微信公众号|小红书|今日头条|掘金)\s*$",
    re.IGNORECASE,
)


def _clean(title: str) -> str:
    t = (title or "").strip()
    t = re.sub(r"\s+", " ", t)
    t = _SITE_SUFFIX_RE.sub("", t).strip()
    # 飞书节点标题非法字符（与 title_norm 一致，避免建议标题又触发清洗）
    t = t.replace("<", "〈").replace(">", "〉").replace('"', "“").replace("'", "’")
    return t


def fetch_og_title(url: str) -> str:
    """取原文页面真实标题（og:title）；失败返回空串。

    调用方应传入 source_url（正文里的原文链接）。对登录墙站点（scys）
    会返回空，调用方需回退到展示原文链接人工核。
    """
    if not url or not url.startswith("http"):
        return ""
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT, allow_redirects=True)
    except Exception:
        return ""
    if r.status_code != 200:
        return ""
    html = r.text
    for pat in (
        r'<meta\s+property=["\']og:title["\']\s+content=["\'](.*?)["\']',
        r'<meta\s+content=["\'](.*?)["\']\s+property=["\']og:title["\']',
        r'<title[^>]*>(.*?)</title>',
    ):
        m = re.search(pat, html, re.S | re.I)
        if m:
            t = _clean(m.group(1))
            if t:
                return t
    return ""


# ── B站：官方 API 取真标题（确定性，不靠网页 og:title 刮，规避反爬） ──
_BV_RE = re.compile(r"(BV[0-9A-Za-z]+)")


def _extract_bvid(url: str) -> str:
    m = _BV_RE.search(url or "")
    if m:
        return m.group(1)
    # b23.tv 短链需先解析重定向
    if "b23.tv" in (url or ""):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT, allow_redirects=True)
            m = _BV_RE.search(r.url)
            if m:
                return m.group(1)
        except Exception:
            pass
    return ""


def fetch_bilibili_title(url: str) -> str:
    """用 B站官方 view 接口取视频真标题（按 BV 号，确定性、机械、无反爬）。"""
    bvid = _extract_bvid(url)
    if not bvid:
        return ""
    try:
        r = requests.get(
            f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
            headers={**_HEADERS, "Referer": "https://www.bilibili.com"},
            timeout=_TIMEOUT,
        )
        d = r.json()
        if d.get("code") == 0:
            return _clean((d.get("data") or {}).get("title", "") or "")
    except Exception:
        pass
    return ""


def titles_differ(a: str, b: str) -> bool:
    """归一化比较两个标题是否明显不同（忽略日期后缀/标点/空格）。"""
    def norm(s: str) -> str:
        s = (s or "").lower()
        s = re.sub(r"[-_]\s*\d{6,8}", "", s)        # 剥 -20260310 日期后缀
        s = re.sub(r"[-_]\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}", "", s)
        s = re.sub(r"[\s\-_，。、：:.,!！?？“”\"'‘’()（）\[\]【】]", "", s)
        return s
    return norm(a) != norm(b)


# ── 域名路由 ───────────────────────────────────────────────
def _is_wechat(url: str) -> bool:
    return bool(url) and "mp.weixin.qq.com" in url

def _is_bilibili(url: str) -> bool:
    return bool(url) and ("bilibili.com" in url or "b23.tv" in url)

def _is_scys(url: str) -> bool:
    return bool(url) and "scys.com" in url


# 公众号单会话：直接复用共享 CDP 会话（不再各自实现开/关浏览器）
from shared.cdp_session import SharedCdpSession


class WeChatCdpSession(SharedCdpSession):
    """公众号单会话（继承 shared.cdp_session.SharedCdpSession）。

    保持原类名以兼容 rename_list.py 等调用方；浏览器生命周期已上移到共享模块，
    公众号 与 scys 共用同一套「只开关一次」逻辑。
    """
    pass


def fetch_wechat_title_cdp(url: str) -> str:
    """单篇公众号原文标题（CDP 登录态，开一个临时会话）。批量请用 WeChatCdpSession。"""
    with WeChatCdpSession() as s:
        return s.get_title(url)


def fetch_real_title(url: str) -> str:
    """按平台取原文真标题（统一入口）。

    优先级（用户 2026-08-26 决策：微信优先 CDP，不先走代理）：
    - 公众号 mp.weixin.qq.com → CDP 登录态抓原文页（首选），失败回退裸 og:title
    - B站 → 裸 og:title（B站公开，稳定）
    - 生财 scys → 登录墙，用户确认当前标题对 → 返回空（调用方保持现状/人工）
    """
    if not url or not url.startswith("http"):
        return ""
    if _is_bilibili(url):
        return fetch_bilibili_title(url)   # 官方 API，确定性，不靠网页 og:title
    if _is_scys(url):
        return ""  # 生财用户确认当前标题正确，不动
    if _is_wechat(url):
        t = fetch_wechat_title_cdp(url)
        if t:
            return t
        return fetch_og_title(url)  # CDP 不可用时的兜底
    return fetch_og_title(url)
