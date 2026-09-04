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
import json
import time
import random
import argparse
from collections import deque

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)


def _run_batch(args) -> int:
    """批量模式（2026-09-03）：单进程逐条处理 N 个视频。

    为什么有这个模式：编排层 fetch_up_range 原先每条视频起一个子进程，
    129 集 = 129 次 Python 冷启动 + 全包 import + 重读 .env。批量模式把
    N 条塞进一个进程（--batch-file），冷启动摊销 1/N，条间延迟/每小时预算
    也移到进程内（请求节奏更真实、编排层代码更简单）。

    输入 JSON：[{"idx": 1, "url": "https://www.bilibili.com/video/BV..",
                 "title": "...", "author": "UP名", "publish_time": 0, "lang": "zh"}]

    结束时打印 BATCH_RESULTS_START/END 包裹的 JSON（videos 结果 + http_trace 请求级
    追踪），编排层据此入队 / 写结构化运行日志（请求密度、412/413/429、超时分布）。
    """
    from videos import summarize_video
    from videos import fetch as _fetch

    with open(args.batch_file, encoding="utf-8") as f:
        entries = json.load(f)

    window = deque()  # 已完成条目时间戳（滑动 1h 预算窗口）
    max_per_hour = args.max_per_hour
    out = []
    for n, ent in enumerate(entries):
        if n > 0:
            delay = random.uniform(args.delay_min, args.delay_max)
            print(f"[delay] 等待 {delay:.1f}s 后继续…", flush=True)
            time.sleep(delay)
        # 每小时预算（滑动窗口，满额睡眠到窗口释放）
        now = time.time()
        while window and now - window[0] >= 3600:
            window.popleft()
        while max_per_hour and len(window) >= max_per_hour:
            sleep_s = max(1.0, window[0] + 3600 - now + random.uniform(1, 5))
            print(f"[rate] 每小时 {max_per_hour} 条预算已满，睡眠 {sleep_s:.0f}s", flush=True)
            time.sleep(sleep_s)
            now = time.time()
            while window and now - window[0] >= 3600:
                window.popleft()

        url = ent.get("url", "")
        rec = {"idx": ent.get("idx"), "url": url, "title": ent.get("title", ""),
               "ok": False, "series": False, "risk412": False,
               "raw_file": "", "error": "", "ms": 0}
        prev_risk = _fetch.risk_412_hit()
        t0 = time.time()
        try:
            r = summarize_video({
                "url": url,
                "author": ent.get("author", ""),
                "publish_time": ent.get("publish_time", 0),
                "folder": ent.get("folder", ""),
                "lang": ent.get("lang", "zh"),
            })
            rec["ok"] = bool(r.get("success"))
            rec["series"] = bool(r.get("series_dir"))
            rec["raw_file"] = r.get("raw_file", "") or ""
            if not rec["ok"]:
                rec["error"] = (r.get("message", "") or "")[:300]
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"[:300]
        rec["ms"] = int((time.time() - t0) * 1000)
        rec["risk412"] = bool(_fetch.risk_412_hit() and not prev_risk)
        out.append(rec)
        window.append(time.time())
        print(f"[batch] #{rec['idx']} {'OK' if rec['ok'] else 'FAIL'}"
              f"{' series' if rec['series'] else ''}"
              f"{' RISK412' if rec['risk412'] else ''} {rec['ms']}ms"
              + (f" err={rec['error'][:120]}" if rec['error'] else ""), flush=True)

    print("=====BATCH_RESULTS_START=====")
    print(json.dumps({"videos": out, "http_trace": _fetch.get_http_trace()},
                     ensure_ascii=False))
    print("=====BATCH_RESULTS_END=====")
    return 0


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
    parser.add_argument('--batch-file', type=str, default='',
                        help='批量模式：JSON 文件（[{idx,url,title,author,publish_time,lang}]），'
                             '单进程逐条处理，结束打印 BATCH_RESULTS JSON')
    parser.add_argument('--delay-min', type=float, default=15.0, help='批量模式条间最小延迟秒')
    parser.add_argument('--delay-max', type=float, default=30.0, help='批量模式条间最大延迟秒')
    parser.add_argument('--max-per-hour', type=int, default=100, help='批量模式每小时条数预算')

    args = parser.parse_args()

    if args.batch_file:
        return _run_batch(args)

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
