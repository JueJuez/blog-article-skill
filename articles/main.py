import os
import re
import time
import asyncio
from datetime import datetime
from .fetch import fetch_web_content  # A1: 增强抓取层
from .prompt import format_note_with_prompt, CONTENT_SUMMARY_PROMPT, get_note_prompt, classify_note_type
from prompts.templates import verify_note, should_gate_retry, build_gate_critique, QUALITY_GATE_SELFCHECK
from .manager import OutputManager
from . import dedup  # A2: 增量去重
from shared.wb_ai import call_wb_ai  # C2/A6: 可选 WB 内置 AI


NOTES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'notes')


def _ensure_notes_dir() -> str:
    os.makedirs(NOTES_DIR, exist_ok=True)
    return NOTES_DIR


# ---------------------------------------------------------------------------
# 统一 AI 调用（A6 + A4）：external → WB 内置 AI → 降级
# ---------------------------------------------------------------------------

def call_ai_summary_with_meta(prompt: str, content: str, **kwargs) -> dict:
    """统一 AI 总结入口，返回带 usage 的 meta dict 或 None（触发降级）。

    优先级：
      1. FORCE_AGENT_MODE=1（默认）时直接返回 None，由 WorkBuddy 执行模型总结，
         不再调用任何外部 Provider。
      2. FORCE_AGENT_MODE=0 时尝试外部 Provider（openai/anthropic/google/local）。
      3. 外部 AI 不可用 / 未配置时返回 None，走降级路径。
    """
    if os.environ.get("FORCE_AGENT_MODE", "1") == "1":
        print("   🤖 FORCE_AGENT_MODE=1，跳过外部 AI，交由执行模型总结")
        return None

    from .ai_provider import call_external_ai_summarize_meta
    meta = call_external_ai_summarize_meta(prompt, content, **kwargs)
    if meta:
        meta["source"] = "external"
        return meta
    return None


# ---------------------------------------------------------------------------
# A5: 标签建议（分类反哺默认 tags）
# ---------------------------------------------------------------------------

_NOTE_TYPE_TAG = {
    "structured": "结构化复盘",
    "key_points": "要点提炼",
    "case": "案例拆解",
    "opinion": "观点卡",
}

_CONTENT_KEYWORDS = [
    "人工智能", "AI", "技术", "科技", "总结", "分析", "教程", "实战",
    "副业", "出海", "SEO", "独立开发", "产品", "增长", "编程",
]


def suggest_default_tags(note_type: str, title: str = "", content: str = "") -> list:
    """未指定 tags 时，由笔记类型 + 内容关键词生成默认标签。"""
    tags = [_NOTE_TYPE_TAG.get(note_type, "文章总结")]
    text = f"{title}\n{content}"
    for kw in _CONTENT_KEYWORDS:
        if kw in text and kw not in tags:
            tags.append(kw)
    return tags


# ---------------------------------------------------------------------------
# 标题 / 文件名
# ---------------------------------------------------------------------------

def extract_article_title(content: str) -> str:
    lines = content.split('\n')
    for line in lines[:10]:
        line = line.strip()
        if line.startswith('培训主题') or line.startswith('标题'):
            match = re.search(r'(培训主题|标题)\s*[：:]?\s*(.+)', line)
            if match:
                title = match.group(2).strip()
                title = re.sub(r'[\\/:*?"<>|\n\r]', '_', title)
                return title[:50]
    for line in lines[:20]:
        line = line.strip()
        if line.startswith('## '):
            title = line[3:].strip()
            if title and not title.startswith('本章') and not title.startswith('核心'):
                title = re.sub(r'[\\/:*?"<>|\n\r]', '_', title)
                return title[:50]
    for line in lines:
        line = line.strip()
        if line.startswith('# 一、') or line.startswith('# 二、') or line.startswith('# 三、') or line.startswith('# 四、'):
            title = line[2:].strip()
            if title.startswith(('一、', '二、', '三、', '四、')):
                title = title[2:].strip()
            title = re.sub(r'[\\/:*?"<>|\n\r]', '_', title)
            return title[:50]
    for line in lines:
        line = line.strip()
        if line.startswith("**核心定位**") or line.startswith("核心定位"):
            match = re.search(r'(核心定位\s*[：:]?)\s*(.+)', line)
            if match:
                title = match.group(2).strip()
                title = re.sub(r'[\\/:*?"<>|\n\r]', '_', title)
                return title[:50]
    for line in lines:
        line = line.strip()
        if line and not line.startswith('#') and not line.startswith('-') and len(line) < 100:
            return line[:50].replace('/', '_').replace('\\', '_')
    return "文章总结"


