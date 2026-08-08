"""videos — 视频总结主逻辑（P2.1 获取 + P2.2 分块两段式 + P2.3 分集/playlist）

对视频/音频的字幕（transcript）或本地文件 ASR 结果做总结，复用 prompts 共享模板
与 articles 的多目标保存能力。

获取层（fetch/asr）与总结层（本模块）解耦；任意长度先经 shared.chunking 分块再喂模板，
绝不因超长爆上下文。

输入来源（按优先级）：
  1. url 为 YouTube/Bilibili 单视频 → 自动抓 CC 字幕（P2.1）
  2. url 为 playlist/合集/分P → 自动逐条总结 + 可选系列总览（P2.3）
  3. file 为本地视频/音频 → ASR 转写（P3）
  4. transcript/content 为字幕文本 → 直接总结（P1）

降级：FORCE_AGENT_MODE=1 时返回 need_continue_summary + prompt + 字幕文本，交执行模型（Agent）总结。
"""

import os
import re
from typing import Optional, List, Dict, Any

import articles.main as articles_main
from articles.main import (
    call_ai_summary_with_meta,
    save_summarized_article,
)
from prompts.templates import (
    get_note_prompt, format_note_with_prompt,
    verify_note, should_gate_retry, build_gate_critique, QUALITY_GATE_SELFCHECK,
)
from prompts.classify import classify_note_type
from shared.chunking import chunk_segments, chunk_text, two_stage_summarize, segments_to_text

from . import fetch, asr, multimodal

_NOTE_TYPE_TAG = {
    "structured": "结构化复盘",
    "key_points": "要点提炼",
    "case": "案例拆解",
    "opinion": "观点卡",
}


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _ai_summarize(prompt: str, content: str) -> Optional[str]:
    """统一 AI 总结（复用 articles 的 external→WB→降级 逻辑）。"""
    meta = call_ai_summary_with_meta(prompt, content)
    return meta.get("content") if meta else None


def _summarize_segments(segments, note_type: str, title: str = "", visual_context: str = "") -> Optional[str]:
    """两段式总结：分块 → 逐块小结 → 二次合并（P2.2）。

    visual_context: P4 多模态理解的画面上下文；非空时拼接到首块，供 AI 参考。
    """
    if isinstance(segments, str):
        chunks = [{"text": t} for t in chunk_text(segments)]
    elif isinstance(segments, list) and segments and isinstance(segments[0], dict):
        chunks = chunk_segments(segments)
    else:
        chunks = [{"text": str(segments)}]

    if not chunks:
        return None

    # P4：画面视觉信息拼到首块，让 AI 在总结时参考
    if visual_context and chunks:
        first = chunks[0]
        prefix = f"【画面视觉信息（来自多模态理解）】\n{visual_context}\n\n"
        if isinstance(first, dict):
            first["text"] = prefix + first.get("text", "")
        else:
            chunks[0] = prefix + str(first)

    prompt = get_note_prompt(note_type)

    def summarize_fn(text: str, i: int, total: int) -> Optional[str]:
        chunk_prompt = (prompt +
                        f"\n\n（这是第 {i+1}/{total} 段，请先独立小结这一段，"
                        f"严格保持笔记结构，不要补全未出现的内容）")
        return _ai_summarize(chunk_prompt, text)

    def merge_fn(partials: List[str]) -> Optional[str]:
        merge_prompt = (prompt +
                        "\n\n以下是各分段的小结，请合并为一篇完整笔记"
                        "（去重、保持结构、控制单篇篇幅、不要重复章节）：")
        return _ai_summarize(merge_prompt, "\n\n---\n\n".join(partials))

    final = two_stage_summarize(chunks, summarize_fn, merge_fn)
    if final and note_type:
        # A：质量闸门（第二遍把关）——评分<阈值带反馈重试一次
        gate_src = _sample_text(segments)[:6000]
        gate = verify_note(final, gate_src, note_type)
        if should_gate_retry(gate):
            crit = build_gate_critique(gate)

            def summarize_fn2(text, i, total):
                return _ai_summarize(prompt + crit, text)

            def merge_fn2(partials):
                return _ai_summarize(prompt + crit, "\n\n---\n\n".join(partials))

            retry = two_stage_summarize(chunks, summarize_fn2, merge_fn2)
            if retry:
                final = retry
    return final


