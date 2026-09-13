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
# P2 Draft-only 模式（并行流水线落盘解耦）：worker 只写本地草稿，Landing 阶段统一落飞书
# ---------------------------------------------------------------------------
DRAFT_DIR = os.path.join(NOTES_DIR, "_drafts")


def _get_draft_dir() -> str:
    os.makedirs(DRAFT_DIR, exist_ok=True)
    return DRAFT_DIR


def land_drafts(obsidian: bool = False) -> int:
    """Landing 阶段（P2 Parallel）：串行消费 notes/_drafts/* 草稿，统一落飞书。

    设计：并行 worker 写草稿时设 DRAFT_ONLY=1（不碰飞书）；父进程 Landing 关闭该 env 后
    调本函数，串行落盘避免并发飞书限流 / 重复节点竞态（D1/#2/#6）。落盘成功即删草稿。
    返回实际落盘条数。

    落盘沿用 FeishuOutput.save 的 upsert + 节点锁（幂等、无重复节点）。obsidian 尊重草稿 meta。
    """
    import json as _json
    import glob as _glob
    draft_dir = _get_draft_dir()
    # 递归：draft 文件名含 folder 层级（如「测试分类/账号/xxx.md」），会落在子目录下
    metas = sorted(_glob.glob(os.path.join(draft_dir, "**", "*.meta.json"), recursive=True))
    if not metas:
        return 0
    count = 0
    for meta_path in metas:
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = _json.load(f)
            draft_path = meta.get("draft_path") or (meta_path[:-len(".meta.json")] + ".md")
            if not os.path.exists(draft_path):
                try:
                    os.remove(meta_path)
                except OSError:
                    pass
                continue
            with open(draft_path, encoding="utf-8") as f:
                content = f.read()
            mgr = OutputManager(obsidian=bool(obsidian or meta.get("obsidian", False)))
            mgr.save_all(content, meta["filename"], title=meta.get("title", ""))
            # 落盘成功 → 清理草稿（飞书可用时不会双写本地笔记，无需保留草稿）
            try:
                os.remove(draft_path)
                os.remove(meta_path)
            except OSError:
                pass
            count += 1
            print(f"  📥 Landed draft: {meta.get('title', '')[:40]}")
        except Exception as e:
            print(f"  ⚠️ land_draft 失败（保留草稿下次重试）：{e}")
    return count


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
    # 仅对已知笔记类型补「类型」标签（结构化复盘/观点卡等，裸标签）；未命中则不补——
    # #文章总结 这类无信息量的兜底标签不再生成（落地使用无意义，且与裸化的类型标签重复）。
    tags = [_NOTE_TYPE_TAG[note_type]] if note_type in _NOTE_TYPE_TAG else []
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
        # 日期前缀强制（2026-09-07）：日期在最前，按名排序即按时间排序，
        # 彻底取代此前「标题-日期」后缀格式（后缀排序错乱 + 同名不同日期不可区分）。
        filename = f"{date_str}_【{safe_category}】{safe_title[:50]}.md"
    else:
        filename = f"{date_str}_{safe_title[:50]}.md"
    return filename


# 最近一次降级暂存的 raw 文件路径（供 skill_main 降级返回时携带给外层/监控）
_LAST_RAW_FILEPATH = ""
# 最近一次降级时的发布时间（epoch 秒，2026-09-07 生成侧发布时间链路）
_LAST_PUBLISH_TIME = 0


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


