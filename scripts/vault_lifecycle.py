#!/usr/bin/env python3
"""scripts/vault_lifecycle.py — vault 生命周期工具（2026-09-06，本地保留机制的对账半边）。

架构决策（用户 2026-09-06 拍板由 AI 定）：
- **记录 = 真源**：dedup 索引（.cache/dedup.json）+ series_state + manifest 记录
  「是否已总结」，**永不清理**——本地/vault 文件删了它也知道哪些总结过。
- **vault 文件 = 上传载荷**：Obsidian vault 本身就是本地 markdown（即"本地保留"），
  audit_sync.py 负责 vault→飞书镜像（本地→云单向）。
- **本工具补两个缺口**：
  ① reconcile：记录命中但 vault 文件丢失 → 清记录，下次补齐自动重抓重总结
     （用户语义："有就直接上传，没有就重新总结再上传"）。
  ② old-report：vault 内超过 N 天的笔记文件清单（**只报告不删除**，归档/清理由用户人工裁决）。

注意：dedup 索引只存 URL/内容 hash + title + filename，没有 folder。reconcile 按
filename 在 vault 内递归找同名文件；找不到 = 文件丢失。只处理 URL 为指定域名的条目
（默认 bilibili.com/video，避免误清公众号/scys 等非 vault 管理的记录——那些记录的
filename 本来就不在 vault 里）。

用法：
  python scripts/vault_lifecycle.py reconcile --dry            # 预览丢失文件的记录
  python scripts/vault_lifecycle.py reconcile --apply          # 清除丢失记录
  python scripts/vault_lifecycle.py old-report --days 90       # 90 天前文件清单（只读）
"""
import argparse
import json
import os
import sys
import time
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))
VAULT = os.environ.get("OBSIDIAN_VAULT_PATH", "")


def _vault_files_index() -> dict:
    """vault 内 filename -> 完整路径 索引（一次扫描）。"""
    idx = {}
    for dirpath, _dirs, files in os.walk(VAULT):
        for fn in files:
            if fn.endswith(".md") and fn not in idx:
                idx[fn] = os.path.join(dirpath, fn)
    return idx


def cmd_reconcile(apply: bool, scope_substr: str = "") -> None:
    from articles import dedup
    if not VAULT or not os.path.isdir(VAULT):
        print(f"❌ OBSIDIAN_VAULT_PATH 未配置或不存在：{VAULT}")
        sys.exit(1)
    if apply and not scope_substr:
        print("❌ --apply 必须搭配 --scope-substr <关键词>（UP名/公众号名等）。\n"
              "   原因：dedup 索引里混着飞书时代记录（其 filename 本来就不在 vault），\n"
              "   无范围的全量清除会误清它们导致整批重总结。先 dry 看清单，再按范围清。")
        sys.exit(1)
    index = dedup._load_index()
    vfiles = _vault_files_index()
    lost = []
    for h, rec in index.items():
        title, filename = rec.get("title", ""), rec.get("filename", "")
        if not filename or not filename.endswith(".md"):
            continue  # 非 markdown 记录不动，避免误清
        if scope_substr and scope_substr not in title and scope_substr not in filename:
            continue
        # 索引里 filename 可能带 vault 相对路径（folder/xxx.md），按 basename 对账
        base = os.path.basename(filename)
        if base in vfiles:
            continue
        lost.append((h, title, base))
    print(f"[reconcile] 索引 {len(index)} 条；vault 文件 {len(vfiles)} 个；"
          f"范围「{scope_substr or '全部(仅报告)'}」内记录在但 vault 文件丢失 {len(lost)} 条：")
    for h, title, base in lost[:30]:
        print(f"    - {title[:40]}  ({base})")
    if len(lost) > 30:
        print(f"    …共 {len(lost)} 条")
    if not apply:
        print("\n[dry] 未做改动。确认范围后加 --scope-substr <关键词> --apply 清除"
              "（下次补齐将自动重抓重总结）。")
        return
    for h, _t, _f in lost:
        index.pop(h, None)
    dedup._save_index(index)
    print(f"✅ 已清除 {len(lost)} 条丢失记录，dedup 索引剩 {len(index)} 条。")


def cmd_old_report(days: int) -> None:
    if not VAULT or not os.path.isdir(VAULT):
        print(f"❌ OBSIDIAN_VAULT_PATH 未配置或不存在：{VAULT}")
        sys.exit(1)
    cutoff = time.time() - days * 86400
    old = []
    for dirpath, _dirs, files in os.walk(VAULT):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            p = os.path.join(dirpath, fn)
            mtime = os.path.getmtime(p)
            if mtime < cutoff:
                old.append((mtime, p))
    old.sort()
    print(f"[old-report] vault 内超过 {days} 天未改动的笔记 {len(old)} 个"
          f"（只报告，不删除；清理请人工裁决后自行操作）：")
    for mtime, p in old[:50]:
        print(f"    - {time.strftime('%Y-%m-%d', time.localtime(mtime))}  {os.path.relpath(p, VAULT)}")
    if len(old) > 50:
        print(f"    …共 {len(old)} 个")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("reconcile", help="记录 vs vault 文件对账")
    p1.add_argument("--apply", action="store_true")
    p1.add_argument("--scope-substr", dest="scope_substr", default="",
                    help="只对账标题/文件名含此关键词的记录（--apply 必带）")
    p2 = sub.add_parser("old-report", help="超期文件清单（只读）")
    p2.add_argument("--days", type=int, default=90)
    args = ap.parse_args()
    if args.cmd == "reconcile":
        cmd_reconcile(args.apply, args.scope_substr)
    else:
        cmd_old_report(args.days)


if __name__ == "__main__":
    main()