def _summarize_and_save(segments, source_url: str, title: str, author: str,
                        tags: list, note_type: str, force: bool, visual_context: str = "",
                        publish_time: int = 0, folder: str = "", obsidian: bool = False):
    """总结并保存；返回 (filename, final_text, degraded, article_content, note_type)。"""
    if not note_type:
        sample = (segments_to_text(segments)
                  if isinstance(segments, list) and segments and isinstance(segments[0], dict)
                  else (segments if isinstance(segments, str) else ""))
        note_type = classify_note_type(title, sample)

    final = _summarize_segments(segments, note_type, title, visual_context=visual_context)
    if final is None:
        content = (segments_to_text(segments)
                   if isinstance(segments, list) and segments and isinstance(segments[0], dict)
                   else str(segments))
        return (None, None, True, content, note_type)

    label = _NOTE_TYPE_TAG.get(note_type, "视频笔记")
    save_tags = list(tags) if tags else [label]
    formatted, filename = save_summarized_article(
        final, original_url=source_url, author=author,
        tags=save_tags, original_title=title or "视频总结", note_type=note_type,
        publish_time=publish_time, folder=folder, obsidian=obsidian
    )
    return (filename, final, False, None, note_type)


def _sample_text(segments) -> str:
    if isinstance(segments, list) and segments and isinstance(segments[0], dict):
        return segments_to_text(segments)
    return str(segments)


def _looks_like_playlist(url: str, input_data: dict) -> bool:
    if not url:
        return False
    if input_data.get("playlist"):
        return True
    u = url.lower()
    return ("playlist" in u) or ("list=" in u) or ("bilibili.com/videos" in u) \
        or ("bilibili.com/medialist" in u) or ("/channel/" in u)


# ---------------------------------------------------------------------------
# B站系列课（多P视频）
# ---------------------------------------------------------------------------

def _sanitize_filename(name: str) -> str:
    if not name:
        return "未命名"
    s = re.sub(r'[\\/:*?"<>|\n\r\t]', '_', name).strip()
    s = re.sub(r'\s+', ' ', s)
    return s[:80] or "未命名"


def _local_write_enabled() -> bool:
    """本地 notes/ 仅在没有配置 obsidian/feishu 时才落盘。

    用户偏好（2026-07-21 起）：默认只写飞书（2026-08-08 改为单写优先），
    Obsidian 仅在显式开启时追加（见 §3.0）；本地 notes/ 不再作为交付目标写入
    （既不在 OutputManager 默认 save_all 里、也不在系列课里写本地文件，仅飞书不可用且未请求 Obsidian 时兜底）。
    """
    mgr = articles_main.OutputManager()
    names = {o.name.lower() for o in mgr.get_available_outputs()}
    return not (names & {"obsidian", "feishu"})


def _save_series_note(content: str, series_dir: str, base_name: str,
                      author: str, url: str, tags: list, note_type: str,
                      obsidian: bool = False) -> str:
    """把单集总结笔记同步到所有已配置输出（Obsidian / 飞书等）。

    满足用户需求：系列课先建一个「系列名」容器（Obsidian=同名子文件夹；
    飞书=同名 wiki 节点），里面每集一个文件。
    - 本地 notes/<系列名>/<第XX集_标题>.md：仅当没有配置 obsidian/飞书时才落盘
      （用户偏好：有云同步就不写本地）
    - 各外部输出：<容器>/<第XX集_标题>.md（自动建容器，非致命，失败仅告警）

    Returns 本地绝对路径（有云时不写本地，返回预期路径字符串）。
    """
    formatted = format_note_with_prompt(
        content=content, author=author, url=url,
        tags=tags, add_metadata=False
    )
    filename = f"{base_name}.md"
    path = os.path.join(series_dir, filename)
    # 有云同步（obsidian/feishu）则不写本地 notes/（用户偏好）
    if _local_write_enabled():
        with open(path, "w", encoding="utf-8") as f:
            f.write(formatted)

    # 同步到所有已配置输出（Obsidian / 飞书等）：每个输出下先建「系列名」容器再放笔记
    try:
        series_folder = os.path.basename(series_dir)  # 如 "千刀千法"
        mgr = articles_main.OutputManager(obsidian=obsidian)
        for out in mgr.get_available_outputs():
            try:
                if out.save_series(formatted, filename, series_folder):
                    print(f"   🔗 已同步 {out.name}：{series_folder}/{filename}")
            except Exception as e:
                print(f"   ⚠️ {out.name} 同步跳过（非致命）：{e}")
    except Exception as e:
        print(f"   ⚠️ 外部同步跳过（非致命）：{e}")

    return path


