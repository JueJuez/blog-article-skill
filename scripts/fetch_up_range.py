#!/usr/bin/env python3
"""批量抓取某 UP 视频字幕（idx 切片），逐条跑 videos/run.py，结果汇总落盘。
FORCE_AGENT_MODE 下 run.py 只抓字幕不总结；本脚本收集 degraded 信息供子 Agent 总结。
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST_PATH = os.path.join(ROOT, "notes", "_scraped", "bili_1750569201_videos.json")
OUT_PATH = os.path.join(ROOT, "notes", "_scraped", "趋势浪子_fetch_results.json")
AUTHOR = "趋势浪子"


def main():
    start, end = int(sys.argv[1]), int(sys.argv[2])
    items = json.load(open(LIST_PATH, encoding="utf-8"))["items"]
    results = []
    if os.path.exists(OUT_PATH):
        results = json.load(open(OUT_PATH, encoding="utf-8"))
    done_urls = {r["url"] for r in results}

    for it in items:
        if not (start <= it["idx"] <= end):
            continue
        url = f"https://www.bilibili.com/video/{it['bvid']}"
        if url in done_urls:
            print(f"[skip] #{it['idx']} 已有结果", flush=True)
            continue
        print(f"\n===== #{it['idx']}/{it['title'][:40]} {url} =====", flush=True)
        try:
            p = subprocess.run([sys.executable, os.path.join(ROOT, "videos", "run.py"),
                                "--url", url, "--author", AUTHOR],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=900, cwd=ROOT)
            tail = (p.stdout or "")[-3000:]
            err_tail = (p.stderr or "")[-1500:]
        except subprocess.TimeoutExpired:
            tail, err_tail = "", "TIMEOUT 900s"
        except Exception as e:
            tail, err_tail = "", f"{type(e).__name__}: {e}"
        print(tail[-1200:], flush=True)
        if err_tail.strip():
            print("[stderr]", err_tail[-400:], flush=True)
        results.append({"idx": it["idx"], "url": url, "title": it["title"],
                        "rc": p.returncode if 'p' in dir() else -1,
                        "stdout_tail": tail, "stderr_tail": err_tail})
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"\n✅ 批量抓取完成 [{start}-{end}]，共 {len(results)} 条结果 → {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
