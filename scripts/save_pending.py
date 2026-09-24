"""scripts/save_pending.py — 待总结队列统一落盘 runner（子 Agent 用）。

用法:
    python scripts/save_pending.py --queue monitors --key <url>        --file <总结稿.md> [--force]
    python scripts/save_pending.py --queue scys     --key <topicId>     --file <总结稿.md> [--force]

为何存在：子 Agent 直接 import articles 常踩两个坑 —— ① .env 必须在 import 前置位
（feishu.py 在 __init__ 读环境变量）；② `python scripts/x.py` 时 sys.path[0] 是 scripts/。
本脚本统一处理 env + sys.path + 队列元数据回填，子 Agent 只需写好总结稿再跑一行。

队列字段差异在这里抹平：
- monitors：键 = url，原文路径字段 = raw_file，落盘目录 = 条目 folder。
- scys：键 = topicId，原文路径字段 = output，落盘目录 = 生财有术/<project>。

成功落盘后**不直接改队列**（多子 Agent 并发 read-modify-write 会互相覆盖），
由主 Agent 收工后跑 `python scripts/filter_pending.py` 按 dedup 索引统一出队。
"""
import argparse
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

QUEUE_PATHS = {
    "monitors": os.path.join(BASE_DIR, "monitors", "pending_summaries.json"),
    "scys": os.path.join(BASE_DIR, "notes", "_scraped", "scys", "pending_summaries.json"),
}
KEY_FIELDS = {"monitors": "url", "scys": "topicId"}


def _load_entry(queue: str, key: str):
    path = QUEUE_PATHS[queue]
    if not os.path.exists(path):
        raise SystemExit(f"[ERR] 队列文件不存在: {path}")
    items = json.load(open(path, "r", encoding="utf-8"))
    field = KEY_FIELDS[queue]
    entry = next((e for e in items if str(e.get(field, "")) == str(key)), None)
    if entry is None:
        raise SystemExit(f"[ERR] 队列中找不到 {field}={key}（可能已被消费出队）")
    return entry


def build_payload(queue: str, entry: dict, content: str, force: bool) -> dict:
    if queue == "monitors":
        return {
            "summarized_content": content,
            "original_url": entry.get("url", ""),
            "author": entry.get("author", ""),
            "tags": entry.get("tags", []) or [],
            "original_title": entry.get("title", ""),
            "publish_time": entry.get("publish_time", 0),
            "folder": entry.get("folder", ""),
            "raw_file": entry.get("raw_file", ""),
            "note_type": entry.get("note_type", ""),
            "obsidian": True,
            "force": force,
        }
    project = entry.get("project", "") or "未分类"
    return {
        "summarized_content": content,
        # scys 无 author 概念：用 project 兜底，路由以 folder 为准
        "original_url": entry.get("url", ""),
        "author": "生财有术",
        "tags": [],
        "original_title": entry.get("title", ""),
        "publish_time": 0,
        "folder": f"生财有术/{project}",
        "raw_file": entry.get("output", ""),
        "note_type": entry.get("note_type", ""),
        "obsidian": True,
        "force": force,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", required=True, choices=sorted(QUEUE_PATHS))
    ap.add_argument("--key", required=True, help="monitors 传 url；scys 传 topicId")
    ap.add_argument("--file", required=True, help="子 Agent 写好的总结稿 md 路径")
    ap.add_argument("--force", action="store_true", help="绕过 dedup 去重闸门（仅重试仍失败时用）")
    args = ap.parse_args()

    from shared.env import load_env
    load_env()

    if not os.path.exists(args.file):
        print(f"[ERR] 总结稿不存在: {args.file}")
        return 4
    entry = _load_entry(args.queue, args.key)
    content = open(args.file, "r", encoding="utf-8").read()

    from articles.main import save_summary_only
    res = save_summary_only(build_payload(args.queue, entry, content, args.force))
    print("RESULT: " + json.dumps(res, ensure_ascii=False, default=str))
    if res.get("success"):
        return 0
    # 内容判据失败带 retry 标记 → 子 Agent 按 issues 重写后重交（≤2 次），再失败才 --force
    msg = str(res.get("message", ""))
    if msg.startswith("VERIFIER") and not args.force:
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