def _extract_h1(md: str) -> str:
    for line in md.splitlines():
        s = line.strip()
        if s.startswith('# ') and not s.startswith('## '):
            return s[2:].strip()
    return ""


def _extract_one_liner(md: str) -> str:
    for line in md.splitlines():
        # 兼容「> **30秒速览**：…」引用块前缀，与新模板对齐
        s = line.strip().lstrip('>').strip()
        if s.startswith('**一句话核心结论') or s.startswith('**30秒速览'):
            for sep in ('：', ':'):
                if sep in s:
                    return s.split(sep, 1)[1].strip().strip('*').strip()
    return ""


def _read_series_from_feishu(series_title: str) -> list:
    """从飞书「系列名」容器读回各集（云真值），抽取 集号 / H1 / 一句话核心结论。

    仅在本地不落盘时调用（用户偏好：有云同步就不写本地，
    总览改从云读，避免依赖本地未落盘的旧稿）。
    Returns list of (page, h1, one_liner, note_link)。
    """
    from articles.feishu import FeishuOutput
    f = FeishuOutput()
    if not f.is_available():
        return []
    ctok = f.ensure_series_node(series_title)
    if not ctok:
        return []
    res = f._run_cli_command(["wiki", "+node-list", "--parent-node-token", ctok,
                                "--space-id", f.wiki_space, "--as", "user",
                                "--json", "--page-all"])
    kids = (res.get("data", {}).get("nodes", [])) if res else []
    rows = []
    for k in kids:
        node_title = k.get("title", "")
        obj = k.get("obj_token", "")
        m = re.match(r'^第(\d{2})集_(.*)$', node_title)
        if not m:
            # 跳过非单集节点（如 00_系列总览），避免总览把自己列成"第00集"
            continue
        page = int(m.group(1))
        h1 = node_title
        one = "（待总结）"
        if obj:
            try:
                r = f._run_cli_command(["docs", "+fetch", "--doc", obj,
                                         "--doc-format", "markdown", "--scope", "full",
                                         "--as", "user", "--json"])
                content = (r.get("data", {}).get("document", {}).get("content", "")
                             if isinstance(r, dict) else "")
                h1 = _extract_h1(content) or node_title
                one = _extract_one_liner(content) or "（待总结）"
            except Exception:
                pass
        h1 = h1.replace('|', '/')
        one = one.replace('|', '/')
        note_link = f"[笔记](./{node_title}.md)"
        rows.append((page, h1, one, note_link))
    rows.sort(key=lambda r: r[0])
    return rows


def _render_series_overview(series_title: str, url: str, rows: list, learning_path_md: str = "") -> str:
    """纯渲染：把各集 rows + 学习路径段拼成总览 markdown（不读盘、不写盘）。

    rows: [(page, title, one_liner, note_link), ...]
    learning_path_md: AI 生成的「建议顺序 + 先修说明」段（空则省略该段）。
    """
    lines = [
        f"# {series_title} · 系列总览",
        "",
        f"> 系列链接：{url}",
        f"> 共 {len(rows)} 集（每集独立成篇，详见下方链接）",
        "",
        "## 各集导航",
        "",
        "| 集 | 标题 | 一句话核心结论 | 笔记 |",
        "| --- | --- | --- | --- |",
    ]
    for page, title, one, note_link in rows:
        lines.append(f"| 第{page:02d}集 | {title} | {one} | {note_link} |")
    lines += ["", "---", ""]
    if learning_path_md:
        lines += ["## 学习路径", "", learning_path_md, "", "---", ""]
    lines += [
        "*本总览由 blog-article-skill 自动生成，系列课每集总结后更新。*",
        "",
    ]
    return "\n".join(lines)