def generate_filename(title: str, url: str = "", category: str = "", publish_time: int = 0) -> str:
    safe_title = re.sub(r'[\\/:*?"<>|\n\r]', '_', title).strip()
    if not safe_title or len(safe_title) < 2:
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        return f"未命名笔记-{timestamp}.md"
    # 文件名日期：优先内容原始发布时间，否则处理时间
    if publish_time and publish_time > 0:
        date_str = datetime.fromtimestamp(publish_time).strftime('%Y%m%d')
    else:
        date_str = datetime.now().strftime('%Y%m%d')
    if category:
        safe_category = re.sub(r'[\\/:*?"<>|\n\r]', '_', category).strip()
        filename = f"【{safe_category}】{safe_title[:50]}-{date_str}.md"
    else:
        filename = f"{safe_title[:50]}-{date_str}.md"
    return filename


# 最近一次降级暂存的 raw 文件路径（供 skill_main 降级返回时携带给外层/监控）
_LAST_RAW_FILEPATH = ""


def save_raw_content_to_file(content: str, title: str = "", prefix: str = "_raw_") -> str:
    notes_dir = _ensure_notes_dir()
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    safe_title = re.sub(r'[\\/:*?"<>|\n\r]', '_', title)[:30] if title else "no_title"
    filename = f"{prefix}{safe_title}-{timestamp}.md"
    filepath = os.path.join(notes_dir, filename)
    header = f"> 原始文章内容（自动暂存）\n> 标题：{title}\n> 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n\n"
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(header + content)
    return os.path.abspath(filepath)


def save_summarized_from_file(filepath: str, original_url: str = "", author: str = "", tags: list = None, original_title: str = "", obsidian: bool = False):
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"总结内容文件不存在: {filepath}")
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    if not content.strip():
        raise ValueError(f"总结内容文件为空: {filepath}")
    return save_summarized_article(content, original_url=original_url, author=author, tags=tags, original_title=original_title, obsidian=obsidian)


def _extract_title_from_summary(summarized_content: str) -> str:
    lines = summarized_content.split('\n')
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('**作者') or stripped.startswith('作者'):
            j = i - 1
            while j >= 0:
                prev = lines[j].strip()
                if prev and not re.match(r'^#\S+(?:\s+#\S+)*$', prev):
                    return prev[:50]
                j -= 1
            break
    for line in lines:
        stripped = line.strip()
        match = re.search(r'\*{0,2}核心定位\*{0,2}\s*[：:]\s*(.+)', stripped)
        if match:
            return match.group(1).strip()[:50]
    return ""


def _yaml_frontmatter(meta: dict) -> str:
    """把 meta 渲染成 YAML frontmatter（无 pyyaml 依赖）。"""
    lines = []
    for k, v in meta.items():
        if isinstance(v, dict):
            lines.append(f"{k}:")
            for kk, vv in v.items():
                lines.append(f"  {kk}: {vv}")
        else:
            lines.append(f"{k}: {v if v is not None else ''}")
    return "---\n" + "\n".join(lines) + "\n---\n\n"


