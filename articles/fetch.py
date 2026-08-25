"""articles/fetch.py — 网页正文抓取（A1 升级）

抓取层升级：用 trafilatura（主力）提取干净正文，readability-lxml（次选），
原 bs4 多选择器逻辑作为兜底。自动剥离导航/广告/侧栏，提升笔记质量。

保留原有标题特殊处理（sina/baijiahao/og:title 等）。库缺失时自动降级到下一层，
绝不因某个解析库不可用而整体失败。
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# 中国时区（文章发布时间多为北京时间；naive 日期统一按 +08:00 解释，避免依赖系统 TZ）
_CHINA_TZ = timezone(timedelta(hours=8))


# 微信扫码墙 / 反爬页的 UI 特征文本。这些只出现在未登录态的墙页，绝不会出现在真实正文里。
# trafilatura 等抽取器可能把墙页的 UI 壳文本（如「微信扫一扫可打开此内容」）当成正文抽出，
# 其长度常 > 100 字阈值，从而骗过长度闸门。命中任一标记即判为「无真实正文」。
_WALL_MARKERS = (
    "微信扫一扫可打开此内容",   # 典型扫码墙提示
    "使用完整服务",             # 扫码墙副提示
    "该内容已被发布者删除",
    "访问过于频繁",
    "请输入验证码",
    "环境异常，请稍后",
    "此内容因违规无法查看",
)


def _looks_like_wall(content: str) -> bool:
    """抽取到的文本若只是微信墙/反爬页的 UI 文本（无真实正文），返回 True。"""
    if not content:
        return False
    return any(m in content for m in _WALL_MARKERS)


def is_scys_url(url: str) -> bool:
    """scys（生财有术）付费站链接需要 CDP 登录态抓取，走独立分流。"""
    return isinstance(url, str) and url.startswith("https://scys.com/")


def _scys_cdp_fetch(url: str, out_path=None, **kwargs) -> dict:
    """接管用户主 Chrome 抓 scys 正文（scripts/login_cdp_fetch.fetch 的薄包装）。"""
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from login_cdp_fetch import fetch as cdp_fetch

    if out_path is None:
        out_path = (Path(__file__).resolve().parent.parent / "notes" / "_scraped"
                    / "scys_single" / "latest.md")
    return cdp_fetch(url, Path(out_path), **kwargs)



# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------

def _download(url: str):
    """下载网页，返回 (html_str, response) 或 (None, None)。"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            return None, None
        # 编码处理（保留 sina/baijiahao 特例）
        if 'sina' in url.lower():
            response.encoding = 'utf-8'
        elif 'baijiahao' in url.lower():
            response.encoding = 'utf-8'
        elif response.encoding and response.encoding.lower() not in ('iso-8859-1',):
            pass
        else:
            response.encoding = response.apparent_encoding or 'utf-8'
        return response.text, response
    except Exception as e:
        print(f"❌ 下载网页失败: {str(e)}")
        return None, None


# ---------------------------------------------------------------------------
# 各提取层
# ---------------------------------------------------------------------------

def _extract_trafilatura(html: str, url: str = "") -> str:
    """trafilatura 主力提取干净正文。"""
    try:
        import trafilatura
        kwargs = {"url": url} if url else {}
        extracted = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            **kwargs,
        )
        if extracted and len(extracted.strip()) >= 100:
            return extracted.strip()
    except Exception as e:
        print(f"   ℹ️ trafilatura 提取失败，尝试下一层: {e}")
    return ""


def _extract_readability(html: str) -> str:
    """readability-lxml 次选提取。库未安装时返回空。"""
    try:
        from readability import Document
        doc = Document(html)
        summary = doc.summary()
        soup = BeautifulSoup(summary, 'html.parser')
        text = soup.get_text(separator='\n', strip=True)
        if text and len(text) >= 100:
            return text
    except ImportError:
        pass
    except Exception as e:
        print(f"   ℹ️ readability 提取失败，尝试下一层: {e}")
    return ""