def _generate_series_overview(series_title: str, series_dir: str, url: str,
                              obsidian: bool = False) -> str:
    """系列课总览大纲：抽取各集 标题 + 一句话核心结论，生成 00_系列总览.md。

    用户规则：系列课总结必生成总览。
    - 本地落盘时：扫描 notes/<系列名>/ 各集 .md（原有逻辑）。
    - 有云同步（Obsidian/飞书）不写本地时：从飞书容器读回（云真值），
      避免依赖本地未落盘的旧稿。

    总览本身同步到所有已配置输出（Obsidian/飞书），本地仅当无云时落盘。
    Returns 本地绝对路径（无云时为 None）。
    """
    # 本地不落盘（有云同步）时，从飞书容器读回各集（云真值）；否则读本地
    if _local_write_enabled():
        ep_files = sorted(
            f for f in os.listdir(series_dir)
            if re.match(r'^第\d{2}集_.*\.md$', f) and not f.startswith('00_')
        )
        rows = []
        for f in ep_files:
            m = re.match(r'^第(\d{2})集_(.*?)(_raw)?\.md$', f)
            page = int(m.group(1)) if m else 0
            is_raw = f.endswith('_raw.md')
            path = os.path.join(series_dir, f)
            try:
                with open(path, encoding='utf-8') as fh:
                    md = fh.read()
            except Exception:
                md = ""
            title = _extract_h1(md) or (m.group(2) if m else f)
            one = '（待总结）' if is_raw else (_extract_one_liner(md) or '（待总结）')
            # 表格内禁用竖线，避免破坏 markdown 表格
            title = title.replace('|', '/')
            one = one.replace('|', '/')
            note_link = f"[笔记](./{f})"
            rows.append((page, title, one, note_link))
    else:
        # 有云同步：从飞书「系列名」容器读回（避免依赖本地未落盘的旧稿）
        rows = _read_series_from_feishu(series_title)
        if not rows:
            print("   ⚠️ 飞书未读到子节点，总览跳过（非致命）")
            return None
    rows.sort(key=lambda r: r[0])

    # 生成「学习路径」段（建议顺序 + 先修说明），基于各集标题+一句话结论
    learning_path_md = ""
    if rows:
        lp_prompt = (
            "你正在为一套系列课/合集生成「学习路径」说明。"
            "综合以下各集标题与一句话核心结论，输出一段中文 markdown（不要任何标题、不要代码块）："
            "① 建议学习顺序（若与发布顺序不同请指出并说明理由）；"
            "② 先修/依赖（哪些集是后续集的基础，必须前置）；"
            "③ 一句话课程脉络（这条线到底在讲什么）。"
            "保持精炼、可操作；不要复述各集结论。"
        )
        lp_input = "\n".join(f"第{r[0]:02d}集 {r[1]} —— {r[2]}" for r in rows)
        learning_path_md = _ai_summarize(lp_prompt, lp_input) or ""
        # 无 AI 时朴素降级：按发布顺序建议，保证「学习路径」段始终存在（不破总览结构）
        if not learning_path_md and len(rows) > 1:
            chain = " → ".join(f"第{r[0]:02d}集 {r[1]}" for r in rows)
            learning_path_md = (
                "（无 AI 生成路径，按发布顺序的朴素建议）\n"
                f"建议从「第{rows[0][0]:02d}集 {rows[0][1]}」开始，依次：{chain}。"
                "若某集正文标注了先修/依赖，请优先补齐前置集再进入后续。"
            )

    content = _render_series_overview(series_title, url, rows, learning_path_md=learning_path_md)

    overview_name = "00_系列总览.md"
    # 有云同步则不写本地 notes/（用户偏好）
    if _local_write_enabled():
        overview_path = os.path.join(series_dir, overview_name)
        with open(overview_path, "w", encoding="utf-8") as fh:
            fh.write(content)

    # 同步到所有已配置输出（Obsidian / 飞书等，非致命）
    try:
        series_folder = os.path.basename(series_dir)
        mgr = articles_main.OutputManager(obsidian=obsidian)
        for out in mgr.get_available_outputs():
            try:
                if out.save_series(content, overview_name, series_folder):
                    print(f"   🔗 已同步 {out.name} 总览：{series_folder}/{overview_name}")
            except Exception as e:
                print(f"   ⚠️ {out.name} 总览同步跳过（非致命）：{e}")
    except Exception as e:
        print(f"   ⚠️ 总览外部同步跳过（非致命）：{e}")

    return overview_path if _local_write_enabled() else None