def save_summarized_from_file(filepath: str, original_url: str = "", author: str = "", tags: list = None, original_title: str = "", obsidian: bool = False, folder: str = "", category: str = "", publish_time: int = 0) -> tuple:
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"总结内容文件不存在: {filepath}")
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    if not content.strip():
        raise ValueError(f"总结内容文件为空: {filepath}")
    # 收口（2026-09-09）：去重/质量门禁/autoroute/publish_time 四件事统一由 save_summary_only
    # 一处维护，本函数不再自带 autoroute（save_summary_only 内部幂等处理）。
    result = save_summary_only({
        'summarized_content': content, 'original_url': original_url,
        'author': author, 'tags': tags, 'original_title': original_title,
        'obsidian': obsidian, 'folder': folder, 'category': category,
        'publish_time': publish_time,
    })
    if not result.get('success'):
        raise RuntimeError(result.get('message', '保存失败'))
    return (result.get('content') or result.get('message', ''), result.get('filename', ''))


def _extract_title_from_summary(summarized_content: str) -> str:
    """去 H1（DECISION-20260907）：正文不再含一级标题/标签行，标题由 original_title/文件名承担；
    本函数仅作「核心定位：」兜底提取，不再扫作者行上方（旧格式 H1 已废止）。"""
    for line in summarized_content.split('\n'):
        stripped = line.strip()
        match = re.search(r'\*{0,2}核心定位\*{0,2}\s*[：:]\s*(.+)', stripped)
        if match:
            return match.group(1).strip()[:50]
    return ""


def _freshness_label(publish_time: int) -> str:
    """（已废弃）原新鲜度标签逻辑。2026-09-13 用户决策移除 #更早/🔥当日/本周 等时效标签（无检索价值），保留空壳以兼容旧引用。"""
    return ""


def _sanitize_folder(folder: str) -> str:
    """净化子目录路径：按 / 分段、每段去非法字符，如「投资交易/舟亦横」。"""
    if not folder:
        return ""
    parts = [re.sub(r'[\\:*?"<>|\n\r]', '_', p).strip()
             for p in folder.replace("\\", "/").split("/")]
    parts = [p for p in parts if p and p not in (".", "..")]
    return "/".join(parts)


def _guess_source(url: str) -> str:
    """从 URL 推断来源站（登记表 source 字段）：bilibili/wechat/scys，未知留空。"""
    u = url or ""
    if "bilibili.com" in u or "b23.tv" in u:
        return "bilibili"
    if "mp.weixin.qq.com" in u:
        return "wechat"
    if "scys.com" in u:
        return "scys"
    return ""


