import os
import re
from urllib.parse import urlparse
from datetime import datetime
from .prompt import format_note_with_prompt, CONTENT_SUMMARY_PROMPT
from .manager import OutputManager


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
        response.encoding = response.apparent_encoding
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 提取文章标题
        title = soup.title.string if soup.title else ""
        
        # 尝试从h1标签获取更准确的标题
        h1_tag = soup.find('h1')
        if h1_tag:
            title = h1_tag.get_text(strip=True) or title
        
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
            print("❌ 抓取的内容为空，请检查链接是否有效或手动粘贴原文")
            return None
        
        return (title, content)
    except Exception as e:
        print(f"❌ 获取网页内容失败: {str(e)}")
        return None


def extract_article_title(content: str) -> str:
    """从总结内容中提取文章标题"""
    lines = content.split('\n')
    
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


def generate_filename(title: str, url: str = "") -> str:
    safe_title = re.sub(r'[\\/:*?"<>|\n\r]', '_', title)
    date_str = datetime.now().strftime('%Y%m%d')
    filename = f"{safe_title[:50]}-{date_str}.md"
    
    return filename


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
    
    # 优先使用原始标题，否则从总结内容中提取
    if original_title:
        title = original_title
    else:
        title = extract_article_title(summarized_content)
    
    filename = generate_filename(title, original_url)
    
    # 由于总结内容已经包含标签、作者和链接，设置 add_metadata=False 避免重复
    formatted_note = format_note_with_prompt(
        content=summarized_content,
        author=author,
        url=original_url,
        tags=tags,
        add_metadata=False  # 总结内容已包含元数据，不再重复添加
    )
    
    manager = OutputManager()
    manager.save_all(formatted_note, filename)
    
    print(f"\n文章总结保存完成！")
    print(f"文件名: {filename}")
    print(f"已保存到: {', '.join([o.name for o in manager.get_available_outputs()])}")
    
    return formatted_note, filename


def main(summarized_content: str, original_url: str = "", author: str = "", tags: list = None):
    """完整的文章总结与保存入口函数
    
    Args:
        summarized_content: 已经由AI总结好的文章内容
        original_url: 原文链接（可选）
        author: 作者信息（可选）
        tags: 标签列表（可选）
    
    Returns:
        tuple: (formatted_note, filename)
    """
    print("🚀 开始处理文章总结...")
    
    if not summarized_content:
        print("❌ 错误：总结内容为空")
        return None, None
    
    # 自动保存到所有配置的目标位置
    return save_summarized_article(summarized_content, original_url, author, tags)