def _build_subagent_prompt(note_type: str, raw_path: str, md_path: str,
                           url: str, page: int, part: str, series_title: str) -> str:
    """生成派发子 Agent 用的完整指令（含模板规范）。

    关键改进：把 prompts/templates.py 的统一规范（含 UNIVERSAL_RULES 的案例背景 /
    连贯性 / markdown 排版要求）完整注入子 Agent prompt，避免之前手写简版漏掉规范
    导致总结不连贯、缺背景、格式弱。子 Agent 各自独立上下文，互不干扰。

    Args:
        note_type: 笔记类型（key_points / structured / case / opinion）
        raw_path: 该集 raw 字幕文件绝对路径
        md_path:   目标笔记绝对路径（= raw_path 去掉 _raw）
        url:       系列课链接
        page:      集号（第几集）
        part:      分P 标题
        series_title: 系列名
    Returns: 可直接作为 Agent 工具 prompt 参数的字符串。
    """
    note_prompt = get_note_prompt(note_type)
    return (
        "你是「" + series_title + "」B站系列课的单集笔记总结子 Agent。你的上下文是独立的，"
        "只处理这一集，不要读取项目其他文件（除非本指令要求）。\n\n"
        "## 任务\n把下面这个 raw 字幕文件，提炼成一篇笔记 Markdown。\n\n"
        "## 输入文件\nRAW = `" + raw_path + "`\n"
        "用 Read 工具读取它。文件结构：前 7 行是元数据（`>` 开头：原始字幕 / 系列 / 分P / 链接），忽略；"
        "第 8 行 `---` 之后是字幕正文（口语化口播，含\"嗯/啊/呃/也就是说\"等填充词与重复，提炼时过滤）；"
        "你需要的链接在元数据里（`> 链接：...`），原样用在来源行。\n\n"
        "## 总结规范（必须 100% 遵守，不可精简、不可改格式、不可删减结构）\n"
        "以下是从项目统一模板中提取的硬性规范，优先级最高：\n\n"
        + note_prompt + "\n\n"
        "## 输出结构（顺序固定，严格按上方规范排版）\n"
        "1. `# <本集标题>`（标题从内容提炼，不要照抄文件名）\n"
        "2. 标签行（`#标签1 #标签2 ...`，可补本集相关标签）\n"
        "3. `**作者**：【作者未知】 | **来源链接**：[千刀千法 第" + str(page) + "集](" + url + "`)\n"
        "4. `**一句话核心结论**：` 这句最该被记住什么\n"
        "5. `**核心论点**`（3～5 条，每条 `**小标题**` + **背景因果展开** + 证据；"
        "**必须保留案例完整背景链条（背景→前因→经过→后果），段落间有逻辑衔接，不可只堆结论与零散数字**）\n"
        "6. `**金句摘录**`（3～5 句原文 `>` 引用，禁止改写）\n"
        "7. `**可行动项**`（2～4 条 `-` 列表，听完能马上做）\n"
        "8. `**适合谁看 / 不推荐谁看**`（一句话）\n"
        "9. 文末一句加粗的话收束全场\n\n"
        "## 写出与清理\n"
        "用 Write 工具写入：`" + md_path + "`（即去掉 `_raw` 四字）\n"
        "写完后删除 raw：用 Bash 执行 `rm \"" + raw_path + "\"`\n"
        "确认 raw 已删除。\n\n"
        "## 回报\n回复一句话：已写出 <MD文件名>，约 XXX 字，raw 已删除。"
    )


