"""去重飞书系列容器下的重复子节点，并同步 manifest。

用法：python scripts/dedup_feishu_series.py "价值投资，知行合一"
"""
import os
import sys
import re
import json
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

from articles.feishu import FeishuOutput
from shared.routing import resolve_folder
from shared import series_manifest as sm
from shared import series_naming as sn
from articles import main as articles_main


def dedup_series(series_title: str, author: str = None, dry_run: bool = False):
    f = FeishuOutput()
    if not f.is_available():
        print("❌ 飞书不可用"); return 1

    # 加载 manifest
    m = sm.SeriesManifest(series_title, notes_dir=articles_main.NOTES_DIR).load()
    author = author or m.author or "土斯土耶夫斯基"

    full_folder = resolve_folder({"author": author, "series": series_title,
                                  "source": "monitor_series", "platform": "bilibili"})
    folder = full_folder.rsplit("/", 1)[0] if "/" in full_folder else full_folder
    up_tok = f.ensure_folder_path([d for d in folder.split("/") if d])
    container_tok = f.ensure_series_node(series_title, parent_token=up_tok)
    if not container_tok:
        print("❌ 无法获取系列容器"); return 1

    children = f.list_children(container_tok)
    print(f"容器「{series_title}」共有 {len(children)} 个子节点")

    by_title = {}
    for n in children:
        t = n.get("title", "")
        by_title.setdefault(t, []).append(n)

    dup = {t: nodes for t, nodes in by_title.items() if len(nodes) > 1}
    if not dup:
        print("未发现重复节点"); return 0

    print(f"发现 {len(dup)} 个重复标题，共 {sum(len(v) for v in dup.values())} 个节点")

    # 决定去留：优先保留 manifest 中记录的 node_token；否则保留第一个
    kept = {}  # title -> kept node
    deleted = 0
    for title, nodes in dup.items():
        # 匹配 page
        mm = re.match(r"^第(\d{2})集_", title)
        page = int(mm.group(1)) if mm else None
        want_node = ""
        if page is not None:
            ep = m.get(page)
            want_node = (ep or {}).get("node", "")
        # 选择保留
        keep = None
        if want_node:
            for n in nodes:
                if n.get("node_token") == want_node:
                    keep = n; break
        if keep is None:
            keep = nodes[0]
        kept[title] = keep
        for n in nodes:
            if n.get("node_token") != keep.get("node_token"):
                print(f"  🗑️ 删除重复：{title} -> {n.get('node_token')}")
                if not dry_run:
                    if f.delete_node(n.get("node_token")):
                        deleted += 1
                    else:
                        print(f"  ⚠️ 删除失败：{n.get('node_token')}")
                else:
                    deleted += 1
    print(f"计划/已删除 {deleted} 个重复节点")

    # 重新 reconcile，更新 manifest node token
    if not dry_run:
        m.reconcile_feishu(parent_token=up_tok)
        m.save()
        print(m.summary_line())
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("series_title")
    ap.add_argument("--author", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.exit(dedup_series(args.series_title, args.author, args.dry_run))
