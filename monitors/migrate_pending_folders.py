"""monitors/migrate_pending_folders.py — 一次性把旧格式 pending 重路由到新结构。

用户 2026-08-23 决策「节点move + 凭pending重路由」：监控产出的 353 条旧 pending
（monitors/pending_summaries.json 的「分类/账号」格式 + scys 的未带 folder 格式）
只是本地 JSON，不写飞书。本脚本用统一路由器 resolve_folder 重写其 folder 字段，
使其与新建的【监控】/【我的笔记】结构一致，后续 drain 直接落正确节点。

零飞书 IO；可重复运行（幂等）。
"""
import json
import os
import sys
from collections import Counter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from shared.routing import resolve_folder

PENDING_SUMMARY_PATH = os.path.join(BASE_DIR, "monitors", "pending_summaries.json")
SCYS_PENDING_PATH = os.path.join(BASE_DIR, "notes", "_scraped", "scys", "pending_summaries.json")


def _rewrite_monitor_pending() -> int:
    if not os.path.exists(PENDING_SUMMARY_PATH):
        return 0
    data = json.load(open(PENDING_SUMMARY_PATH, encoding="utf-8"))
    if not data:
        return 0
    before = Counter(x.get("folder", "?") for x in data)
    for e in data:
        new = resolve_folder({
            "author": e.get("author") or e.get("mp_name") or e.get("sub_name"),
            "mp_name": e.get("mp_name"), "sub_name": e.get("sub_name"),
            "source": e.get("source"), "route": e.get("route"),
            "url": e.get("url"), "category": e.get("category"),
        })
        e["folder"] = new
    after = Counter(x.get("folder", "?") for x in data)
    json.dump(data, open(PENDING_SUMMARY_PATH, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"[monitor pending] {len(data)} 条")
    print("  前:", dict(before))
    print("  后:", dict(after))
    return len(data)


def _rewrite_scys_pending() -> int:
    if not os.path.exists(SCYS_PENDING_PATH):
        return 0
    data = json.load(open(SCYS_PENDING_PATH, encoding="utf-8"))
    if not data:
        return 0
    before = Counter(x.get("folder", "<无>") for x in data)
    for e in data:
        e["author"] = "生财有术"
        e["source"] = "monitor_scys"
        e["folder"] = resolve_folder({
            "url": e.get("url"), "scys_domain": e.get("project") or e.get("domain"),
        })
    after = Counter(x.get("folder", "?") for x in data)
    json.dump(data, open(SCYS_PENDING_PATH, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"[scys pending] {len(data)} 条")
    print("  前:", dict(before))
    print("  后:", dict(after))
    return len(data)


if __name__ == "__main__":
    n1 = _rewrite_monitor_pending()
    n2 = _rewrite_scys_pending()
    print(f"\n✅ 重路由完成：monitor {n1} + scys {n2} = {n1 + n2} 条（仅改本地 JSON，未写飞书）")