def _handle_bilibili_series(url: str, input_data: dict, series: dict = None):
    """B站系列课处理：Phase1 全抓取（已由 fetch 完成）→ Phase2 逐集总结。

    用户明确：先把所有集字幕一次性抓完，再逐集做总结（避免逐集重复建连浪费）。
    每集笔记存到 notes/<系列名>/ 文件夹下，文件名以「第XX集_分P标题」开头。

    Args:
        series: 已由 fetch.fetch_bilibili_series 抓好字幕的系列结构；为 None 时内部再抓一次。
    """
    if series is None:
        series = fetch.fetch_bilibili_series(url, lang=input_data.get("lang", "zh"))
    if not series:
        # 实际是单P，退回单视频逻辑
        return _handle_single_video(url, input_data)

    print(f"\n📚 识别为 B站系列课（{series.get('kind', '')}）「{series['series_title']}」，"
          f"已完成全部字幕抓取，开始逐集总结：{url}")
    series_title = series["series_title"]
    entries = series["entries"]
    # 优先用字幕抓取阶段提取到的 UP主（fetch_bilibili_series 已带 author），
    # 其次回退到调用方显式传入的 author
    author = series.get("author", "") or input_data.get("author", "")
    base_tags = input_data.get("tags", []) or [series_title]
    note_type_arg = input_data.get("note_type", "")
    force = input_data.get("force", False)
    obsidian = input_data.get("obsidian", False)

    print(f"\n📁 建立系列文件夹：notes/{_sanitize_filename(series_title)}/")
    series_dir = os.path.join(articles_main.NOTES_DIR, _sanitize_filename(series_title))
    os.makedirs(series_dir, exist_ok=True)

    print(f"🧠 Phase 2：逐集总结（共 {len(entries)} 集）...")
    results: List[Dict] = []
    degraded_any = False
    for idx, entry in enumerate(entries, 1):
        page = entry["page"]
        part = entry["part"]
        segs = entry["segments"]
        ep_title = entry["title"]
        print(f"\n[{idx}/{len(entries)}] 第{page}集：{part or '(无标题)'}")

        note_type = note_type_arg or classify_note_type(ep_title, segments_to_text(segs))
        final = _summarize_segments(segs, note_type, ep_title)
        base = f"第{page:02d}集_{_sanitize_filename(part or '未命名')}"

        if final is None:
            # AI 不可用：暂存原始字幕，交外层总结（不中断其他集）
            degraded_any = True
            raw_text = segments_to_text(segs)
            raw_path = os.path.join(series_dir, base + "_raw.md")
            with open(raw_path, "w", encoding="utf-8") as f:
                f.write(
                    f"> 原始字幕（AI 不可用，待外层总结）\n"
                    f"> 系列：{series_title}\n> 分P：第{page}集 {part}\n> 链接：{url}\n\n---\n\n"
                    + raw_text
                )
            results.append({"page": page, "part": part, "raw": os.path.relpath(raw_path, articles_main.NOTES_DIR), "degraded": True})
            print(f"   ⚠️ AI 不可用，原始字幕已暂存：{raw_path}")
            continue

        ep_tags = list(base_tags) + [_NOTE_TYPE_TAG.get(note_type, "视频笔记")]
        path = _save_series_note(final, series_dir, base, author, url, ep_tags, note_type, obsidian=obsidian)
        # 自愈：若此前降级留下 raw，成功总结后清除，避免半成品残留
        raw_path = os.path.join(series_dir, base + "_raw.md")
        if os.path.exists(raw_path):
            try:
                os.remove(raw_path)
            except Exception:
                pass
        results.append({"page": page, "part": part, "filename": os.path.relpath(path, articles_main.NOTES_DIR)})
        print(f"   ✅ 已保存：{path}")

    # 系列总览大纲（用户规则：系列课总结必生成，含各集导航 + 一句话核心结论）
    overview_path = _generate_series_overview(series_title, series_dir, url, obsidian=obsidian)
    print(f"   🧭 系列总览已生成：{overview_path}")

    return {
        "success": True,
        "message": (f"系列课「{series_title}」处理完成：{len(entries)} 集"
                    f"（{len([r for r in results if 'filename' in r])} 篇笔记"
                    f"{'，'+str(len([r for r in results if r.get('degraded')]))+' 集待外层总结' if degraded_any else ''}）"),
        "series_dir": series_dir,
        "series_title": series_title,
        "results": results,
        "overview": overview_path,
        "degraded_any": degraded_any,
    }