def save_summarized_article(summarized_content: str, original_url: str = "", author: str = "", tags: list = None, original_title: str = "", meta: dict = None, note_type: str = "", publish_time: int = 0, folder: str = "", obsidian: bool = False, draft_only: bool = False, content_key: str = "", topics: list = None) -> tuple:
    """保存已总结的文章内容到所有可用目标。

    Args:
        meta: 保留参数兼容；frontmatter 已停产（PLAN-20260906 任务2），检索改走去重登记表
        note_type: 保留参数兼容；不再写入笔记 frontmatter
        content_key: 无 original_url 的粘贴原文场景，按此原文内容哈希登记去重；
                     有 url 时忽略。两者皆无则本篇不进登记表。
        publish_time: 内容原始发布时间（epoch 秒）；>0 时文件名日期用发布时间，
                      而非处理时间——投资类内容时效性强，记录「内容何时发的」才有意义。
        folder: 归档子目录（如「投资交易/舟亦横」）。非空时笔记落
                Obsidian `<vault>/<folder>/` 与飞书对应层级容器节点下（不进「待归类」）；
                监控订阅产出用它按「分类/账号名」归档，内容与源头对得上。
    """
    tags = list(tags or [])

    # 先基于「用户原始 tags」锁定 category（防止下方注入的自动裸标签 topic/用途 抢分类）。
    # 自动语义标签只进笔记顶部 #标签 行用于检索，绝不参与文件夹路由。
    from shared.routing import CATEGORY_SKIP_TAGS
    category = ""
    for tag in tags:
        if tag not in CATEGORY_SKIP_TAGS and "/" not in tag:
            category = tag
            break

    title = original_title or _extract_title_from_summary(summarized_content) or ""

    # 方案 A（2026-09-13 修订·2026-09-13 晚间二次精简）：落盘时复用分类器追加语义标签。
    # 维度顺序：主题实体（裸） → 用途（裸） → 父/子领域（命名空间） → 类型（裸）。
    # 仅追加、不覆盖调用方传入的 tags；去重。topic、用途、类型 均为裸标签（用户要求精简），
    # 仅领域保留「父/子」命名空间（含 /，被 category 推算跳过，不抢文件夹路由）。
    # 主题实体维度由总结 LLM 顺手生成（落盘前已从正文提取并移除『核心主题词』区块），
    # 无 LLM 主题词时退化为代码关键词抽取。
    try:
        from shared.note_classify import infer_semantic_tags, extract_and_strip_topics
        summarized_content, _block_topics = extract_and_strip_topics(summarized_content)
        effective_topics = topics if (topics is not None and len(topics) > 0) else _block_topics
        semantic = infer_semantic_tags(
            summarized_content, folder=folder, author=author,
            note_type=note_type, url=original_url, topics=effective_topics,
        )
        if semantic:
            _seen = set(tags)
            for _t in semantic:
                if _t not in _seen:
                    tags.append(_t)
                    _seen.add(_t)
    except Exception as _e:
        print(f"  ⚠️ 语义标签自动生成失败（非致命，跳过）：{_e}")

    # 方案 A 收口：命名空间标签已编码的信息，移除冗余裸标签
    # （例：领域/类型等命名空间值若又作为裸标签出现则移除，避免同义双写；
    #  作者本身已在文件夹路径 + Obsidian path: 搜索可聚合，裸作者标签纯属重复噪声亦移除）
    _ns_values = {_t.split("/", 1)[1] for _t in tags if "/" in _t}
    if _ns_values:
        tags = [_t for _t in tags if not ("/" not in _t and _t in _ns_values)]
    _author_norm = (author or "").strip()
    if _author_norm:
        tags = [_t for _t in tags if _t != _author_norm]

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

    # ── P2 Draft-only 模式（并行 worker 用，避免并发落飞书；Landing 阶段统一落盘）──
    # 控制来源：显式参数优先；否则读环境变量 DRAFT_ONLY（worker 子进程启动时设置）。
    # 含 folder 的 filename（如「投资交易/舟亦横/x.md」）在 _drafts 下保留层级，Landing 按原 filename 落盘。
    _draft_on = draft_only if draft_only else (
        os.getenv("DRAFT_ONLY", "").lower() in ("1", "true", "yes", "on"))
    if _draft_on:
        import json as _json
        dd = _get_draft_dir()
        dp = os.path.join(dd, filename)
        os.makedirs(os.path.dirname(dp), exist_ok=True)
        with open(dp, "w", encoding="utf-8") as f:
            f.write(formatted_note)
        meta_path = os.path.join(dd, filename + ".meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            _json.dump({
                "filename": filename, "title": title, "original_url": original_url,
                "folder": folder, "obsidian": obsidian, "author": author, "tags": tags,
                "draft_path": dp,
            }, f, ensure_ascii=False)
        # 仍标记 dedup，避免重复入队（落盘在 Landing 阶段，不改 dedup 语义）
        if original_url:
            dedup.mark_summarized(url=original_url, title=original_title,
                                  filename=filename, folder=folder,
                                  note_type=note_type,
                                  source=_guess_source(original_url))
        print(f"📝 [draft-only] 已写本地草稿（不落飞书）：{dp}")
        return formatted_note, dp

    manager.save_all(formatted_note, filename, title=title)

    # mark 收敛（PLAN-20260906 任务2）：登记只发生在本保存点，调用方不再各自 mark。
    # url 优先；粘贴原文（无 url）按 content_key 哈希登记；两者皆无则不登记。
    if original_url or content_key:
        dedup.mark_summarized(url=original_url, content=content_key, title=title,
                              filename=filename, folder=folder, note_type=note_type,
                              source=_guess_source(original_url))

    # 总览索引维护（仅监控路径 folder 非空时）：落盘成功后把本篇插入账号容器总览，
    # 解决飞书按创建时间排、补历史数据后顺序乱的问题（用户 2026-08-25 决策）。
    # 系列课有集数顺序、走自有总览，不重复进账号总览。
    if folder:
        feishu_out = next((o for o in manager.get_available_outputs() if o.name == "feishu"), None)
        last = getattr(feishu_out, "_last_created", None) if feishu_out else None
        if last and last.get("url"):
            try:
                from shared import feishu_overview as fo
                fo.add_entry(
                    folder,
                    parent_token=last.get("parent_token"),
                    entry={"title": title, "url": last["url"], "publish_time": publish_time},
                )
            except Exception as e:
                print(f"  ⚠️ 总览索引更新失败（非致命，不影响落盘）：{e}")

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
    # 提取 LLM 生成的『核心主题词』区块（转为 #topic/ 标签，从正文移除，不让质量门禁误判）
    from shared.note_classify import extract_and_strip_topics
    summary, topics = extract_and_strip_topics(summary)
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
        "summary": summary,
        "usage": meta.get("usage"),
        "model": meta.get("model"),
        "source": meta.get("source"),
        "topics": topics,
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
            original_title, article_content, page_publish_time = result
            # 散文：优先用页面提取的「文章发布时间」命名文件名；
            # 调用方显式传入（如监控 feed 自带发布时间）则尊重，不覆盖；
            # 两者皆无 → 回退到处理时间（generate_filename 内部 datetime.now()）。
            if not publish_time and page_publish_time:
                publish_time = page_publish_time
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
        global _LAST_RAW_FILEPATH, _LAST_PUBLISH_TIME
        _LAST_RAW_FILEPATH = raw_filepath
        _LAST_PUBLISH_TIME = publish_time
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
            publish_time=publish_time, folder=folder, obsidian=obsidian,
            content_key=article_content, topics=ai_result.get("topics")
        )
        print("\n✅ 文章总结与保存流程完成！")
        return summarized_content, formatted_note, filename, original_title, None
    except Exception as e:
        error_msg = f"❌ 保存失败: {str(e)}"
        print(error_msg)
        return summarized_content, None, None, original_title, error_msg


