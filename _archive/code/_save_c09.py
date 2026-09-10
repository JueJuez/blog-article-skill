"""scripts/_save_c09.py — chunk_09 落盘助手（force 覆盖版）。

与 _save_migrate_entry.py 行为一致，但强制 force=True：
- 跳过 dedup 闸门，允许用正确内容覆盖已被并发写坏的登记表 / Obsidian 笔记；
- 仍走机械质量门禁（verify_note_mechanical），红线不放宽。
- 从调用方传入的 summary 文件读取内容（建议传 chunk 唯一的 c09_<ii>.summary.md，
  以规避多 chunk 并发共享 NN.summary.md 文件名空间被互相覆盖的问题）。

用法：
    python scripts/_save_c09.py --chunk <chunk.json> --index <N> --summary <summary.md>
stdout：{"success":bool,"skipped":bool,"filename":str,"message":str,"issues":[...]}
"""
import os
import sys
import json
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("OBSIDIAN_WRITE", "1")
os.environ["DISABLE_FEISHU_SYNC"] = "1"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", required=True)
    ap.add_argument("--index", type=int, required=True)
    ap.add_argument("--summary", required=True)
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
        "force": True,
    })
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
