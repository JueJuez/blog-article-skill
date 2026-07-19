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

降级：无可用 AI 时返回 need_continue_summary + prompt + 字幕文本，交外层对话。
"""

import os
from typing import Optional, List, Dict, Any

import articles.main as articles_main
from articles.main import (
    call_ai_summary_with_meta,
    save_summarized_article,
)
from prompts.templates import get_note_prompt
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

    return two_stage_summarize(chunks, summarize_fn, merge_fn)


def _summarize_and_save(segments, source_url: str, title: str, author: str,
                        tags: list, note_type: str, force: bool, visual_context: str = ""):
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
        tags=save_tags, original_title=title or "视频总结", note_type=note_type
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
# 各类来源处理
# ---------------------------------------------------------------------------

def _handle_single_video(url: str, input_data: dict, suppress: bool = False):
    if not suppress:
        print(f"\n📺 获取视频字幕: {url}")
    title, segments = fetch.fetch_transcript(url)
    if segments is None:
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

    filename, final_text, degraded, article_content, note_type = _summarize_and_save(
        segments, url, title, author, tags, note_type, force, visual_context=visual_context
    )

    if degraded:
        return {
            "success": True,
            "need_continue_summary": True,
            "message": "⚠️ AI Provider 暂不可用，已准备好字幕内容，请外层总结",
            "article_content": article_content,
            "note_type": note_type,
            "prompt": get_note_prompt(note_type),
            "original_url": url,
            "original_title": title,
            "author": author,
            "tags": tags,
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
                tags=[label, "系列总览"], original_title="系列总览", note_type="structured"
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

    if url and (fetch.is_youtube(url) or fetch.is_bilibili(url)):
        return _handle_single_video(url, input_data)

    if file_path and os.path.exists(file_path):
        return _handle_local_file(file_path, input_data)

    if transcript and transcript.strip():
        return _handle_transcript_text(transcript, url, input_data)

    return {
        "success": False,
        "message": "请提供视频 URL / 本地文件路径 / transcript 文本。",
    }