def autoroute_folder(folder: str, author: str, original_url: str, original_title: str, tags: list = None, source: str = "user_link", category: str = "") -> tuple:
    """folder 为空时按 author/url 走统一路由器兜底（L8 语义，2026-09-05 收编全部旁路入口）。

    「落哪」由代码决定，不靠调用方记性。返回 (folder, tags)；author 会补进 tags。
    显式传了 folder 的调用方（monitors 管线 / land_scys_batch 等）原样返回。
    category：手贴散文的分类（B9 修复）。显式参数优先，缺省时从 tags 推断——
    无作者但有分类的内容落【我的总结】/<分类>，而不是被兜进【待归类】。
    """
    if folder:
        return folder, list(tags or [])
    from shared.routing import resolve_folder, extract_author, category_from_tags
    _author = (author or "").strip() or extract_author((original_url or "").strip())
    _category = (category or "").strip() or category_from_tags(tags)
    folder = resolve_folder({
        "author": _author, "url": original_url or "",
        "title": original_title, "source": source,
        "category": _category,
    })
    if _author and _author not in (tags or []):
        tags = list(tags or []) + [_author]
    print(f"   📁 folder 未传，自动路由到: {folder}")
    return folder, tags


def save_summary_only(input_data: dict) -> dict:
    print("💾 执行外层兜底总结后的自动保存...")
    summarized_content = input_data.get('summarized_content', '')
    # 先从正文提取并移除 LLM 生成的『核心主题词』区块（转为 #topic/ 标签，不让机械门禁误判字数/格式）
    from shared.note_classify import extract_and_strip_topics
    summarized_content, _block_topics = extract_and_strip_topics(summarized_content)
    _topics = input_data.get('topics') or _block_topics
    original_url = input_data.get('original_url', '')
    author = input_data.get('author', '')
    tags = input_data.get('tags', [])
    original_title = input_data.get('original_title', '')
    publish_time = input_data.get('publish_time', 0)
    folder = input_data.get('folder', '')
    obsidian = input_data.get('obsidian', False)
    max_words = input_data.get('max_words')  # 动态扩容上限覆盖（父 Agent 逐级放宽时传入 4000/5000）
    # source_chars：原文（转录稿）长度，用于 source-aware 参考值（有则按源长×比例带，无则固定区间兜底）
    source_chars = input_data.get('source_chars') or 0
    if not source_chars:
        _rf = input_data.get('raw_file') or input_data.get('raw_file_path')
        if _rf and os.path.exists(_rf):
            try:
                from prompts.verifier import count_note_words
                source_chars = count_note_words(open(_rf, encoding='utf-8').read())
            except Exception:
                source_chars = 0
    if not summarized_content:
        return {'success': False, 'message': '请提供总结好的内容'}
    # 机械去重闸门（DECISION-20260825）：URL 已总结过 → 不再写飞书，按成功出队；
    # force=True 为强制重写逃生舱。AI 只交总结，写不写由代码决定。
    if original_url and not input_data.get('force', False):
        rec = dedup.is_summarized(url=original_url)
        if rec:
            print(f"⏭️ 该链接已总结过，机械跳过写入（{rec.get('filename', '')}）。如需重写传 force=True")
            return {'success': True, 'skipped': True,
                    'message': f"ALREADY_EXISTS:{rec.get('filename', '')}",
                    'filename': rec.get('filename', '')}
    # 机械质量门禁（DECISION-20260905，零 AI 依赖）：主标题唯一 / 来源链接卫生 / 字数区间。
    # 接入顺序硬约束：在 dedup 闸门之后（已总结条目机械出队优先于质量拦截），
    # 在 folder 自动路由之前（违规内容不触发路由副作用）。首次拦截不落盘、不 dedup，
    # 子 Agent 按返回的 issues 修复后重试；同 URL 再次仍纯字数违规则重试放行落盘
    # （2026-09-10：重改仍越界说明压缩已到头——干货密度高或原文本身撑不起区间）。
    # H1/来源链接等确定性可修复问题永不放行；force 只豁免 dedup，不豁免质量底线。
    from prompts.verifier import verify_note_mechanical
    from shared.gate_blockers import log_gate_block, count_blocks
    _gate = verify_note_mechanical(summarized_content, input_data.get('note_type', ''),
                                   source_url=original_url, max_words=max_words,
                                   source_chars=source_chars)
    if not _gate["passed"]:
        _non_word_issues = [i for i in _gate["issues"] if not i.startswith("字数")]
        _blocked_before = count_blocks(original_url or "")
        if not _non_word_issues and _blocked_before >= 1:
            print(f"↩️ 字数门禁重试放行：该 URL 已拦截 {_blocked_before} 次，重改后仍越界，"
                  "按内容密度/原文长度实情放行落盘")
            log_gate_block(source="queue", note_type=input_data.get('note_type', ''),
                           url=original_url or "", title=original_title or "",
                           issues=_gate["issues"], warnings=_gate.get("warnings", []),
                           action="bypassed_retry")
        else:
            print("⛔ 机械门禁拦截：" + "；".join(_gate["issues"]))
            # 拦截事件持久化（gate_blockers 台账）：队列路径此前无拦截记录，无法区分
            # 「未消费」与「被拦」，观测缺口由此补齐；台账失败不影响主流程。
            log_gate_block(source="queue", note_type=input_data.get('note_type', ''),
                           url=original_url or "", title=original_title or "",
                           issues=_gate["issues"], warnings=_gate.get("warnings", []),
                           compression_warnings=_gate.get("compression_warnings", []))
            return {'success': False, 'message': 'VERIFIER_FAILED:' + '；'.join(_gate["issues"]),
                    'issues': _gate["issues"],
                    'compression_warnings': _gate.get("compression_warnings", [])}
    # L8 修复（2026-09-03）：自带总结的保存路径 folder 为空时自动走统一路由器，
    # 与 skill_main 的 L7 手贴 URL 路径对齐——「落哪」由代码决定，不靠调用方记性。
    # 背景：批量总结曾有 78 篇因调用方漏传 folder 全部落进【待归类】。
    folder, tags = autoroute_folder(folder, author, original_url, original_title, tags,
                                    category=input_data.get('category', ''))
    try:
        formatted_note, filename = save_summarized_article(
            summarized_content, original_url=original_url, author=author,
            tags=tags, original_title=original_title, publish_time=publish_time,
            folder=folder, obsidian=obsidian,
            note_type=input_data.get('note_type', ''),
            topics=_topics
        )
        # 无人值守降级：落盘命中参考值抽检信号 → 入 needs_review 队列（filename 已知）
        _review_flags = _gate.get("review_flags", [])
        if _review_flags:
            try:
                from prompts.review_rubric import queue_for_review
                queue_for_review(filename=filename, note_type=input_data.get('note_type', ''),
                                 reasons=_review_flags, source_url=original_url)
            except Exception:
                pass
        return {'success': True, 'message': '文章总结已自动保存！', 'filename': filename, 'content': formatted_note,
                'compression_warnings': _gate.get("compression_warnings", []),
                'review_flags': _review_flags}
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
            "title": input_data.get("original_title", ""),
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
            return {'success': True, 'message': '文章总结已自动保存！', 'filename': filename, 'content': formatted_note, 'original_title': original_title}
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
                'publish_time': _LAST_PUBLISH_TIME or publish_time,
            }
        else:
            return {'success': False, 'message': '内容获取失败'}
    except Exception as e:
        return {'success': False, 'message': f'执行失败: {str(e)}'}


