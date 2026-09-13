#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/land_scys_by_key.py — scys 待总结队列的单线程落盘+出队（Obsidian 版，按 topicId 稳定键）。

这是 scys 批量总结落盘的**现役入口**（2026-09-13 从 _tmp 正式化至此）：
- 读取 `notes/_scraped/scys/pending_summaries.json` 队列；
- 按 `entry["topicId"]` **稳定键**定位子 Agent 写好的 `temp/sum_<topicId>.md`（彻底规避
  「出队导致队列位置平移 → 按位置 index 读 temp 文件错配 URL」的索引漂移坑）；
- 调 `save_summary_only(obsidian=True)` 落本地 Obsidian，落盘成功才从队列出队（出队也按
  topicId 定位，url 兜底）。

前置：scys 补齐/增量抓取已把每条总结写到 `temp/sum_<topicId>.md`（文件名 = 队列 topicId）。
落盘目标恒为本地 Obsidian（DISABLE_FEISHU_SYNC=1）；若需恢复飞书写同步，走 `land_scys_batch.py`。

用法（子 Agent 总结完成后，或人工手动运行）：
    python scripts/land_scys_by_key.py
"""
import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PENDING = os.path.join(ROOT, "notes", "_scraped", "scys", "pending_summaries.json")
TEMP = os.path.join(ROOT, "notes", "_scraped", "scys", "_sum_temp")

TAG_LINE_RE = __import__("re").compile(r"^#[\w一-鿿]+(?:\s+#[\w一-鿿]+)+$")


def extract_tags_and_strip(content: str):
    """抽出井号标签行里的标签，并从正文剥离该行（防止与 param tags 重复）。"""
    lines = content.split("\n")
    tags = []
    out = []
    for ln in lines:
        if TAG_LINE_RE.match(ln.strip()):
            for t in ln.strip().split():
                t = t.lstrip("#").strip()
                if t and t not in tags:
                    tags.append(t)
            continue
        out.append(ln)
    return tags, "\n".join(out)


def _load_env():
    # 必须在 import articles 之前就位（feishu.py 在 __init__ 读环境变量）
    env_path = os.path.join(ROOT, ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


def main():
    _load_env()
    from articles.main import save_summary_only
    from shared.routing import resolve_folder

    queue = json.load(open(PENDING, encoding="utf-8"))
    ok = skip = fail = miss = 0
    for entry in queue:
        key = entry.get("topicId") or entry.get("url", "")
        sumf = os.path.join(TEMP, f"sum_{key}.md")
        if not os.path.exists(sumf):
            miss += 1
            continue
        content = open(sumf, encoding="utf-8").read()
        orig_tags, stripped = extract_tags_and_strip(content)
        project = entry.get("project", "")
        url = entry.get("url", "")
        title = entry.get("original_title") or entry.get("title", "")
        tags = list(dict.fromkeys(orig_tags + ["生财有术", project]))
        folder = resolve_folder({"author": "", "url": url, "scys_domain": project})
        pub = int((entry.get("list_meta") or {}).get("gmtCreate") or 0)
        res = save_summary_only({
            "summarized_content": stripped,
            "original_url": url,
            "author": "",
            "tags": tags,
            "original_title": title,
            "publish_time": pub,
            "folder": folder,
            "note_type": entry.get("note_type", ""),
            "obsidian": True,
        })
        if res.get("success"):
            if res.get("skipped"):
                print(f"[{key}] SKIP(dedup) -> {res.get('filename')}")
            else:
                print(f"[{key}] OK -> {res.get('filename')}")
            q = json.load(open(PENDING, encoding="utf-8"))
            qi = next((j for j, e in enumerate(q) if e.get("topicId") == entry.get("topicId")), None)
            if qi is None:
                qi = next((j for j, e in enumerate(q) if e.get("url") == url), None)
            if qi is not None:
                q.pop(qi)
                json.dump(q, open(PENDING, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            ok += 1
        else:
            print(f"[{key}] FAIL: {res.get('message')}")
            fail += 1
    remain = len(json.load(open(PENDING, encoding="utf-8")))
    print(f"\n完成：落盘 OK {ok}（含 dedup skip）, FAIL {fail}, MISS {miss}，剩余队列 {remain} 条")


if __name__ == "__main__":
    main()
