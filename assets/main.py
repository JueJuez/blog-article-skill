import os
import re
import asyncio
from datetime import datetime
from .prompt import format_note_with_prompt, CONTENT_SUMMARY_PROMPT
from .manager import OutputManager


NOTES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'notes')


def _ensure_notes_dir() -> str:
    os.makedirs(NOTES_DIR, exist_ok=True)
    return NOTES_DIR


def fetch_web_content(url: str):
    """获取网页内容（模拟WebFetch功能）
    
    Returns:
        tuple: (title, content) 或 None
    """
    try:
        import requests
        from bs4 import BeautifulSoup
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=30)
        
        # 优先使用响应头中的编码，如果没有则使用自动检测
        if response.encoding:
            response.encoding = response.encoding
        else:
            response.encoding = response.apparent_encoding
        
        # 新浪网站特殊处理：确保使用正确的编码
        if 'sina' in url.lower():
            response.encoding = 'utf-8'
        
        # 百度百家号特殊处理：经常返回错误的编码，强制使用UTF-8
        if 'baijiahao' in url.lower():
            response.encoding = 'utf-8'
        
        # 使用content而非text，避免requests自动解码可能导致的问题
        soup = BeautifulSoup(response.content, 'html.parser', from_encoding=response.encoding)
        
        # 提取文章标题（尝试多个选择器）
        title = ""
        
        # 优先尝试 og:title（最可靠）
        meta_title = soup.find('meta', property='og:title')
        if meta_title and meta_title.get('content'):
            title = meta_title['content'].strip()
        
        # 如果 og:title 没有，尝试 main-title 类（新浪文章常用）
        if not title:
            main_title_tag = soup.select_one('.main-title')
            if main_title_tag:
                title = main_title_tag.get_text(strip=True)
        
        # 如果还没找到，尝试所有 h1 标签，排除栏目名称
        if not title:
            for h1_tag in soup.find_all('h1'):
                text = h1_tag.get_text(strip=True)
                if text and len(text) > 10 and '新浪看点' not in text and '栏目' not in text:
                    title = text
                    break
        
        # 如果还没找到，尝试其他标题选择器
        if not title:
            title_selectors = [
                '.article-title',
                'h1.article-title',
                'div.article-title',
                '.article-main-title',
                '#title'
            ]
            for selector in title_selectors:
                tag = soup.select_one(selector)
                if tag:
                    text = tag.get_text(strip=True)
                    if text and len(text) > 10:
                        title = text
                        break
        
        # 最后尝试 title 标签
        if not title and soup.title:
            title = soup.title.string or ""
            # 清理 title 标签中的网站名称
            title = title.replace('|新浪新闻', '').replace('-新浪新闻', '').strip()
        
        # 提取文章正文（适配多种博客格式）
        content = ""
        selectors = [
            'article',
            '.article-content',
            '.content',
            '.main-content',
            '.post-content',
            '#article',
            '.article-body',
            '.bd'
        ]
        
        for selector in selectors:
            article = soup.select_one(selector)
            if article:
                content = article.get_text(separator='\n', strip=True)
                break
        
        if not content:
            # 如果没有找到特定标签，提取所有段落
            paragraphs = soup.find_all('p')
            content = '\n\n'.join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
        
        if not content:
            print("❌ 抓取的内容为空，请检查链接是否有效")
            print("💡 请手动复制全文原文发送，我将继续整理总结")
            return None
        
        # 检查内容长度，少于100字视为抓取失败
        if len(content.strip()) < 100:
            print("❌ 抓取的正文过短（少于100字），视为抓取失败")
            print("💡 请手动复制全文原文发送，我将继续整理总结")
            return None
        
        return (title, content)
    except Exception as e:
        print(f"❌ 获取网页内容失败: {str(e)}")
        print("💡 请手动复制全文原文发送，我将继续整理总结")
        return None