_BS4_SELECTORS = [
    'article', '.article-content', '.content', '.main-content',
    '.post-content', '#article', '.article-body', '.bd',
]


def _extract_bs4_content(soup: BeautifulSoup) -> str:
    """原 bs4 多选择器兜底。"""
    for selector in _BS4_SELECTORS:
        article = soup.select_one(selector)
        if article:
            content = article.get_text(separator='\n', strip=True)
            if content and len(content) >= 100:
                return content
    paragraphs = soup.find_all('p')
    content = '\n\n'.join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
    return content


# ---------------------------------------------------------------------------
# 标题提取（保留原特殊逻辑）
# ---------------------------------------------------------------------------

def _extract_title(soup: BeautifulSoup, url: str = "") -> str:
    # 1. og:title
    meta_title = soup.find('meta', property='og:title')
    if meta_title and meta_title.get('content'):
        t = meta_title['content'].strip()
        if len(t) > 5:
            return t

    # 2. 新浪 main-title
    main_title_tag = soup.select_one('.main-title')
    if main_title_tag:
        t = main_title_tag.get_text(strip=True)
        if len(t) > 10:
            return t

    # 3. 所有 h1（排除栏目名）
    for h1_tag in soup.find_all('h1'):
        text = h1_tag.get_text(strip=True)
        if text and len(text) > 10 and '新浪看点' not in text and '栏目' not in text:
            return text

    # 4. 其他标题选择器
    for selector in ['.article-title', 'h1.article-title', 'div.article-title',
                     '.article-main-title', '#title']:
        tag = soup.select_one(selector)
        if tag:
            text = tag.get_text(strip=True)
            if text and len(text) > 10:
                return text

    # 5. title 标签
    if soup.title:
        t = soup.title.string or ""
        t = t.replace('|新浪新闻', '').replace('-新浪新闻', '').strip()
        if t:
            return t
    return ""


def _trafilatura_title(html: str) -> str:
    try:
        import trafilatura
        from trafilatura.metadata import extract_metadata
        meta = extract_metadata(html)
        if meta and getattr(meta, "title", None):
            t = meta.title.strip()
            if len(t) > 3:
                return t
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# 发布时间提取（A 系列增强：散文单篇也用「文章发布时间」命名文件名）
# ---------------------------------------------------------------------------

def _parse_date_to_epoch(s: str) -> int:
    """把常见日期字符串解析成 epoch 秒；解析失败返回 0。

    支持：ISO 8601（含 +08:00 / Z / 无时区）、`2026-08-25 13:00:00`、
    中文 `2026年08月25日`。naive 日期按北京时间（+08:00）解释，
    使结果不依赖运行机器的系统时区。
    """
    if not s:
        return 0
    s = s.strip()
    # 中文「年/月/日」归一为 ISO 日期
    m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', s)
    if m:
        s = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # 截取第一个像日期的片段（去掉末尾 "(UTC)" / "GMT" 之类的尾巴）
    m = re.search(
        r'(\d{4}-\d{2}-\d{2}'
        r'(?:[ T]\d{2}:\d{2}(?::\d{2})?'
        r'(?:[Zz]|[+\-]\d{2}:?\d{2})?)?)',
        s,
    )
    if not m:
        return 0
    ds = (m.group(1)
          .replace(' ', 'T')
          .replace('Z', '+00:00')
          .replace('+0800', '+08:00')
          .replace('+0000', '+00:00'))
    try:
        dt = datetime.fromisoformat(ds)
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_CHINA_TZ)
    return int(dt.timestamp())


