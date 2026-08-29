"""closeout_one.py — 监控降级队列闭环用的机械落盘助手。

子 Agent 流程：
  1. 读 raw_file 原文 -> 按 note_type 模板写结构化总结 markdown 到 _closeout/<safe>.md
  2. 调用本脚本：把 md 内容交给 articles.main.save_summary_only 落盘
     （飞书走 lark-cli，不可用时回退本地 notes/，dedup 写索引防重复）

用法：
  python closeout_one.py <md_path> <url> <author> <title> <note_type> <folder> <publish_time>
"""
import sys
import os
import json

# 让脚本在任意 cwd 下都能 import 到项目内的 articles.main（相对脚本自身，不依赖绝对路径）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from articles.main import save_summary_only  # noqa: E402


def main():
    if len(sys.argv) < 8:
        print(json.dumps({"error": "usage: closeout_one.py <md> <url> <author> <title> <note_type> <folder> <publish_time>"}, ensure_ascii=False))
        return 1
    md_path, url, author, title, note_type, folder, publish_time = sys.argv[1:8]
    try:
        with open(md_path, encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(json.dumps({"title": title, "success": False, "msg": f"读md失败: {e}"}, ensure_ascii=False))
        return 1

    try:
        pt = int(publish_time) if publish_time else 0
    except ValueError:
        pt = 0

    res = save_summary_only({
        "summarized_content": content,
        "original_url": url,
        "author": author,
        "original_title": title,
        "publish_time": pt,
        "folder": folder,
        "tags": [],
        "obsidian": False,
    })
    print(json.dumps({
        "title": title,
        "note_type": note_type,
        "success": res.get("success"),
        "skipped": res.get("skipped", False),
        "msg": res.get("message"),
        "filename": res.get("filename"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