def extract_article_title(content: str) -> str:
    """从内容中提取文章标题"""
    lines = content.split('\n')
    
    # 首先尝试从内容开头提取标题（适用于直接传入文章内容的情况）
    # 查找以"培训主题"、"标题"、"文章标题"等开头的行
    for line in lines[:10]:  # 只检查前10行
        line = line.strip()
        if line.startswith('培训主题') or line.startswith('标题'):
            match = re.search(r'(培训主题|标题)\s*[：:]?\s*(.+)', line)
            if match:
                title = match.group(2).strip()
                title = re.sub(r'[\\/:*?"<>|\n\r]', '_', title)
                return title[:50]
    
    # 查找以"## "开头的二级标题（可能包含文章标题）
    for line in lines[:20]:
        line = line.strip()
        if line.startswith('## '):
            title = line[3:].strip()
            if title and not title.startswith('本章') and not title.startswith('核心'):
                title = re.sub(r'[\\/:*?"<>|\n\r]', '_', title)
                return title[:50]
    
    # 优先查找一级标题（# 一、xxxx 或 # 二、xxxx 格式），跳过标签行（如 # AI #人工智能）
    for line in lines:
        line = line.strip()
        if line.startswith('# 一、') or line.startswith('# 二、') or line.startswith('# 三、') or line.startswith('# 四、'):
            # 提取标题内容
            title = line[2:].strip()  # 去掉 "# "
            if title.startswith('一、') or title.startswith('二、') or title.startswith('三、') or title.startswith('四、'):
                title = title[2:].strip()  # 去掉 "一、" 等序号
            # 清理标题中的特殊字符
            title = re.sub(r'[\\/:*?"<>|\n\r]', '_', title)
            return title[:50]
    
    # 如果没有找到一级标题，查找"核心定位"后面的内容作为标题
    for line in lines:
        line = line.strip()
        if line.startswith("**核心定位**") or line.startswith("核心定位"):
            # 提取核心定位后面的内容作为标题
            match = re.search(r'(核心定位\s*[：:]?)\s*(.+)', line)
            if match:
                title = match.group(2).strip()
                # 清理标题中的特殊字符
                title = re.sub(r'[\\/:*?"<>|\n\r]', '_', title)
                return title[:50]
    
    # 如果没有找到核心定位，查找其他合适的标题
    for line in lines:
        line = line.strip()
        if line and not line.startswith('#') and not line.startswith('-') and len(line) < 100:
            return line[:50].replace('/', '_').replace('\\', '_')
    
    return "文章总结"


def generate_filename(title: str, url: str = "", category: str = "") -> str:
    safe_title = re.sub(r'[\\/:*?"<>|\n\r]', '_', title)
    
    # 移除首尾空白字符
    safe_title = safe_title.strip()
    
    # 如果标题为空或过于简短，使用"未命名笔记-时间戳"格式（直接返回完整文件名）
    if not safe_title or len(safe_title) < 2:
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        return f"未命名笔记-{timestamp}.md"
    
    # 正常标题：【分类】标题-年月日.md
    date_str = datetime.now().strftime('%Y%m%d')
    if category:
        safe_category = re.sub(r'[\\/:*?"<>|\n\r]', '_', category).strip()
        filename = f"【{safe_category}】{safe_title[:50]}-{date_str}.md"
    else:
        filename = f"{safe_title[:50]}-{date_str}.md"
    
    return filename


def save_raw_content_to_file(content: str, title: str = "", prefix: str = "_raw_") -> str:
    """将原始文章内容保存到 notes/ 目录下的临时文件

    在降级流程中使用：当无外部 AI Provider 时自动将原文写入文件，
    供外层对话直接 Read 读取，避免终端截断问题。

    Args:
        content: 原始文章内容
        title: 文章标题（用于文件名）
        prefix: 文件名前缀

    Returns:
        str: 保存的文件绝对路径
    """
    notes_dir = _ensure_notes_dir()
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    safe_title = re.sub(r'[\\/:*?"<>|\n\r]', '_', title)[:30] if title else "no_title"
    filename = f"{prefix}{safe_title}-{timestamp}.md"
    filepath = os.path.join(notes_dir, filename)

    header = f"> 原始文章内容（自动暂存）\n> 标题：{title}\n> 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n\n"
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(header + content)

    return os.path.abspath(filepath)


