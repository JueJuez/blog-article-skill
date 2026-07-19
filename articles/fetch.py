"""articles/fetch.py — 网页正文抓取（A1 升级）

抓取层升级：用 trafilatura（主力）提取干净正文，readability-lxml（次选），
原 bs4 多选择器逻辑作为兜底。自动剥离导航/广告/侧栏，提升笔记质量。

保留原有标题特殊处理（sina/baijiahao/og:title 等）。库缺失时自动降级到下一层，
绝不因某个解析库不可用而整体失败。
"""

import os
import re
import requests
from bs4 import BeautifulSoup


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
# 对外主函数
# ---------------------------------------------------------------------------

def fetch_web_content(url: str):
    """获取网页内容（增强版）。

    Returns:
        tuple: (title, content) 或 None
    """
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

    if not content or len(content.strip()) < 100:
        print("❌ 抓取的正文过短（少于100字），视为抓取失败")
        print("💡 请手动复制全文原文发送，我将继续整理总结")
        return None

    # 标题：bs4 优先（保留特例），无则 trafilatura 元数据
    title = _extract_title(soup, url) or _trafilatura_title(html) or "未命名文章"

    return (title, content)


def async_fetch_web_content(url: str):
    """兼容旧调用：同步返回（异步路径在 main.py 中另作处理）。"""
    return fetch_web_content(url)
