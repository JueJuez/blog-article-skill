"""scripts/_save_migrate_entry.py — 迁移队列单条落盘助手（子 Agent 调用）。

读取 chunk 文件中第 index 条的元数据 + 已生成的 summary 文件，
调用 articles.main.save_summary_only 落盘到本地 Obsidian（不写飞书）。
只负责机械落盘，不做总结（总结由子 Agent 这个 LLM 完成）。

用法（子 Agent 在 chunk 目录内逐条调用）：
    python scripts/_save_migrate_entry.py --chunk <chunk.json> --index <N> --summary <summary.md>

stdout 输出 JSON：{"success":bool,"skipped":bool,"filename":str,"message":str,"issues":[...]}
"""
import os
import sys
import json
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 迁移队列默认只写本地 Obsidian，不写飞书（与项目规则 2026-09-04 一致）
os.environ.setdefault("OBSIDIAN_WRITE", "1")
os.environ["DISABLE_FEISHU_SYNC"] = "1"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", required=True, help="chunk 文件路径（含全部条目）")
    ap.add_argument("--index", type=int, required=True, help="条目在 chunk 中的下标")
    ap.add_argument("--summary", required=True, help="已生成的总结 markdown 文件路径")
    args = ap.parse_args()

    try:
        chunk = json.load(open(args.chunk, encoding="utf-8"))
    except Exception as e:
        print(json.dumps({"success": False, "message": f"chunk 读取失败: {e}"}))
        return

    if not isinstance(chunk, list) or args.index >= len(chunk):
        print(json.dumps({"success": False, "message": f"index {args.index} 越界（chunk 长度 {len(chunk)}）"}))
        return

    e = chunk[args.index]
    try:
        summary = open(args.summary, encoding="utf-8").read()
    except Exception as ex:
        print(json.dumps({"success": False, "message": f"summary 读取失败: {ex}"}))
        return

    from articles.main import save_summary_only
    res = save_summary_only({
        "summarized_content": summary,
        "original_url": e.get("url", ""),
        "author": e.get("author", "") or "",
        "tags": e.get("tags") or [],
        "original_title": e.get("title", "") or "",
        "publish_time": e.get("publish_time", 0) or 0,
        "folder": e.get("folder", "") or "",
        "note_type": e.get("note_type", "") or "",
        "obsidian": True,
        "force": False,
    })
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