def save_summarized_from_file(filepath: str, original_url: str = "", author: str = "", tags: list = None, original_title: str = ""):
    """从文件读取已总结的内容并保存到所有配置的目标位置

    专为外层对话设计：对话完成总结后将内容写入文件，
    再调用此函数读取并保存，避免通过命令行传递长文本。

    Args:
        filepath: 包含总结内容的文件路径
        original_url: 原文链接（可选）
        author: 作者信息（可选）
        tags: 标签列表（可选）
        original_title: 原始文章标题（可选）

    Returns:
        tuple: (formatted_note, filename)
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"总结内容文件不存在: {filepath}")

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    if not content.strip():
        raise ValueError(f"总结内容文件为空: {filepath}")

    return save_summarized_article(content, original_url=original_url, author=author, tags=tags, original_title=original_title)


def _extract_title_from_summary(summarized_content: str) -> str:
    """从已总结的内容中提取标题
    
    按优先级尝试：
    1. 匹配 **作者** 上一行（通常是标题行，跳过标签行）
    2. "核心定位" 后的内容
    """
    lines = summarized_content.split('\n')
    
    # 优先级1：查找 **作者** 或 作者 行的上一行非空、非标签内容（跳过空行和标签行）
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('**作者') or stripped.startswith('作者'):
            j = i - 1
            while j >= 0:
                prev = lines[j].strip()
                # 跳过空行和纯标签行（如 #独立开发者 #出海 #SEO）
                if prev and not re.match(r'^#\S+(?:\s+#\S+)*$', prev):
                    return prev[:50]
                j -= 1
            break
    
    # 优先级2：从核心定位行提取（兼容 **核心定位** 格式）
    for line in lines:
        stripped = line.strip()
        match = re.search(r'\*{0,2}核心定位\*{0,2}\s*[：:]\s*(.+)', stripped)
        if match:
            return match.group(1).strip()[:50]
    
    return ""


def save_summarized_article(summarized_content: str, original_url: str = "", author: str = "", tags: list = None, original_title: str = ""):
    """保存已总结的文章内容到所有可用目标
    
    Args:
        summarized_content: 已经由AI总结好的文章内容
        original_url: 原文链接（可选）
        author: 作者信息（可选）
        tags: 标签列表（可选）
        original_title: 原始文章标题（可选，用于生成文件名）
    
    Returns:
        tuple: (formatted_note, filename)
    """
    tags = list(tags or ["文章总结"])
    if original_url and "转载" not in tags:
        tags.append("转载")
    
    # 标题获取优先级：original_title > 从总结内容提取 > 空
    title = original_title or _extract_title_from_summary(summarized_content) or ""
    
    # 从 tags 中提取第一个有意义标签作为分类（跳过"文章总结""转载"等通用标签）
    category = ""
    skip_categories = {"文章总结", "转载", "总结", "笔记"}
    for tag in tags:
        if tag not in skip_categories:
            category = tag
            break
    
    filename = generate_filename(title, original_url, category)
    
    # 检查文件名冲突并生成唯一文件名（禁止覆盖已有文件）
    manager = OutputManager()
    available_outputs = manager.get_available_outputs()

    if len(available_outputs) > 0:
        should_rename = False
        for output in available_outputs:
            if os.path.exists(output.get_output_path(filename)):
                should_rename = True
                break

        if should_rename:
            base, ext = os.path.splitext(filename)
            counter = 1
            first_output = available_outputs[0]
            while os.path.exists(first_output.get_output_path(f"{base}-{counter}{ext}")):
                counter += 1
            filename = f"{base}-{counter}{ext}"
    
    # 由于总结内容已经包含标签、作者和链接，设置 add_metadata=False 避免重复
    formatted_note = format_note_with_prompt(
        content=summarized_content,
        author=author,
        url=original_url,
        tags=tags,
        add_metadata=False  # 总结内容已包含元数据，不再重复添加
    )
    
    manager.save_all(formatted_note, filename)
    
    print(f"\n文章总结保存完成！")
    print(f"文件名: {filename}")
    print(f"已保存到: {', '.join([o.name for o in manager.get_available_outputs()])}")
    
    return formatted_note, filename


def _call_ai_summary(prompt: str, content: str, temperature: float = 0.7) -> str:
    """
    使用配置驱动的外部 AI Provider 进行文章总结
    
    仅检测需要显式 API Key 配置的外部 Provider：
    - openai: OpenAI API（需配置 OPENAI_API_KEY）
    - anthropic: Anthropic Claude API（需配置 ANTHROPIC_API_KEY）
    - google: Google Gemini API（需配置 GOOGLE_API_KEY）
    - local: 本地模型 Ollama（需配置 LOCAL_API_BASE）
    
    自动检测优先级：
    1. 优先使用环境变量 AI_PROVIDER 指定的外部 Provider
    2. 按顺序检测：openai > anthropic > google > local
    3. 使用第一个可用的外部 Provider
    
    注意：Trae SDK 不在此自动检测范围内，当无外部 Provider 可用时，
    返回 None 触发 skill_main 的降级流程，由外层对话接手处理。
    
    Args:
        prompt: 提示词模板（CONTENT_SUMMARY_PROMPT）
        content: 需要总结的文章内容
        temperature: 温度参数，控制创造性（默认0.7）
    
    Returns:
        str: AI模型返回的总结内容，失败返回None
    """
    from .ai_provider import call_external_ai_summarize, list_external_providers
    
    available = list_external_providers()
    if not available:
        print("   ℹ️ 未配置外部 AI Provider（OpenAI/Anthropic/Google/Local）")
        print("   ℹ️ 将触发降级流程，由外层对话接手总结")
        return None
    
    print(f"   📋 检测到可用的外部 AI Provider: {', '.join(available)}")
    result = call_external_ai_summarize(prompt, content, temperature=temperature)
    
    if result is None:
        print("   ❌ 外部 AI Provider 调用失败")
        return None
    
    return result


def summarize_content(content: str, author: str = "", url: str = "", tags: list = None, original_title: str = "") -> str:
    """
    使用CONTENT_SUMMARY_PROMPT调用AI模型对文章内容进行结构化总结
    
    Args:
        content: 原始文章内容
        author: 作者信息（可选）
        url: 原文链接（可选）
        tags: 标签列表（可选）
        original_title: 原始文章标题（可选）
    
    Returns:
        str: AI模型生成的结构化总结内容
    """
    tags = tags or []
    
    # 确保内容是正确的UTF-8编码
    if isinstance(content, bytes):
        content = content.decode('utf-8', errors='replace')
    
    # 内容长度检查
    if len(content.strip()) < 100:
        print("⚠️ 文章内容过短（少于100字），无法进行有效总结")
        return None
    
    # 获取内容总结Prompt模板
    prompt = CONTENT_SUMMARY_PROMPT
    
    # 构建前置元数据（标签、作者、链接）供AI参考
    metadata = ""
    if tags:
        metadata += f"标签：{' '.join(['#' + t for t in tags])}\n"
    if author:
        metadata += f"作者：{author}\n"
    if url:
        metadata += f"来源链接：{url}\n"
    if original_title:
        metadata += f"文章标题：{original_title}\n"
    
    # 如果有元数据，添加到内容前面
    if metadata:
        content_with_metadata = f"【文章元信息】\n{metadata}\n\n【文章正文】\n{content}"
    else:
        content_with_metadata = content
    
    # 调用AI模型进行总结
    print("   🤖 正在调用AI模型进行结构化总结...")
    summarized = _call_ai_summary(prompt, content_with_metadata)
    
    if summarized is None:
        # AI调用失败，返回None表示总结失败
        return None
    
    print("   ✅ AI总结完成")
    return summarized


def summarize_and_save(url_or_content: str, author: str = "", tags: list = None):
    """
    完整的文章总结与自动保存流程

    Args:
        url_or_content: 博客链接或文章原文
        author: 作者信息（可选）
        tags: 标签列表（可选）

    Returns:
        tuple: (summarized_content, article_content, original_url, original_title, error_message)
            - AI成功时：返回 (总结内容, 格式化笔记, 文件名, 标题, None)
            - AI失败时：返回 (None, 原文内容, URL, 标题, None)，外层对话可继续处理
            - 内容获取失败时：返回 (None, None, None, None, 错误消息)
    """
    print("🚀 开始执行文章总结与保存流程...")

    original_title = ""
    original_url = ""
    article_content = ""

    # 步骤1：获取内容
    print("\n📥 步骤1：获取文章内容")
    try:
        if url_or_content.startswith('http://') or url_or_content.startswith('https://'):
            print(f"   正在获取链接内容: {url_or_content}")
            original_url = url_or_content
            result = fetch_web_content(url_or_content)
            if not result:
                error_msg = "❌ 内容获取失败：无法访问链接或解析内容"
                print(error_msg)
                return None, None, None, None, error_msg
            original_title, article_content = result
            print(f"   ✅ 内容获取成功，标题: {original_title}")
        else:
            print("   直接处理输入的文章内容")
            original_url = ""
            article_content = url_or_content
            original_title = extract_article_title(article_content)
            print(f"   ✅ 内容获取成功，标题: {original_title}")
    except Exception as e:
        error_msg = f"❌ 内容获取失败: {str(e)}"
        print(error_msg)
        return None, None, None, None, error_msg

    # 步骤2：AI总结
    print("\n🧠 步骤2：AI模型生成结构化总结")
    print("   使用CONTENT_SUMMARY_PROMPT指导AI总结...")
    try:
        summarized_content = summarize_content(article_content, author=author, url=original_url, tags=tags, original_title=original_title)
    except Exception as e:
        print(f"⚠️ AI总结异常: {str(e)}")
        summarized_content = None

    # 检查AI总结是否成功
    if summarized_content is None:
        print("\n⚠️ AI总结暂不可用，已成功抓取文章内容")
        raw_filepath = save_raw_content_to_file(article_content, title=original_title)
        print(f"   📄 原始内容已暂存至: {raw_filepath}")
        print("   💡 外层对话可直接 Read 该文件获取完整原文，避免终端截断")
        return None, article_content, original_url, original_title, None

    print("   ✅ AI总结完成")

    # 步骤3：自动保存
    print("\n💾 步骤3：自动保存到配置的目标位置")
    try:
        tags = list(tags or ["文章总结"])

        if article_content:
            keywords = ["人工智能", "AI", "技术", "科技", "总结", "分析"]
            for kw in keywords:
                if kw in article_content and kw not in tags:
                    tags.append(kw)

        formatted_note, filename = save_summarized_article(summarized_content, original_url, author, tags, original_title)

        print("\n✅ 文章总结与保存流程完成！")
        return summarized_content, formatted_note, filename, original_title, None
    except Exception as e:
        error_msg = f"❌ 保存失败: {str(e)}"
        print(error_msg)
        return summarized_content, None, None, original_title, error_msg


def save_summary_only(input_data: dict) -> dict:
    """
    专门用于外层兜底总结后的自动保存函数

    Args:
        input_data: 输入数据字典，包含以下字段：
            - summarized_content: 已经总结好的内容（必填）
            - original_url: 原文链接（可选）
            - author: 作者信息（可选）
            - tags: 标签列表（可选）
            - original_title: 原始文章标题（可选）

    Returns:
        dict: 执行结果
    """
    print("💾 执行外层兜底总结后的自动保存...")

    summarized_content = input_data.get('summarized_content', '')
    original_url = input_data.get('original_url', '')
    author = input_data.get('author', '')
    tags = input_data.get('tags', [])
    original_title = input_data.get('original_title', '')

    if not summarized_content:
        return {
            'success': False,
            'message': '请提供总结好的内容'
        }

    try:
        formatted_note, filename = save_summarized_article(
            summarized_content, 
            original_url=original_url, 
            author=author, 
            tags=tags,
            original_title=original_title
        )
        return {
            'success': True,
            'message': '文章总结已自动保存！',
            'filename': filename,
            'content': formatted_note
        }
    except Exception as e:
        return {
            'success': False,
            'message': f'保存失败: {str(e)}'
        }


def skill_main(input_data: dict) -> dict:
    """
    技能主入口函数，供技能系统调用

    Args:
        input_data: 输入数据字典，包含以下字段：
            - content: 文章内容或博客链接
            - url: 原文链接（可选）
            - author: 作者信息（可选）
            - tags: 标签列表（可选）
            - summarized_content: 已经总结好的内容（用于外层兜底总结后保存）
            - original_title: 原始文章标题（可选）

    Returns:
        dict: 执行结果
            - success=True, summarized=内容: AI总结成功并已保存
            - success=True, need_continue_summary=True: 无外部AI Provider，已抓取内容，外层继续处理
            - success=False: 执行失败

    降级流程说明：
        当未配置任何外部 AI Provider（OpenAI/Anthropic/Google/Local）时：
        1. 正常抓取网页文章内容
        2. 返回 need_continue_summary=True + 已抓取的 article_content + prompt
        3. 由外层对话（当前使用的 AI 平台）接手进行总结处理
        4. 外层对话**必须**使用返回的 prompt（CONTENT_SUMMARY_PROMPT）进行结构化总结
        5. 总结完成后调用 save_summary_only 或 save_summarized_article 保存
        
        本 SKILL 为通用标准设计，不绑定任何特定平台 SDK（Trae/Codex/Claude Code/OpenCode 等）。
        无论是外部 API 路径还是降级路径，AI 总结都必须通过 CONTENT_SUMMARY_PROMPT 来保证输出格式统一。
    """
    # 如果直接提供了总结好的内容，直接保存
    if input_data.get('summarized_content'):
        return save_summary_only(input_data)

    print("🔧 blog-article-skill 技能执行中...")

    content = input_data.get('content', '')
    url = input_data.get('url', '')
    author = input_data.get('author', '')
    tags = input_data.get('tags', [])

    if url and not content:
        content = url

    if not content:
        return {
            'success': False,
            'message': '请提供文章内容或博客链接'
        }

    try:
        result = summarize_and_save(content, author, tags)
        summarized, second, third, original_title, error_msg = result

        if error_msg:
            return {
                'success': False,
                'message': error_msg
            }

        if summarized is not None:
            # AI成功：(summarized_content, formatted_note, filename, original_title, None)
            formatted_note = second
            filename = third
            return {
                'success': True,
                'message': '文章总结已自动保存！',
                'filename': filename,
                'content': formatted_note
            }
        elif second:  # AI失败，但有文章内容
            # AI失败：(None, article_content, original_url, original_title, None)
            article_content = second
            original_url = third
            return {
                'success': True,
                'need_continue_summary': True,
                'message': '⚠️ AI Provider 暂不可用，已成功抓取文章内容，请使用 CONTENT_SUMMARY_PROMPT 进行结构化总结',
                'article_content': article_content,
                'prompt': CONTENT_SUMMARY_PROMPT,
                'original_url': original_url,
                'original_title': original_title,
                'author': author,
                'tags': tags
            }
        else:
            return {
                'success': False,
                'message': '内容获取失败'
            }
    except Exception as e:
        return {
            'success': False,
            'message': f'执行失败: {str(e)}'
        }


async def async_fetch_web_content(url: str):
    """异步获取网页内容（使用 aiohttp）"""
    try:
        import aiohttp
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                html = await response.text(encoding='utf-8', errors='replace')

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')

        title = ""
        meta_title = soup.find('meta', property='og:title')
        if meta_title and meta_title.get('content'):
            title = meta_title['content'].strip()

        if not title:
            main_title_tag = soup.select_one('.main-title')
            if main_title_tag:
                title = main_title_tag.get_text(strip=True)

        if not title:
            for h1_tag in soup.find_all('h1'):
                text = h1_tag.get_text(strip=True)
                if text and len(text) > 10 and '新浪看点' not in text and '栏目' not in text:
                    title = text
                    break

        if not title:
            title_selectors = [
                '.article-title', 'h1.article-title', 'div.article-title',
                '.article-main-title', '#title', 'h1'
            ]
            for selector in title_selectors:
                tag = soup.select_one(selector)
                if tag:
                    text = tag.get_text(strip=True)
                    if text and len(text) > 10:
                        title = text
                        break

        if not title and soup.title:
            title = soup.title.string or ""

        content = ""
        selectors = ['article', '.article-content', '.content', '.main-content',
                     '.post-content', '#article', '.article-body', '.bd']
        for selector in selectors:
            article = soup.select_one(selector)
            if article:
                content = article.get_text(separator='\n', strip=True)
                break

        if not content:
            paragraphs = soup.find_all('p')
            content = '\n\n'.join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])

        if not content or len(content.strip()) < 100:
            return None

        return (title, content)
    except ImportError:
        print("⚠️ 异步抓取需要 aiohttp，请运行: pip install aiohttp")
        return await asyncio.to_thread(fetch_web_content, url)
    except Exception as e:
        print(f"❌ 异步获取网页内容失败: {str(e)}")
        return None


async def async_save_summarized_from_file(filepath: str, original_url: str = "", author: str = "", tags: list = None, original_title: str = ""):
    """异步版：从文件读取已总结的内容并保存到所有配置的目标位置"""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"总结内容文件不存在: {filepath}")

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    if not content.strip():
        raise ValueError(f"总结内容文件为空: {filepath}")

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, save_summarized_article, content, original_url, author, tags, original_title
    )


if __name__ == "__main__":
    print("此模块不支持直接运行，请使用：")
    print("  python assets/run.py --url 'https://example.com/article'")
    print("或从 Python 调用 summarize_and_save() / skill_main()")