def _freshness_label(publish_time: int) -> str:
    """按内容原始发布时间距今天数返回新鲜度标签（时效感知）。

    当日(<1天)=🔥当日 / 一周内=本周 / 更早=更早。无发布时间则返回空串。
    """
    if not publish_time or publish_time <= 0:
        return ""
    age_days = (time.time() - publish_time) / 86400.0
    if age_days < 1:
        return "🔥当日"
    if age_days < 7:
        return "本周"
    return "更早"


def _sanitize_folder(folder: str) -> str:
    """净化子目录路径：按 / 分段、每段去非法字符，如「投资交易/舟亦横」。"""
    if not folder:
        return ""
    parts = [re.sub(r'[\\:*?"<>|\n\r]', '_', p).strip()
             for p in folder.replace("\\", "/").split("/")]
    parts = [p for p in parts if p and p not in (".", "..")]
    return "/".join(parts)


def save_summarized_article(summarized_content: str, original_url: str = "", author: str = "", tags: list = None, original_title: str = "", meta: dict = None, note_type: str = "", publish_time: int = 0, folder: str = "", obsidian: bool = False):
    """保存已总结的文章内容到所有可用目标。

    Args:
        meta: A4 增强，含 {'usage': {...}, 'model': str}；存在时写入笔记 frontmatter
        note_type: 笔记类型，写入 frontmatter 便于检索
        publish_time: 内容原始发布时间（epoch 秒）；>0 时文件名日期与 frontmatter 用发布时间，
                      而非处理时间——投资类内容时效性强，记录「内容何时发的」才有意义。
        folder: 归档子目录（如「投资交易/舟亦横」）。非空时笔记落
                Obsidian `<vault>/<folder>/` 与飞书对应层级容器节点下（不进「待归类」）；
                监控订阅产出用它按「分类/账号名」归档，内容与源头对得上。
    """
    tags = list(tags or ["文章总结"])
    if original_url and "转载" not in tags:
        tags.append("转载")
    # 新鲜度标签（时效感知）：追加到末尾，避免它抢走「分类」（文件名用首个非跳过 tag 作分类）
    fresh = _freshness_label(publish_time)
    if fresh:
        tags.append(fresh)

    title = original_title or _extract_title_from_summary(summarized_content) or ""
    category = ""
    # 跳过「纯元信息/系统标签」——这些只作笔记内 #标签，不抢「分类」（分类决定落盘文件夹）。
    # 含：默认标签、转载标记、短动态类、新鲜度标签（🔥当日/本周/更早），保证监控产出统一落「待归类」。
    skip_categories = {"文章总结", "转载", "总结", "笔记",
                       "动态速览", "短动态", "🔥当日", "本周", "更早"}
    for tag in tags:
        if tag not in skip_categories:
            category = tag
            break

    folder = _sanitize_folder(folder)
    # folder 已提供分类归档路径时，文件名不再加【分类】前缀，避免子目录下重复冗余
    filename = generate_filename(
        title, original_url,
        category=category if not folder else "",
        publish_time=publish_time
    )
    if folder:
        filename = f"{folder}/{filename}"

    # 文件名冲突处理（禁止覆盖）
    manager = OutputManager(obsidian=obsidian)
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

    formatted_note = format_note_with_prompt(
        content=summarized_content, author=author, url=original_url,
        tags=tags, add_metadata=True, publish_time=publish_time
    )

    # A4：frontmatter（常驻）。新鲜度 + 发布时间用于时效感知；token 用量在走 AI 时补。
    fm = {}
    if original_url:
        fm["source_url"] = original_url
    if note_type:
        fm["note_type"] = note_type
    fresh = _freshness_label(publish_time)
    if fresh:
        fm["freshness"] = fresh
    if publish_time and publish_time > 0:
        fm["published_at"] = datetime.fromtimestamp(publish_time).isoformat(timespec="seconds")
    if meta and meta.get("usage"):
        fm["model"] = meta.get("model")
        fm["tokens"] = meta.get("usage")
        fm["generated_at"] = datetime.now().isoformat(timespec="seconds")
    if fm:
        formatted_note = _yaml_frontmatter(fm) + formatted_note

    manager.save_all(formatted_note, filename)

    print(f"\n文章总结保存完成！")
    print(f"文件名: {filename}")
    print(f"已保存到: {', '.join([o.name for o in manager.get_available_outputs()])}")

    return formatted_note, filename


