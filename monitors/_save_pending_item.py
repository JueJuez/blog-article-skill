"""单条待总结落盘助手（原子化：仅 save_summary_only 成功才从队列移除该条）。

用法（由子 Agent 调用）：
    python monitors/_save_pending_item.py <raw_file_path> <summary_md_path>

- 按 raw_file 在 pending_summaries.json 中定位条目（不受索引漂移影响）
- 调 articles.main.save_summary_only 双写（当前仅 Obsidian，飞书由 DISABLE_FEISHU_SYNC 跳过）
- 成功则移除该条并回写队列；失败则保留条目并输出 FAIL 原因
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from articles.main import save_summary_only

PENDING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pending_summaries.json")


def main():
    if len(sys.argv) < 3:
        print("usage: _save_pending_item.py <raw_file_path> <summary_md_path>")
        sys.exit(2)

    raw_path = sys.argv[1]
    summary_file = sys.argv[2]

    try:
        with open(summary_file, encoding="utf-8") as f:
            summarized = f.read()
    except Exception as e:
        print(f"ERR 读取总结文件失败: {e}")
        sys.exit(4)

    if not summarized.strip():
        print("ERR 总结内容为空")
        sys.exit(5)

    try:
        queue = json.load(open(PENDING, encoding="utf-8"))
    except Exception as e:
        print(f"ERR 读取队列失败: {e}")
        sys.exit(6)

    idx = next((i for i, x in enumerate(queue) if os.path.normpath(x.get("raw_file") or "") == os.path.normpath(raw_path)), None)
    if idx is None:
        print(f"ERR 队列中未找到 raw_file={raw_path}")
        sys.exit(3)

    item = queue[idx]
    res = save_summary_only({
        "summarized_content": summarized,
        "original_url": item.get("url", ""),
        "author": item.get("author", ""),
        "tags": item.get("tags", []),
        "original_title": item.get("title", ""),
        "publish_time": item.get("publish_time", 0),
        "folder": item.get("folder", ""),
    })

    if res.get("success"):
        queue.pop(idx)
        json.dump(queue, open(PENDING, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"OK saved: {res.get('filename')} | 剩余队列: {len(queue)}")
    else:
        print(f"FAIL: {res.get('message')} | 条目保留在队列")


if __name__ == "__main__":
    main()
