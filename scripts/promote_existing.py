"""scripts/promote_existing.py — 把飞书里已有的「账号根节点」内容重归档到 【日更】/系列课。

背景（用户 2026-08-25 决策）：
- 引入【日更】子节点前，非系列内容直接堆在 【监控】/<平台>/<账号>/ 根下；
- 引入 series_patterns 后，新内容会按标题自动落到 【日更】 或 <系列> 子节点；
- 但存量内容仍在账号根下，结构不一致。本脚本一次性把账号根下的叶子文档
  按标题关键词重归档：
    - 命中账号 series_patterns → 移动到 【监控】/<平台>/<账号>/<系列>/
    - 其余 → 移动到 【监控】/<平台>/<账号>/【日更】/
  （【日更】/已有系列子文件夹本身是容器，跳过其内部内容，不递归）
- 移动后重建各 folder 的总览索引，使排序文档与飞书实际结构一致。

用法：
  python scripts/promote_existing.py --account 哥飞
  python scripts/promote_existing.py --account Mark__Huang
  python scripts/promote_existing.py --all          # 遍历 subscriptions 全部监控账号
  python scripts/promote_existing.py --all --dry-run   # 只打印会怎么动，不真移动

幂等：已在新位置（【日更】/系列）的内容不会被二次移动；重跑安全。
"""
import os
import sys
import json
import time
import argparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, ".env"))

from shared.routing import (load_account_registry, load_series_patterns,
                            match_series, MONITOR_ROOT, DAILY, _PLATFORM_FOLDER)
from shared import feishu_overview as fo


def _find_child(f, parent_token: str, title: str):
    for n in f.list_children(parent_token):
        if n.get("title") == title:
            return n
    return None


def _resolve_node(f, path_parts: list):
    """从 wiki 根 walker 找到 path_parts 对应节点 token；任一段缺失返回 None。"""
    parent = f.wiki_parent_node
    if not parent:
        return None
    tok = None
    for seg in path_parts:
        node = _find_child(f, parent, seg)
        if not node:
            return None
        tok = node.get("node_token")
        parent = tok
    return tok


def _ensure_subfolder(f, path_parts: list) -> str:
    return f.ensure_folder_path(path_parts)


def _fetch_body(f, obj_token: str) -> str:
    """读文档正文（markdown），用于 body 匹配。失败返回 ''。"""
    if not obj_token:
        return ""
    try:
        res = f._run_cli_command([
            "docs", "+fetch", "--doc", obj_token, "--doc-format", "markdown",
            "--as", "user", "--json",
        ], timeout=30)
        return ((((res or {}).get("data", {}) or {}).get("document", {}) or {}).get("content", "") or "")
    except Exception:
        return ""


def _migrate_account(f, account: str, dry_run: bool, use_body: bool = False) -> dict:
    reg = load_account_registry()
    info = reg.get(account)
    if not info:
        print(f"[skip] {account} 不在订阅名单（非监控账号），跳过")
        return {"moved": 0, "skipped": 0}
    platform = info.get("platform", "")
    pf = _PLATFORM_FOLDER.get(platform, platform)
    acct_path = [MONITOR_ROOT, pf, account]
    acct_tok = _resolve_node(f, acct_path)
    if not acct_tok:
        print(f"[skip] 飞书里找不到节点 {'/'.join(acct_path)}，跳过（可能尚无内容）")
        return {"moved": 0, "skipped": 0}

    pats = load_series_patterns().get(account, [])
    # 扫描范围：账号根 + 已有的【日更】子文件夹（存量内容可能堆在任一处）。
    # 已有的系列子文件夹视为目标，不再扫描其内部。
    scan_roots = [(acct_tok, acct_path)]
    _dt = _find_child(f, acct_tok, DAILY)
    daily_tok = _dt.get("node_token") if _dt else None
    if daily_tok:
        scan_roots.append((daily_tok, acct_path + [DAILY]))

    moved = 0
    skipped = 0
    for parent_tok, parent_path in scan_roots:
        for child in f.list_children(parent_tok):
            t = child.get("title", "")
            nt = child.get("node_token", "")
            if not nt:
                continue
            if t.startswith(fo.OVERVIEW_PREFIX):
                continue  # 总览文档自身
            # 容器节点（系列子文件夹等）有子节点 → 跳过
            if f.list_children(nt):
                continue
            # 叶子文档：决定归属（默认看标题；--body 时追加正文匹配）
            body = ""
            if use_body:
                body = _fetch_body(f, child.get("obj_token", ""))
                time.sleep(0.8)  # 逐篇读正文，节流避免飞书频限导致正文抓空
            series = match_series(account, t, body)
            if series:
                target_path = acct_path + [series]
                dest = f"系列《{series}》"
            else:
                target_path = acct_path + [DAILY]
                dest = DAILY
            # 已在正确位置（如 日更 里的非系列文档）→ 跳过
            if parent_path[-1] == target_path[-1]:
                continue
            if dry_run:
                print(f"  [dry-run] 《{t}》 → {dest}")
                moved += 1
                continue
            target_tok = _ensure_subfolder(f, target_path)
            if not target_tok:
                print(f"  ⚠️ 无法确保目标节点 {'/'.join(target_path)}，跳过《{t}》")
                skipped += 1
                continue
            ok = f.move_node(nt, target_tok)
            if ok:
                print(f"  ✓ 移动《{t}》 → {dest}")
                moved += 1
            else:
                print(f"  ⚠️ 移动失败《{t}》")
                skipped += 1

    # 移动后重建总览（账号根 + 日更 + 各系列）
    if not dry_run and (moved > 0):
        for sub in ([DAILY] + [p.get("series") for p in pats if p.get("series")]):
            if not sub:
                continue
            full = "/".join(acct_path + [sub])
            try:
                n = fo.rebuild(full)
                print(f"  🔄 重建总览 {full}：{n} 篇")
            except Exception as e:
                print(f"  ⚠️ 重建总览 {full} 失败：{e}")
        # 账号根总览清空（仅剩子文件夹 + 总览自身）
        try:
            fo.rebuild("/".join(acct_path))
        except Exception:
            pass
    return {"moved": moved, "skipped": skipped}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", help="规范账号名（如 哥飞 / Mark__Huang）")
    ap.add_argument("--all", action="store_true", help="遍历 subscriptions 全部监控账号")
    ap.add_argument("--body", action="store_true",
                    help="除标题外，追加正文匹配（存量泛化标题内容可用；较慢，会逐篇读正文）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不真移动")
    args = ap.parse_args()

    from articles.feishu import FeishuOutput
    f = FeishuOutput()
    if not f.is_available():
        print("✗ 飞书不可用（FEISHU_WIKI_SPACE 未配或 lark-cli 缺失）")
        return

    reg = load_account_registry()
    if args.all:
        accounts = list(reg.keys())
    elif args.account:
        accounts = [args.account]
    else:
        print("请指定 --account <名> 或 --all")
        return

    total = {"moved": 0, "skipped": 0}
    for acct in accounts:
        print(f"\n=== 账号：{acct} ===")
        r = _migrate_account(f, acct, args.dry_run, use_body=args.body)
        total["moved"] += r["moved"]
        total["skipped"] += r["skipped"]
    print(f"\n完成。移动 {total['moved']} 篇，跳过/失败 {total['skipped']} 篇。")


if __name__ == "__main__":
    main()
