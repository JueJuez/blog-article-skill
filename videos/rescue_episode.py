"""videos/rescue_episode.py — 系列课「单集救回」单一入口（优化 C）。

取代此前散落、易与真实 API 漂移的临时脚本（retry_missing.py 等）：
给定一个系列 + 集号 + 该集链接/ BV 号，自动走完整字幕链路
（原生 API → yt-dlp → ASR 兜底，fetch.fetch_bilibili_transcript 已实现），
把转写稿写成与正常降级路径**完全一致格式**的 raw 文件（前 7 行 `>` 元数据 + `---` + 正文），
并更新 manifest 状态 = raw_ready，供执行模型（Agent）按现有合约总结成 .body.md。

用法：
  # ugc_season（每集独立 BV）：传 --bvid
  python videos/rescue_episode.py --series "股市系统教学" --page 5 --bvid BVxxxx --lang zh
  # 多P 视频（同 BV 多 page）：传 --url（系列链接）+ ?p=N 自动拼
  python videos/rescue_episode.py --series "xxx" --page 3 --url "https://www.bilibili.com/video/BVyyyy"
"""
import os
import sys
import re
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

from articles import main as articles_main
from shared import series_naming as sn
from shared import series_manifest as sm
from videos import fetch, asr

import json as _json
import time as _time

_PENDING_SERIES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "monitors", "pending_series.json")


def _register_pending_series(series_title: str, series_dir: str, url: str, author: str,
                             raw_rel: str) -> None:
    """（闭环补全）把救回的系列登记到 monitors/pending_series.json，使 apply_pending_series.py
    能接管落盘。

    此前 rescue 只更新 manifest，但不写 pending_series.json；29-38 集 drain 后该队列变空，
    导致后续 5/15/18 即便产出 body 也无脚本可触发落飞书（与 monitors/run.py 的降级登记对称）。
    本函数幂等：同系列不重复登记，raw 按相对路径去重合并。
    """
    try:
        if os.path.exists(_PENDING_SERIES_PATH):
            data = _json.load(open(_PENDING_SERIES_PATH, encoding="utf-8"))
        else:
            data = []
        if not isinstance(data, list):
            data = []
        entry = {
            "series_title": series_title,
            "series_dir": series_dir,
            "url": url,
            "author": author,
            "degraded_raws": [raw_rel] if raw_rel else [],
            "queued_at": int(_time.time()),
        }
        for d in data:
            if d.get("series_title") == series_title:
                merged = set(d.get("degraded_raws", [])) | set(entry["degraded_raws"])
                d["degraded_raws"] = sorted(merged)
                # 同步最新 url/author（若之前登记时缺）
                d["url"] = d.get("url") or url
                d["author"] = d.get("author") or author
                d["series_dir"] = d.get("series_dir") or series_dir
                break
        else:
            data.append(entry)
        _json.dump(data, open(_PENDING_SERIES_PATH, "w", encoding="utf-8"),
                   ensure_ascii=False, indent=2)
        print(f"   🤖 已登记到 pending_series.json（供 apply_pending_series.py 落盘）：{series_title}")
    except Exception as e:
        print(f"   ⚠️ 登记 pending_series.json 失败（非致命）：{e}")


def _segments_to_text(segs) -> str:
    """把 segments（list[dict] 或 str）转纯文本。内联避免依赖 videos.main 造成循环导入。"""
    if segs is None:
        return ""
    if isinstance(segs, str):
        return segs
    if isinstance(segs, list):
        parts = []
        for s in segs:
            if isinstance(s, dict):
                parts.append(s.get("text", ""))
            else:
                parts.append(str(s))
        return "\n".join(p for p in parts if p.strip())
    return str(segs)


def _sanitize(name: str) -> str:
    if not name:
        return "未命名"
    s = re.sub(r'[\\/:*?"<>|\n\r\t]', '_', name).strip()
    s = re.sub(r'\s+', ' ', s)
    return s[:80] or "未命名"


