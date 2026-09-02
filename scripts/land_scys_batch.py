"""scys 批量落地：把子 Agent 产出的 sum_<index>.md 落到飞书（生财有术/<领域>）。

- 单线程执行，避免并发写同一队列 JSON（子 Agent 只写 temp 文件，不碰队列）。
- 按 dispatch_map 的 raw_file 定位队列条目；落盘成功才出队。
- 可重跑：save_summary_only 内置 URL 机械去重闸门，重复跑自动 skip。

用法（在 scys 子 Agent 总结完成后运行）：
    python scripts/land_scys_batch.py
"""
import sys
import os
import json
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PENDING = os.path.join(ROOT, "notes", "_scraped", "scys", "pending_summaries.json")
MAP = os.path.join(ROOT, "scripts", "_dispatch_map.json")

# 井号标签行：形如 `#AI #自媒体 #涨粉`（至少两个 #token，无空格紧跟 # 后）
TAG_LINE_RE = re.compile(r"^#[\w一-鿿]+(?:\s+#[\w一-鿿]+)+$")


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


def main():
    from articles.main import save_summary_only

    dm = json.load(open(MAP, encoding="utf-8"))
    temp = dm["temp"]
    items = dm["items"]
    queue = json.load(open(PENDING, encoding="utf-8"))

    ok = skip = fail = miss = 0
    for it in items:
        idx = it["index"]
        raw = it["raw_file"]
        project = it["project"]
        title = it["title"]
        url = it.get("url", "")
        sumf = os.path.join(temp, f"sum_{idx}.md")
        if not os.path.exists(sumf):
            print(f"[{idx}] MISSING {sumf} -> skip")
            miss += 1
            continue
        content = open(sumf, encoding="utf-8").read()
        orig_tags, stripped = extract_tags_and_strip(content)
        # 合并：原文标签 + 领域标签（去重保序）
        tags = list(dict.fromkeys(orig_tags + ["生财有术", project]))
        folder = f"生财有术/{project}"

        # 按 raw_file 定位队列条目（队列字段名为 output；normpath 抗分隔符差异）
        qi = next((i for i, x in enumerate(queue)
                   if os.path.normpath(x.get("output") or x.get("raw_file") or "") == os.path.normpath(raw)), None)
        if qi is None:
            print(f"[{idx}] 队列未找到 raw_file={raw} -> skip")
            miss += 1
            continue

        res = save_summary_only({
            "summarized_content": stripped,
            "original_url": url,
            "author": "",
            "tags": tags,
            "original_title": title,
            "publish_time": 0,
            "folder": folder,
            "obsidian": False,
        })
        if res.get("success"):
            if res.get("skipped"):
                print(f"[{idx}] SKIP(dedup) -> {res.get('filename')}")
            else:
                print(f"[{idx}] OK -> {res.get('filename')}")
            # 落盘成功才出队（重新读最新队列，按 raw_file 定位防止索引漂移）
            queue = json.load(open(PENDING, encoding="utf-8"))
            qi2 = next((i for i, x in enumerate(queue)
                        if os.path.normpath(x.get("output") or x.get("raw_file") or "") == os.path.normpath(raw)), None)
            if qi2 is not None:
                queue.pop(qi2)
                json.dump(queue, open(PENDING, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=2)
            ok += 1
        else:
            print(f"[{idx}] FAIL: {res.get('message')}")
            fail += 1

    print(f"\n完成：OK {ok}（含 dedup skip）, FAIL {fail}, MISS {miss}，剩余队列 {len(queue)} 条")


if __name__ == "__main__":
    main()