# ---------------------------------------------------------------------------
# 各类来源处理
# ---------------------------------------------------------------------------

def _handle_single_video(url: str, input_data: dict, suppress: bool = False):
    if not suppress:
        print(f"\n📺 获取视频字幕: {url}")
    result = fetch.fetch_transcript(url)
    if result is None:
        # 自动 ASR 兜底（用户规则 2026-08-06：抓不到字幕即自动走 ASR）
        # 下载音频 → 本地 faster-whisper 转写，成功则继续总结并落盘（默认飞书，带 obsidian 时双写）。
        print("   ⚠️ 无可用字幕，自动走 ASR 兜底（下载音频 → 本地 Whisper 转写）...")
        asr_res = asr.transcribe_video(url, lang=input_data.get("lang", "zh"))
        if asr_res is None:
            return {
                "success": False,
                "message": "该视频无可用字幕，且 ASR 兜底也失败（音频下载或本地转写未成功，"
                           "可能需 B站登录态 / 网络受限）。可稍后重试，或粘贴字幕文本 / 本地文件处理。",
            }
        title, segments, asr_author = asr_res
        author = asr_author or input_data.get("author", "")
        input_data = {**input_data, "author": author}
        return _finalize_single(title, segments, url, input_data)
    title, segments, fetched_author = result
    if not segments:
        return {
            "success": False,
            "message": "该视频无 CC 字幕。PRD 建议：本地文件 → ASR 兜底，或粘贴字幕文本。"
                       "可用 ASR 模式处理本地视频文件。",
        }
    # P4：多模态画面理解（best-effort，非阻断）
    visual_context = ""
    if input_data.get("multimodal"):
        print("   🖼️ 多模态画面理解（best-effort）...")
        visual_context = multimodal.analyze(url, note_type=input_data.get("note_type", "")) or ""
    # 优先用字幕抓取阶段提取到的作者（单视频 B站现可透传 UP主），CLI 显式传入次之
    author = fetched_author or input_data.get("author", "")
    input_data = {**input_data, "author": author}
    return _finalize_single(title, segments, url, input_data, visual_context=visual_context)


def _handle_local_file(path: str, input_data: dict):
    print(f"\n🎬 本地文件 ASR 转写: {path}")
    title, segments = asr.transcribe_file(path)
    if segments is None:
        return {"success": False, "message": "本地文件 ASR 转写失败（需 yt-dlp + ffmpeg + faster-whisper）。"}
    # P4：多模态画面理解（best-effort，非阻断）
    visual_context = ""
    if input_data.get("multimodal"):
        print("   🖼️ 多模态画面理解（best-effort）...")
        visual_context = multimodal.analyze(path, note_type=input_data.get("note_type", "")) or ""
    return _finalize_single(title or os.path.basename(path), segments, "", input_data, visual_context=visual_context)


def _handle_transcript_text(transcript: str, url: str, input_data: dict):
    print("\n📝 直接总结字幕文本（P1）")
    return _finalize_single(input_data.get("original_title", ""), transcript, url, input_data)


def _finalize_single(title, segments, url, input_data, visual_context: str = ""):
    author = input_data.get("author", "")
    tags = input_data.get("tags", []) or []
    note_type = input_data.get("note_type", "")
    force = input_data.get("force", False)
    publish_time = input_data.get("publish_time", 0)
    folder = input_data.get("folder", "")
    obsidian = input_data.get("obsidian", False)

    filename, final_text, degraded, article_content, note_type = _summarize_and_save(
        segments, url, title, author, tags, note_type, force,
        visual_context=visual_context, publish_time=publish_time, folder=folder,
        obsidian=obsidian
    )

    if degraded:
        # 与 articles 降级对齐：字幕原文落 raw 文件，外层/监控可 Read 后按模板总结
        raw_file = articles_main.save_raw_content_to_file(article_content, title=title)
        return {
            "success": True,
            "need_continue_summary": True,
            "message": "✅ 已准备好字幕内容，等待执行模型（Agent）按笔记模板总结",
            "article_content": article_content,
            "note_type": note_type,
            "prompt": get_note_prompt(note_type) + QUALITY_GATE_SELFCHECK,
            "original_url": url,
            "original_title": title,
            "author": author,
            "tags": tags,
            "raw_file": raw_file,
            "folder": folder,
        }

    return {
        "success": True,
        "message": "视频总结已自动保存！",
        "filename": filename,
        "content": final_text,
    }


