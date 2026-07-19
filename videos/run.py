"""
videos/run.py — 视频总结命令行入口

用法：
  # YouTube / Bilibili 单视频（自动抓 CC 字幕 → 总结）
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
    })

    if not result.get('success'):
        print(f"\n❌ 失败: {result.get('message', '未知错误')}")
        return 1

    if result.get('need_continue_summary'):
        print("\n⚠️ 未配置外部 AI Provider，已准备好字幕内容，等待外层对话总结。")
        print(f"   视频标题: {result.get('original_title', '未知')}")
        print(f"   链接: {result.get('original_url', '未知')}")
        return 0

    if result.get('results') is not None:
        print(f"\n✅ {result.get('message')}")
        for r in result['results']:
            if 'filename' in r:
                print(f"   📄 {r.get('title', '')} → {r['filename']}")
            else:
                print(f"   ⚠️ {r.get('title', '')}: {r.get('error', '')}")
        if result.get('overview'):
            print(f"   🧭 系列总览 → {result['overview']}")
        return 0

    print(f"\n✅ 视频总结完成")
    print(f"   文件名: {result.get('filename')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