def skill_continue_summary(article_content: str, summary_content: str, original_url: str = "", author: str = "", tags: list = None, original_title: str = "", obsidian: bool = False, folder: str = "", publish_time: int = 0) -> dict:
    if not summary_content or not summary_content.strip():
        return {'success': False, 'message': '总结内容为空，请提供有效的总结内容'}
    try:
        folder, tags = autoroute_folder(folder, author, original_url, original_title, tags)
        formatted_note, filename = save_summarized_article(
            summarized_content=summary_content, original_url=original_url, author=author,
            tags=tags, original_title=original_title, obsidian=obsidian, folder=folder,
            publish_time=publish_time
        )
        return {'success': True, 'message': '文章总结已自动保存！', 'filename': filename, 'content': formatted_note}
    except Exception as e:
        return {'success': False, 'message': f'保存失败: {str(e)}'}


async def async_fetch_web_content(url: str):
    """异步获取网页内容（委托增强版同步抓取，复用 trafilatura 等）。"""
    return await asyncio.to_thread(fetch_web_content, url)


async def async_save_summarized_from_file(filepath: str, original_url: str = "", author: str = "", tags: list = None, original_title: str = "", obsidian: bool = False, folder: str = "", publish_time: int = 0) -> tuple:
    """委托同步版 save_summarized_from_file（线程池执行），自动继承 folder 路由与全套闸门。"""
    return await asyncio.to_thread(
        save_summarized_from_file, filepath, original_url=original_url, author=author,
        tags=tags, original_title=original_title, obsidian=obsidian, folder=folder,
        publish_time=publish_time
    )


if __name__ == "__main__":
    print("此模块不支持直接运行，请使用：")
    print("  python articles/run.py --url 'https://example.com/article'")
    print("或从 Python 调用 summarize_and_save() / skill_main()")