def _handle_playlist(url: str, input_data: dict):
    print(f"\n📚 解析 playlist / 合集: {url}")
    entries = fetch.fetch_playlist(url)
    if not entries:
        return {"success": False, "message": "playlist 解析失败或无条目。"}

    results: List[Dict] = []
    texts: List[str] = []
    first_degraded = None
    for i, entry in enumerate(entries, 1):
        print(f"\n[{i}/{len(entries)}] {entry.get('title', entry['url'])}")
        r = _handle_single_video(entry["url"], input_data, suppress=True)
        if r.get("need_continue_summary"):
            # AI 不可用：返回第一篇降级，交由外层
            first_degraded = r
            break
        if r.get("success") and r.get("filename"):
            results.append({"title": entry.get("title", ""), "filename": r["filename"]})
            if r.get("content"):
                texts.append(r["content"])
        else:
            results.append({"title": entry.get("title", ""), "error": r.get("message", "")})

    # 若中途降级，直接返回降级
    if first_degraded:
        return first_degraded

    # 可选系列总览
    overview_file = None
    if texts and (input_data.get("overview") or len(entries) > 1):
        print("\n🧭 生成系列总览...")
        overview_prompt = (
            "你正在为一套系列视频/合集生成「系列总览」笔记。"
            "请综合以下各集小结，提炼：①系列主题与主线 ②各集要点串联 ③适合人群与学习路径 ④核心结论。"
            "保持结构化、控制篇幅。"
        )
        ov = _ai_summarize(overview_prompt, "\n\n===\n\n".join(texts[:12]))
        if ov:
            label = _NOTE_TYPE_TAG.get(input_data.get("note_type", "") or "structured", "结构化复盘")
            formatted, overview_file = save_summarized_article(
                ov, original_url=url, author=input_data.get("author", ""),
                tags=[label, "系列总览"], original_title="系列总览", note_type="structured",
                obsidian=input_data.get("obsidian", False)
            )

    return {
        "success": True,
        "message": f"playlist 处理完成：{len([r for r in results if 'filename' in r])} 篇笔记",
        "results": results,
        "overview": overview_file,
    }


# ---------------------------------------------------------------------------
# 对外主入口
# ---------------------------------------------------------------------------

def summarize_video(input_data: dict) -> dict:
    """视频总结入口，接口形态对齐 articles.skill_main。

    input_data 字段：
        - url:            YouTube/Bilibili 单视频 或 playlist/合集 链接
        - file / path:    本地视频/音频文件路径（→ ASR）
        - transcript / content: 字幕或转写文本（P1）
        - author / tags / original_title / note_type: 元数据
        - playlist:       True 强制按 playlist 处理
        - overview:       True 生成系列总览（playlist 模式）
        - force:          忽略去重强制重跑
    """
    url = input_data.get("url", "")
    file_path = input_data.get("file", "") or input_data.get("path", "")
    transcript = input_data.get("transcript", "") or input_data.get("content", "")

    if url and _looks_like_playlist(url, input_data):
        return _handle_playlist(url, input_data)

    if url and fetch.is_bilibili(url):
        # 系列课（ugc_season 或 多P）：先批量抓取再逐集总结；单P 走单视频逻辑
        series = fetch.fetch_bilibili_series(url, lang=input_data.get("lang", "zh"))
        if series:
            return _handle_bilibili_series(url, input_data, series)
        return _handle_single_video(url, input_data)

    if url and fetch.is_youtube(url):
        return _handle_single_video(url, input_data)

    if file_path and os.path.exists(file_path):
        return _handle_local_file(file_path, input_data)

    if transcript and transcript.strip():
        return _handle_transcript_text(transcript, url, input_data)

    return {
        "success": False,
        "message": "请提供视频 URL / 本地文件路径 / transcript 文本。",
    }
