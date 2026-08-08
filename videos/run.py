"""
videos/run.py — 视频总结命令行入口

用法：
  # YouTube / Bilibili 单视频（自动抓 CC 字幕 → 总结；无字幕则自动下载音频用本地 Whisper 转写）
  python videos/run.py --url "https://www.youtube.com/watch?v=xxxx"

  # playlist / 合集（逐条总结 + 系列总览）
  python videos/run.py --url "https://www.youtube.com/playlist?list=xxxx" --overview

  # 本地视频/音频文件（ASR 转写 → 总结，需 ffmpeg + faster-whisper）
  python videos/run.py --file "lecture.mp4"

  # 直接给字幕文本（P1）
  python videos/run.py --content "字幕文本..."

  # 指定笔记类型 / 作者 / 标签
  python videos/run.py --url "..." --note-type key_points --author "作者" --tags "AI,教程"
"""

import sys
import os
import argparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)


def main():
    parser = argparse.ArgumentParser(description='blog-article-skill 视频总结工具')
    parser.add_argument('--url', '-u', type=str, default='', help='YouTube/Bilibili 视频或 playlist 链接')
    parser.add_argument('--file', '-f', type=str, default='', help='本地视频/音频文件路径（ASR 模式）')
    parser.add_argument('--content', '-c', type=str, default='', help='字幕/转写文本（P1）')
    parser.add_argument('--author', '-a', type=str, default='', help='作者信息')
    parser.add_argument('--tags', '-t', type=str, default='', help='标签，逗号分隔')
    parser.add_argument('--note-type', '-n', type=str, default='', help='笔记类型：structured / key_points / case / opinion')
    parser.add_argument('--playlist', action='store_true', help='强制按 playlist 处理')
    parser.add_argument('--overview', action='store_true', help='playlist 模式：额外生成系列总览')
    parser.add_argument('--force', action='store_true', help='忽略去重强制重跑')
    parser.add_argument('--obsidian', action='store_true', help='同时写入 Obsidian（默认只写飞书）')
    parser.add_argument('--lang', type=str, default='zh', help='B站字幕语言（默认 zh，可选 en 等）')

    args = parser.parse_args()

    if not (args.url or args.file or args.content):
        print("用法:")
        print('  python videos/run.py --url "https://youtube.com/watch?v=xxxx"')
        print('  python videos/run.py --url "https://youtube.com/playlist?list=xxxx" --overview')
        print('  python videos/run.py --file "lecture.mp4"')
        print('  python videos/run.py --content "字幕文本..."')
        return 1

    tags = [t.strip() for t in args.tags.split(',')] if args.tags else []

    from videos import summarize_video
    result = summarize_video({
        'url': args.url,
        'file': args.file,
        'content': args.content,
        'author': args.author,
        'tags': tags,
        'note_type': args.note_type,
        'playlist': args.playlist,
        'overview': args.overview,
        'force': args.force,
        'obsidian': args.obsidian,
        'lang': args.lang,
    })

    if not result.get('success'):
        print(f"\n❌ 失败: {result.get('message', '未知错误')}")
        return 1

    if result.get('need_continue_summary'):
        print("\n⚠️ 未配置外部 AI Provider，已准备好字幕内容，等待外层对话（Agent）按笔记模板总结。")
        print(f"   视频标题: {result.get('original_title', '未知')}")
        print(f"   链接: {result.get('original_url', '未知')}")
        if result.get('raw_file'):
            print(f"   📄 原始字幕文件: {result['raw_file']}（可直接 Read 读取）")
        # 关键：把字幕正文也吐到 stdout，确保经 Bash 执行的执行模型能直接拿到上下文，
        # 不用再去反查/反读文件（曾因 Bash 子进程未回传字幕而丢上下文）。
        content = result.get('article_content') or ''
        if content:
            print("\n" + "=" * 60)
            print("【原始字幕内容 BEGIN】（供外层模型总结，无需再读文件）")
            print(content)
            print("【原始字幕内容 END】" + "=" * 38)
        return 0

    if result.get('results') is not None:
        print(f"\n✅ {result.get('message')}")
        if result.get('series_dir'):
            print(f"   📂 系列文件夹：{result['series_dir']}")
        for r in result['results']:
            if r.get('degraded'):
                print(f"   ⚠️ 第{r.get('page', '?')}集 {r.get('part', '')}: AI 不可用，原始字幕已存 {r.get('raw')}")
            elif 'filename' in r:
                print(f"   📄 第{r.get('page', '?')}集 {r.get('part', '')} → {r['filename']}")
            else:
                print(f"   ⚠️ 第{r.get('page', '?')}集 {r.get('part', '')}: {r.get('error', '')}")
        if result.get('overview'):
            print(f"   🧭 系列总览 → {result['overview']}")
        return 0

    print(f"\n✅ 视频总结完成")
    print(f"   文件名: {result.get('filename')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