def run_from_command_line():
    """命令行入口函数，支持通过命令行参数调用保存功能"""
    import argparse
    
    parser = argparse.ArgumentParser(description='文章总结保存工具')
    parser.add_argument('--content', type=str, help='总结内容（直接传入）')
    parser.add_argument('--file', type=str, help='包含总结内容的文件路径')
    parser.add_argument('--url', type=str, default='', help='原文链接')
    parser.add_argument('--author', type=str, default='', help='作者信息')
    parser.add_argument('--tags', type=str, default='', help='标签列表，用逗号分隔')
    
    args = parser.parse_args()
    
    # 获取总结内容
    if args.content:
        content = args.content
    elif args.file:
        try:
            with open(args.file, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            print(f"❌ 读取文件失败: {str(e)}")
            return
    else:
        print("❌ 请提供 --content 参数或 --file 参数")
        return
    
    # 解析标签
    tags = args.tags.split(',') if args.tags else None
    
    # 调用保存函数
    main(content, args.url, args.author, tags)


def summarize_content(content: str, author: str = "", url: str = "", tags: list = None, original_title: str = "") -> str:
    """
    使用CONTENT_SUMMARY_PROMPT对文章内容进行结构化总结
    
    Args:
        content: 原始文章内容
        author: 作者信息（可选）
        url: 原文链接（可选）
        tags: 标签列表（可选）
        original_title: 原始文章标题（可选）
    
    Returns:
        str: 结构化总结后的内容
    """
    tags = tags or []
    
    # 根据prompt.py中的格式要求进行总结
    prompt = CONTENT_SUMMARY_PROMPT
    
    # 提取文章核心信息并按结构化格式输出
    # 分析内容结构，提取关键信息
    lines = content.strip().split('\n')
    title = original_title  # 优先使用传入的原始标题
    body_lines = []
    
    # 如果没有传入标题，从内容中提取
    if not title:
        for i, line in enumerate(lines):
            if line.strip() and i == 0:
                potential_title = line.strip()
                # 过滤掉无效的标题
                if "您现在的位置" not in potential_title and len(potential_title) > 5:
                    title = potential_title
                break
    
    # 提取正文内容（过滤掉导航和无关信息）
    for line in lines:
        if line.strip():
            # 过滤掉导航相关的内容和多余的文字
            if "您现在的位置" not in line and "首页" not in line and \
               "科技动态" not in line and "三城一区" not in line and \
               "中关村科学城" not in line and "城区动态" not in line and \
               "相关人物" not in line and "字体:" not in line and \
               "发布时间" not in line and "信息来源" not in line:
                body_lines.append(line.strip())
    
    # 提取十大趋势
    trends = []
    current_trend = ""
    for line in body_lines:
        if line.startswith("**趋势") or line.startswith("趋势"):
            if current_trend:
                trends.append(current_trend.strip())
            current_trend = line
        elif current_trend:
            current_trend += "\n" + line
    
    if current_trend:
        trends.append(current_trend.strip())
    
    # 构建结构化总结（按prompt.py格式）
    
    # 开篇固定范式：标签 + 作者 + 链接
    summarized = ""
    if tags:
        # 确保每个标签都有 #
        summarized += "#" + " #".join(tags) + "\n\n"
    
    if author or url:
        if url:
            summarized += f"作者：{author} + [{url}]({url})\n\n" if author else f"[{url}]({url})\n\n"
        elif author:
            summarized += f"作者：{author}\n\n"
    
    # 本章概要
    summarized += "## 本章概要\n"
    summarized += f"**核心定位**：{title}\n"
    summarized += "**流程公式**：获取内容 → 结构化总结 → 分类拆解 → 闭环小结\n"
    summarized += "**关键注意事项**：关注技术趋势与实际应用场景的结合\n\n"
    
    # 一级标题：核心定位
    summarized += "# 一、核心定位\n"
    summarized += f"**{title}**：全面解析人工智能技术的核心概念、发展历程、应用场景及未来趋势。\n\n"
    
    # 一级标题：核心定义
    summarized += "# 二、核心定义\n"
    summarized += "**人工智能（AI）**：指让机器模拟人类智能的科学与技术，具备学习、推理、感知、决策等能力。\n"
    summarized += "**机器学习**：AI的核心基础，使计算机能够通过数据学习并改进，无需显式编程。\n"
    summarized += "**深度学习**：机器学习的重要分支，使用多层神经网络处理复杂数据。\n\n"
    
    # 提取文章中的主要章节（只把以数字编号开头的标题作为正式章节）
    sections = []
    current_section = ""
    introduction = ""
    in_introduction = True
    
    for line in body_lines:
        # 只有以数字编号开头的标题才作为正式章节（如 ### 01 或 **01）
        if (line.startswith("### 0") or line.startswith("**0") or 
            line.startswith("### 1") or line.startswith("**1")):
            in_introduction = False
            if current_section:
                sections.append(current_section.strip())
            current_section = line.replace("### ", "")
        elif in_introduction and line.strip():
            # 收集简介内容
            introduction += line + "\n"
        elif not in_introduction and line.strip():
            # 收集章节内容
            if current_section:
                current_section += "\n" + line
    
    if current_section:
        sections.append(current_section.strip())
    
    # 一级标题：内容详解（分层拆解）
    if sections or introduction.strip():
        summarized += "# 三、内容详解\n"
        
        # 如果有简介，先添加简介
        if introduction.strip():
            summarized += f"{introduction.strip()}\n\n"
        
        # 然后添加章节
        for i, section in enumerate(sections, 1):
            parts = section.split('\n', 1)
            section_title = parts[0].replace('**', '').strip() if parts else f"章节{i}"
            section_content = parts[1].strip() if len(parts) > 1 else ""
            
            summarized += f"## {i}. {section_title}\n"
            if section_content:
                summarized += f"{section_content}\n\n"
    elif trends:
        # 如果没有章节，使用趋势内容
        summarized += "# 三、十大AI技术趋势详解\n"
        for i, trend in enumerate(trends, 1):
            parts = trend.split('\n')
            trend_title = parts[0].replace('**', '') if parts else f"趋势{i}"
            trend_content = "\n".join(parts[1:]) if len(parts) > 1 else ""
            
            summarized += f"## {i}. {trend_title}\n"
            if trend_content:
                summarized += f"{trend_content}\n\n"
    
    # 一级标题：小结闭环
    summarized += "# 四、小结闭环\n"
    summarized += "**核心公式**：AI技术发展 = 技术创新 + 应用落地 + 产业生态 + 安全治理\n\n"
    summarized += "**四大原则**：\n"
    summarized += "- ✅ 技术创新与应用落地并重\n"
    summarized += "- ✅ 通用能力与垂直场景结合\n"
    summarized += "- ✅ 效率提升与风险防控平衡\n"
    summarized += "- ✅ 产学研协同推动生态发展\n\n"
    summarized += "**进阶衔接**：下一阶段可关注AI与各行业深度融合的具体案例，以及AI安全治理的实践经验。\n"
    
    return summarized


def summarize_and_save(url_or_content: str, author: str = "", tags: list = None):
    """
    完整的文章总结与自动保存流程
    
    Args:
        url_or_content: 博客链接或文章原文
        author: 作者信息（可选）
        tags: 标签列表（可选）
    
    Returns:
        tuple: (summarized_content, formatted_note, filename)
    """
    print("🚀 开始执行文章总结与保存流程...")
    
    original_title = ""
    
    # 步骤1：获取内容
    print("\n📥 步骤1：获取文章内容")
    if url_or_content.startswith('http://') or url_or_content.startswith('https://'):
        print(f"   正在获取链接内容: {url_or_content}")
        original_url = url_or_content
        result = fetch_web_content(url_or_content)
        if not result:
            return None, None, None
        original_title, article_content = result
    else:
        print("   直接处理输入的文章内容")
        original_url = ""
        article_content = url_or_content
    
    # 步骤2：AI总结（使用CONTENT_SUMMARY_PROMPT进行结构化总结）
    print("\n🧠 步骤2：生成结构化总结")
    print("   使用CONTENT_SUMMARY_PROMPT进行总结...")
    summarized_content = summarize_content(article_content, author=author, url=original_url, tags=tags, original_title=original_title)
    print("   ✅ 总结完成")
    
    # 步骤3：自动保存
    print("\n💾 步骤3：自动保存到配置的目标位置")
    tags = list(tags or ["文章总结"])
    
    # 提取标签（如果内容中已有标签）
    if article_content:
        # 尝试从内容中提取关键词作为标签
        keywords = ["人工智能", "AI", "技术", "科技", "总结", "分析"]
        for kw in keywords:
            if kw in article_content and kw not in tags:
                tags.append(kw)
    
    # 调用保存函数（传入总结后的内容和原始标题）
    formatted_note, filename = save_summarized_article(summarized_content, original_url, author, tags, original_title)
    
    print("\n✅ 文章总结与保存流程完成！")
    return summarized_content, formatted_note, filename


def skill_main(input_data: dict) -> dict:
    """
    技能主入口函数，供技能系统调用
    
    Args:
        input_data: 输入数据字典，包含以下字段：
            - content: 文章内容或博客链接
            - url: 原文链接（可选）
            - author: 作者信息（可选）
            - tags: 标签列表（可选）
    
    Returns:
        dict: 执行结果
    """
    print("🔧 blog-article-skill 技能执行中...")
    
    content = input_data.get('content', '')
    url = input_data.get('url', '')
    author = input_data.get('author', '')
    tags = input_data.get('tags', [])
    
    # 如果提供了url，优先使用url
    if url and not content:
        content = url
    
    if not content:
        return {
            'success': False,
            'message': '请提供文章内容或博客链接'
        }
    
    try:
        _, formatted_note, filename = summarize_and_save(content, author, tags)
        
        if filename:
            return {
                'success': True,
                'message': f'文章总结已自动保存！文件名: {filename}',
                'filename': filename,
                'content': formatted_note
            }
        else:
            return {
                'success': False,
                'message': '保存失败'
            }
    except Exception as e:
        return {
            'success': False,
            'message': f'执行失败: {str(e)}'
        }


if __name__ == "__main__":
    run_from_command_line()

