"""scripts/backfill_overviews.py — 为所有监控账号的「内容 folder」生成/重建总览。

背景（用户 2026-08-25）：飞书 Wiki 节点无 sort_order，监控补历史后顺序乱；
shared/feishu_overview 已能在每次落盘时把文章插入对应 folder 的总览（📋 总览-<名>），
但历史存量从没回填过，且只系列课有总览。本脚本扫描整棵监控树，对每个「内容 folder」
（其直接子节点全是文章叶子，即 日更 / 各系列容器）调用 feishu_overview.rebuild，
生成带发布日期、按时间倒序、并标记坏标题（⚠️ + 建议标题）的总览。

内容 folder 判定：某节点的子节点没有一个是容器（has_child 全 False）→ 它本身就是
承载文章的内容 folder（日更 / 系列名）。账号层、平台层因含有容器子节点被排除。

用法：
  python scripts/backfill_overviews.py --dry-run            # 只列将处理的 folder
  python scripts/backfill_overviews.py --dry-run --platform B站
  python scripts/backfill_overviews.py --apply              # 全量重建（创建/覆盖总览文档）
  python scripts/backfill_overviews.py --apply --account 哥飞 --limit 1
"""
import os
import sys
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

from articles.feishu import FeishuOutput
from shared import feishu_overview as fo


def find_monitor_root(f: FeishuOutput) -> str:
    """在 wiki_parent_node 下找【监控】容器；找不到则退回 wiki_parent_node。"""
    root = f.wiki_parent_node
    for k in f.list_children(root):
        t = k.get("title", "")
        if "监控" in t:  # 容错【监控】/监控
            return k.get("node_token", "")
    return root


def collect_content_folders(f: FeishuOutput, root_token: str):
    """BFS 收集所有内容 folder 的 folder 路径字符串（如 【监控】/公众号/哥飞/日更）。"""
    out = []
    stack = [(root_token, "【监控】")]
    seen = set()
    while stack:
        tok, path = stack.pop()
        if tok in seen:
            continue
        seen.add(tok)
        kids = f.list_children(tok)
        if not kids:
            continue
        # 含任意容器子节点 → 自身不是内容 folder，继续下钻
        has_container = any(k.get("has_child") for k in kids)
        if not has_container:
            # 直接子节点全是文章叶子 → 自身是内容 folder
            if path.count("/") >= 2:  # 至少 平台/账号/内容层，避免误收顶层
                out.append(path)
            continue
        for k in kids:
            kt = k.get("node_token", "")
            title = k.get("title", "")
            stack.append((kt, f"{path}/{title}"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只列将处理的 folder，不改动飞书")
    ap.add_argument("--apply", action="store_true", help="实际重建总览（默认 dry-run）")
    ap.add_argument("--platform", default="", help="只处理路径含该片段的平台（如 B站 / 公众号 / 生财有术）")
    ap.add_argument("--account", default="", help="只处理路径含该账号名的 folder")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 个 folder（测试用，0=全部）")
    args = ap.parse_args()

    if not args.dry_run and not args.apply:
        args.dry_run = True  # 默认安全：dry-run

    f = FeishuOutput()
    if not f.is_available():
        print("❌ 飞书不可用（FEISHU_WIKI_SPACE / lark-cli 未配置）")
        return

    root = find_monitor_root(f)
    print(f"📂 监控根: {root}")
    folders = collect_content_folders(f, root)
    # 过滤（先全量枚举，再对结果过滤，避免下钻时误砍中间层）
    if args.platform:
        folders = [x for x in folders if args.platform in x]
    if args.account:
        folders = [x for x in folders if args.account in x]
    print(f"📁 内容 folder 共 {len(folders)} 个（过滤后）")

    if args.limit:
        folders = folders[:args.limit]

    if args.dry_run:
        print("\n--- dry-run：将处理以下 folder ---")
        for fol in folders:
            print(f"  · {fol}")
        print(f"\n（dry-run 未改动飞书。加 --apply 才重建总览）")
        return

    # apply
    total = 0
    ok = 0
    for fol in folders:
        try:
            n = fo.rebuild(fol)
            ok += 1
            total += n
            print(f"  ✅ {fol} → 总览 {n} 条")
        except Exception as e:
            print(f"  ❌ {fol} 失败: {e}")
    print(f"\n完成：处理 {ok}/{len(folders)} 个 folder，共写入 {total} 条总览条目")


if __name__ == "__main__":
    main()