def summarize_content(content: str, author: str = "", url: str = "", tags: list = None, original_title: str = "", note_type: str = "") -> dict:
    """调用 AI 对内容做总结，返回 {'summary','usage','model','source'}。

    笔记形态由 note_type 决定；未指定时自动分类。无可用 AI 时 summary=None。
    """
    tags = tags or []
    if isinstance(content, bytes):
        content = content.decode('utf-8', errors='replace')
    if len(content.strip()) < 100:
        print("⚠️ 文章内容过短（少于100字），无法进行有效总结")
        return {"summary": None, "usage": None, "model": None, "source": None}

    if not note_type:
        note_type = classify_note_type(original_title, content)
    print(f"   📑 笔记类型: {note_type}")

    prompt = get_note_prompt(note_type)

    metadata = ""
    if tags:
        metadata += f"标签：{' '.join(['#' + t for t in tags])}\n"
    if author:
        metadata += f"作者：{author}\n"
    if url:
        metadata += f"来源链接：{url}\n"
    if original_title:
        metadata += f"文章标题：{original_title}\n"

    content_with_metadata = f"【文章元信息】\n{metadata}\n\n【文章正文】\n{content}" if metadata else content

    print("   🤖 正在调用AI模型进行结构化总结...")
    meta = call_ai_summary_with_meta(prompt, content_with_metadata)
    if not meta or not meta.get("content"):
        return {"summary": None, "usage": None, "model": None, "source": None}

    summary = meta["content"]
    # A：质量闸门（第二遍把关）——评分<阈值带反馈重试一次
    if note_type:
        gate = verify_note(summary, content[:6000], note_type)
        if should_gate_retry(gate):
            crit = build_gate_critique(gate)
            meta2 = call_ai_summary_with_meta(prompt + crit, content_with_metadata)
            if meta2 and meta2.get("content"):
                summary = meta2["content"]
                meta = meta2

    print("   ✅ AI总结完成")
    return {
        "summary": meta["content"],
        "usage": meta.get("usage"),
        "model": meta.get("model"),
        "source": meta.get("source"),
    }