def rescue(series_title: str, page: int, bvid: str = "", series_url: str = "",
           lang: str = "zh", author: str = "", expected_total: int = 0) -> dict:
    # 1) 拼该集专属链接
    if bvid:
        ep_url = f"https://www.bilibili.com/video/{bvid}"
    elif series_url:
        ep_url = f"{series_url.split('?')[0]}?p={page}"
    else:
        return {"ok": False, "reason": "需提供 --bvid 或 --url"}

    print(f"🔧 救回 第{page}集（{series_title}）\n   URL: {ep_url}")

    # 2) ASR 依赖预检（优化 F）：缺依赖打印一行安装命令，不静默崩
    ok, missing = asr.check_asr_deps()
    if not ok:
        print("   ⚠️ ASR 依赖缺失：" + ", ".join(missing))
        print("   → 安装：pip install " + " ".join(missing))
        print("   （原生 API / yt-dlp 仍会尝试；仅当全失败才需要 ASR）")

    # 3) 抓字幕（含 3 层兜底，ASR 已强制 CUDA）
    res = fetch.fetch_bilibili_transcript(ep_url, lang=lang, page=page)
    if not res:
        return {"ok": False, "reason": "字幕与 ASR 均失败（检查 B站登录态 / 网络）"}

    title, segs, fetched_author = res
    author = author or fetched_author
    text = _segments_to_text(segs)
    if not text.strip():
        return {"ok": False, "reason": "转写结果为空"}

    # part 取标题（ugc_season 的 title 即分P标题；多P 则带主标题，需裁）
    part = title
    # 多P 标题形如「主标题 - 分P」，仅取分P 段
    if " - " in part:
        cand = part.split(" - ", 1)[1].strip()
        if cand:
            part = cand

    # 4) 写 raw（与 degraded 路径同格式：元数据头 + --- + 正文）
    safe_part = _sanitize(part)
    base = sn.normalized_base(page, safe_part)
    series_dir = os.path.join(articles_main.NOTES_DIR, _sanitize(series_title))
    os.makedirs(series_dir, exist_ok=True)
    raw_abs = os.path.join(series_dir, base + "_raw.md")
    rel = os.path.relpath(raw_abs, articles_main.NOTES_DIR)
    header = (
        f"> 原始字幕（ASR/兜底转写，待外层总结）\n"
        f"> 系列：{series_title}\n"
        f"> 分P：第{page}集 {part}\n"
        f"> 作者：{author}\n"
        f"> 链接：{ep_url}\n\n---\n\n"
    )
    with open(raw_abs, "w", encoding="utf-8") as f:
        f.write(header + text)
    print(f"   ✅ 已写 raw（{len(text)} 字）：{rel}")

    # 5) 更新 manifest（状态机续跑信号）
    m = sm.load_or_init(series_title, url=series_url or ep_url, author=author,
                        notes_dir=articles_main.NOTES_DIR, expected_total=expected_total)
    m.upsert(page, part=part, bvid=bvid, url=ep_url, raw=rel, state=sm.RAW_READY)
    m.save()

    # 5.5) 登记到系列待落盘队列（闭环补全：否则 apply_pending_series.py 找不到本系列）
    series_dir = os.path.join(articles_main.NOTES_DIR, _sanitize(series_title))
    _register_pending_series(series_title, series_dir, series_url or ep_url, author, rel)

    body_abs = sn.body_path(raw_abs)
    return {
        "ok": True,
        "page": page,
        "part": part,
        "raw": rel,
        "body": os.path.relpath(body_abs, articles_main.NOTES_DIR),
        "manifest_state": m.state(page),
        "next": f"派子 Agent 读 {rel} 总结成 {os.path.relpath(body_abs, articles_main.NOTES_DIR)}，"
                f"再跑 monitors/apply_pending_series.py 落盘",
    }


def _video_duration(url: str) -> float:
    """用 yt-dlp 只取视频时长（秒），不下载。拿不到返回 0。"""
    import subprocess
    try:
        out = subprocess.run([sys.executable, "-m", "yt_dlp", "--print",
                              "%(duration)s", "--no-warnings", url],
                             capture_output=True, text=True, timeout=60)
        s = out.stdout.strip()
        return float(s) if s else 0.0
    except Exception:
        return 0.0