def _extract_publish_time(soup: BeautifulSoup, html: str = "") -> int:
    """从 HTML 提取文章原始发布时间（epoch 秒）。多源候选，取第一个能解析的。

    候选顺序：meta(article:published_time / og:published_time / datePublished 等)
    → itemprop=datePublished → <time datetime> → JSON-LD 的 datePublished。
    """
    candidates = []
    for prop in ("article:published_time", "article:published",
                 "og:article:published_time", "og:published_time",
                 "datePublished", "publishDate", "pubdate", "publishdate"):
        tag = (soup.find("meta", attrs={"property": prop})
               or soup.find("meta", attrs={"name": prop}))
        if tag and tag.get("content"):
            candidates.append(tag["content"].strip())
    tag = soup.find(attrs={"itemprop": "datePublished"})
    if tag and tag.get("content"):
        candidates.append(tag["content"].strip())
    for t in soup.find_all("time"):
        if t.get("datetime"):
            candidates.append(t["datetime"].strip())
    # JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        for it in (data if isinstance(data, list) else [data]):
            if isinstance(it, dict):
                dp = it.get("datePublished") or it.get("dateCreated") or it.get("uploadDate")
                if dp:
                    candidates.append(str(dp).strip())
    for c in candidates:
        epoch = _parse_date_to_epoch(c)
        if epoch:
            return epoch
    return 0


# ---------------------------------------------------------------------------
# 对外主函数
# ---------------------------------------------------------------------------

def fetch_web_content(url: str):
    """获取网页内容（增强版）。

    scys.com 链接自动分流到 CDP 登录态抓取（用户主 Chrome 需启 debug 端口），
    与普通文章共用同一条总结管道，前端无感。

    Returns:
        tuple: (title, content, publish_time) 或 None
            - publish_time: 文章原始发布时间（epoch 秒，从页面 meta/JSON-LD 提取），
              提取不到为 0。散文单篇据此命名文件名；监控/补齐用各自 feed 的发布时间，
              不会被此处覆盖。
    """
    if is_scys_url(url):
        print("🔐 检测到 scys（生财有术）链接 → 走 CDP 登录态抓取")
        try:
            result = _scys_cdp_fetch(url)
        except Exception as e:
            print(f"❌ scys CDP 抓取失败: {e}")
            print("💡 login_cdp_fetch 会自动回退到 profile_clone_fetch（见 references/scys-fetch-sop.md）")
            return None
        body = (Path(result["output"]).read_text(encoding="utf-8")
                if Path(result["output"]).exists() else "")
        if result.get("login_wall_hit"):
            print(f"❌ 撞登录墙: {result['login_wall_hit']}")
            return None
        if len(body.strip()) < 100:
            print("❌ scys 抓取正文过短，视为失败")
            return None
        return (result["title"], body, 0)

    html, response = _download(url)
    if not html:
        print("❌ 抓取失败：无法访问链接")
        print("💡 请手动复制全文原文发送，我将继续整理总结")
        return None

    soup = BeautifulSoup(html, 'html.parser')

    # 正文：trafilatura → readability → bs4
    content = (
        _extract_trafilatura(html, url)
        or _extract_readability(html)
        or _extract_bs4_content(soup)
    )

    # 墙文/反爬页：trafilatura 等可能抽到扫码墙 UI 壳文本（如「微信扫一扫可打开此内容」），
    # 其长度常 > 100 阈值，但毫无正文价值。命中墙标记即判抓取失败，交给上层重试/移除逻辑。
    if content and _looks_like_wall(content):
        print("❌ 抓取疑似微信扫码墙/反爬页（无真实正文），视为抓取失败")
        print("💡 请手动复制全文原文发送，我将继续整理总结")
        return None

    if not content or len(content.strip()) < 100:
        print("❌ 抓取的正文过短（少于100字），视为抓取失败")
        print("💡 请手动复制全文原文发送，我将继续整理总结")
        return None

    # 标题：bs4 优先（保留特例），无则 trafilatura 元数据
    title = _extract_title(soup, url) or _trafilatura_title(html) or "未命名文章"

    # 发布时间：从页面 meta/JSON-LD 提取（散文单篇据此命名文件名）
    publish_time = _extract_publish_time(soup, html)

    return (title, content, publish_time)


def async_fetch_web_content(url: str):
    """兼容旧调用：同步返回（异步路径在 main.py 中另作处理）。"""
    return fetch_web_content(url)