def summarize_and_save(url_or_content: str, author: str = "", tags: list = None, note_type: str = "", force: bool = False, publish_time: int = 0, folder: str = "", obsidian: bool = False):
    """完整的文章总结与自动保存流程（含 A2 去重 / A5 标签 / A4 计量）。

    Args:
        publish_time: 内容原始发布时间（epoch 秒），透传给笔记落地（记录「内容何时发的」）。

    Returns:
        (summarized_content, formatted_note/full_content, filename/url, title, error_msg)
    """
    print("🚀 开始执行文章总结与保存流程...")

    original_title = ""
    original_url = ""
    article_content = ""

    # 步骤0：URL 去重（提前，省一次抓取）
    if (url_or_content.startswith('http://') or url_or_content.startswith('https://')) and not force:
        if dedup.is_summarized(url=url_or_content):
            rec = dedup.is_summarized(url=url_or_content)
            print(f"⏭️ 该链接已总结过，跳过（{rec.get('filename', '')}）。如需重新总结请加 force=True")
            return (None, None, None, rec.get("title", ""), f"ALREADY_EXISTS:{rec.get('filename', '')}")

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

    # A2：正文去重（粘贴原文场景）
    if not force and not original_url:
        if dedup.is_summarized(content=article_content):
            rec = dedup.is_summarized(content=article_content)
            print(f"⏭️ 该内容已总结过，跳过（{rec.get('filename', '')}）")
            return (None, None, None, rec.get("title", ""), f"ALREADY_EXISTS:{rec.get('filename', '')}")

    # 确定笔记类型（供标签 + 保存 frontmatter 使用）
    if not note_type:
        note_type = classify_note_type(original_title, article_content)

    # A5：标签建议（未指定时由分类反哺）
    if not tags:
        tags = suggest_default_tags(note_type, original_title, article_content)
        print(f"   🏷️ 自动标签: {', '.join(tags)}")

    # 步骤2：AI 总结
    print("\n🧠 步骤2：AI模型生成总结")
    print(f"   使用笔记模板（{note_type or 'auto'}）指导AI总结...")
    try:
        ai_result = summarize_content(article_content, author=author, url=original_url, tags=tags, original_title=original_title, note_type=note_type)
    except Exception as e:
        print(f"⚠️ AI总结异常: {str(e)}")
        ai_result = {"summary": None, "usage": None, "model": None, "source": None}

    if ai_result.get("summary") is None:
        print("\n⚠️ AI总结暂不可用，已成功抓取文章内容")
        raw_filepath = save_raw_content_to_file(article_content, title=original_title)
        global _LAST_RAW_FILEPATH
        _LAST_RAW_FILEPATH = raw_filepath
        print(f"   📄 原始内容已暂存至: {raw_filepath}")
        print("   💡 外层对话可直接 Read 该文件获取完整原文，避免终端截断")
        return None, article_content, original_url, original_title, None

    summarized_content = ai_result["summary"]
    usage = ai_result.get("usage")
    model = ai_result.get("model")
    print("   ✅ AI总结完成")

    # 步骤3：保存
    print("\n💾 步骤3：自动保存到配置的目标位置")
    try:
        formatted_note, filename = save_summarized_article(
            summarized_content, original_url, author, tags, original_title,
            meta={"usage": usage, "model": model}, note_type=note_type,
            publish_time=publish_time, folder=folder, obsidian=obsidian
        )
        # A2：记录去重
        dedup.mark_summarized(url=original_url, content=article_content, title=original_title, filename=filename)
        print("\n✅ 文章总结与保存流程完成！")
        return summarized_content, formatted_note, filename, original_title, None
    except Exception as e:
        error_msg = f"❌ 保存失败: {str(e)}"
        print(error_msg)
        return summarized_content, None, None, original_title, error_msg


def save_summary_only(input_data: dict) -> dict:
    print("💾 执行外层兜底总结后的自动保存...")
    summarized_content = input_data.get('summarized_content', '')
    original_url = input_data.get('original_url', '')
    author = input_data.get('author', '')
    tags = input_data.get('tags', [])
    original_title = input_data.get('original_title', '')
    publish_time = input_data.get('publish_time', 0)
    folder = input_data.get('folder', '')
    obsidian = input_data.get('obsidian', False)
    if not summarized_content:
        return {'success': False, 'message': '请提供总结好的内容'}
    try:
        formatted_note, filename = save_summarized_article(
            summarized_content, original_url=original_url, author=author,
            tags=tags, original_title=original_title, publish_time=publish_time,
            folder=folder, obsidian=obsidian
        )
        return {'success': True, 'message': '文章总结已自动保存！', 'filename': filename, 'content': formatted_note}
    except Exception as e:
        return {'success': False, 'message': f'保存失败: {str(e)}'}