def _check(series_title: str, page: int, bvid: str, series_url: str, lang: str) -> None:
    """预检模式：自报模型位置/大小、CUDA、依赖、登录态、视频时长+推荐超时。

    直接消除「不知道模型在哪」「不知道下一步」两类误判——所有关键信息一次性打印。
    """
    url = f"https://www.bilibili.com/video/{bvid}" if bvid else (
        f"{series_url.split('?')[0]}?p={page}" if series_url else "")
    print(f"🔍 预检 第{page}集（{series_title}）\n   URL: {url}")
    # 1) ASR 模型位置 + 大小（核心：模型默认不在 ~/.cache/huggingface，而在 ~/.cache/asr_whisper）
    mp = asr._resolve_local_model_dir("medium")
    binp = os.path.join(mp, "model.bin") if mp else ""
    if os.path.exists(binp):
        print(f"   ✅ ASR 模型：{binp} （{os.path.getsize(binp)/1024/1024:.1f} MB）")
    else:
        print(f"   ⚠️ ASR 模型缺失：{binp}（需先下载 faster-whisper medium 到该目录）")
    # 2) CUDA 运行库（cublas 等需注入 PATH / DLL 搜索路径）
    asr._ensure_cuda_dlls()
    try:
        import ctranslate2
        n = ctranslate2.get_cuda_device_count()
        print(f"   ✅ CUDA 设备数：{n}（cublas 路径已注入 PATH，GPU 转写可用）" if n
              else "   ⚠️ CUDA 设备数：0（将走 CPU，极慢，建议排查 nvidia-* 运行库）")
    except Exception as e:
        print(f"   ⚠️ CUDA 检测失败：{e}")
    # 3) 依赖预检
    ok, missing = asr.check_asr_deps()
    print(f"   {'✅' if ok else '⚠️'} ASR 依赖：{'OK' if ok else '缺失 ' + ', '.join(missing)}")
    # 4) 登录态
    cookie = os.environ.get("BILI_COOKIE", "")
    print(f"   {'✅' if cookie else '⚠️'} BILI_COOKIE：{'已设置' if cookie else '未设置（可能抓不到高清/登录态字幕，但不影响 ASR）'}")
    # 5) 视频时长 + 推荐超时
    dur = _video_duration(url) if url else 0.0
    rec = int(dur * 3 + 600) if dur else 10800
    if dur:
        print(f"   ℹ️ 视频时长：{dur/60:.1f} 分钟 → 推荐 ASR_WALL_TIMEOUT={rec}（≥时长×3，留足 GPU 余量）")
    else:
        print(f"   ℹ️ 视频时长：未知（手动设 ASR_WALL_TIMEOUT，长视频按 时长×3 估；本命令默认 {rec}）")
    ready = os.path.exists(binp) and ok
    print(f"\n   {'✅ 环境就绪，可直接救回' if ready else '⚠️ 需先处理上方告警'} → 救回命令：")
    print(f"   ASR_WALL_TIMEOUT={rec} {sys.executable} -u videos/rescue_episode.py "
          f"--series \"{series_title}\" --page {page} "
          f"{('--bvid ' + bvid) if bvid else ('--url ' + series_url)} --lang {lang}")


def _status(series_title: str) -> None:
    """状态模式：打印 manifest 概览 + 缺口（待救回集）+ 逐集救回命令。

    让「下一步做什么」显式化——不用再猜漏了哪几集、该跑什么。
    """
    m = sm.load_or_init(series_title, reconcile=True)
    print(m.summary_line())
    gaps = m.gap_pages()
    if not gaps:
        print("\n✅ 无缺口（期望集数内全部已 raw_ready+）")
        return
    print(f"\n⚠️ 缺口（共 {len(gaps)} 集）：{gaps}")
    print("逐集救回命令（把 <该集BV> 换成真实 BV 号）：")
    for p in gaps:
        print(f"  ASR_WALL_TIMEOUT=10800 {sys.executable} -u videos/rescue_episode.py "
              f"--series \"{series_title}\" --page {p} --bvid <该集BV> --lang zh")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", required=True)
    ap.add_argument("--page", type=int, default=0)
    ap.add_argument("--bvid", default="")
    ap.add_argument("--url", default="")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--author", default="")
    ap.add_argument("--expected-total", type=int, default=0,
                    help="系列总集数（填入后做缺口检测，防误报完成）")
    ap.add_argument("--check", action="store_true",
                    help="预检模式：打印模型位置/大小、CUDA、依赖、时长+推荐超时，不实际抓取")
    ap.add_argument("--status", action="store_true",
                    help="状态模式：打印缺口（待救回集）与逐集救回命令")
    args = ap.parse_args()

    if args.status:
        _status(args.series)
        return
    if args.check:
        if not args.page:
            print("❌ --check 需配合 --page（以及 --bvid 或 --url）")
            sys.exit(1)
        _check(args.series, args.page, args.bvid, args.url, args.lang)
        return
    if not args.page:
        print("❌ 救回需 --page（以及 --bvid 或 --url）")
        sys.exit(1)

    r = rescue(args.series, args.page, bvid=args.bvid, series_url=args.url,
               lang=args.lang, author=args.author, expected_total=args.expected_total)
    if not r.get("ok"):
        print(f"❌ 救回失败：{r.get('reason')}")
        sys.exit(1)
    print("\n=== rescue 完成 ===")
    print(f"第{r['page']}集《{r['part']}》")
    print(f"  raw : {r['raw']}")
    print(f"  body: {r['body']}")
    print(f"  state: {r['manifest_state']}")
    print(f"  → {r['next']}")


if __name__ == "__main__":
    main()