def skill_main(input_data: dict) -> dict:
    if input_data.get('summarized_content'):
        return save_summary_only(input_data)

    print("🔧 blog-article-skill 技能执行中...")
    content = input_data.get('content', '')
    url = input_data.get('url', '')
    author = input_data.get('author', '')
    tags = input_data.get('tags', [])
    note_type = input_data.get('note_type', '')
    force = input_data.get('force', False)
    publish_time = input_data.get('publish_time', 0)
    folder = input_data.get('folder', '')
    obsidian = input_data.get('obsidian', False)

    if url and not content:
        content = url
    if not content:
        return {'success': False, 'message': '请提供文章内容或博客链接'}

    # L7 修复（2026-08-23）：手贴 URL 且无显式 folder 时，提取作者并走统一路由器，
    # 使手贴内容与监控内容归到同一账号节点（如手贴「中金点睛」链接 → 【监控】/公众号/中金点睛）。
    # 监控路径已显式传 folder（_item_folder），此处不会重复触发，无副作用。
    _content_str = content if isinstance(content, str) else ""
    if not folder and re.match(r'https?://\S+', _content_str.strip()):
        from shared.routing import resolve_folder, extract_author
        if not author:
            author = extract_author(_content_str.strip())
        folder = resolve_folder({
            "author": author, "url": _content_str.strip(),
            "category": input_data.get("category", ""), "source": "user_link",
        })
        if author and author not in (tags or []):
            tags = list(tags or []) + [author]

    try:
        result = summarize_and_save(content, author, tags, note_type=note_type, force=force, publish_time=publish_time, folder=folder, obsidian=obsidian)
        summarized, second, third, original_title, error_msg = result

        if error_msg:
            if error_msg.startswith("ALREADY_EXISTS:"):
                filename = error_msg.split(":", 1)[1]
                return {
                    'success': True, 'skipped': True,
                    'message': f'该内容已总结过，跳过（{filename}）',
                    'filename': filename,
                }
            return {'success': False, 'message': error_msg}

        if summarized is not None:
            formatted_note = second
            filename = third
            return {'success': True, 'message': '文章总结已自动保存！', 'filename': filename, 'content': formatted_note}
        elif second:
            article_content = second
            original_url = third
            note_type = note_type or classify_note_type(original_title, article_content)
            return {
                'success': True, 'need_continue_summary': True,
                'message': '✅ 已抓取文章内容，等待执行模型（Agent）按笔记模板总结',
                'article_content': article_content, 'note_type': note_type,
                'prompt': get_note_prompt(note_type) + QUALITY_GATE_SELFCHECK, 'original_url': original_url,
                'original_title': original_title, 'author': author, 'tags': tags,
                'raw_file': _LAST_RAW_FILEPATH, 'folder': folder, 'obsidian': obsidian,
            }
        else:
            return {'success': False, 'message': '内容获取失败'}
    except Exception as e:
        return {'success': False, 'message': f'执行失败: {str(e)}'}


def skill_continue_summary(article_content: str, summary_content: str, original_url: str = "", author: str = "", tags: list = None, original_title: str = "", obsidian: bool = False):
    if not summary_content or not summary_content.strip():
        return {'success': False, 'message': '总结内容为空，请提供有效的总结内容'}
    try:
        formatted_note, filename = save_summarized_article(
            summarized_content=summary_content, original_url=original_url, author=author,
            tags=tags or [], original_title=original_title, obsidian=obsidian
        )
        return {'success': True, 'message': '文章总结已自动保存！', 'filename': filename, 'content': formatted_note}
    except Exception as e:
        return {'success': False, 'message': f'保存失败: {str(e)}'}


async def async_fetch_web_content(url: str):
    """异步获取网页内容（委托增强版同步抓取，复用 trafilatura 等）。"""
    return await asyncio.to_thread(fetch_web_content, url)


async def async_save_summarized_from_file(filepath: str, original_url: str = "", author: str = "", tags: list = None, original_title: str = "", obsidian: bool = False):
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"总结内容文件不存在: {filepath}")
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    if not content.strip():
        raise ValueError(f"总结内容文件为空: {filepath}")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: save_summarized_article(
            content, original_url=original_url, author=author, tags=tags,
            original_title=original_title, obsidian=obsidian
        )
    )


if __name__ == "__main__":
    print("此模块不支持直接运行，请使用：")
    print("  python articles/run.py --url 'https://example.com/article'")
    print("或从 Python 调用 summarize_and_save() / skill_main()")
