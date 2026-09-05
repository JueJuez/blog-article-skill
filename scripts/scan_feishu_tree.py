#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/scan_feishu_tree.py — 递归枚举飞书「AI 总结笔记」树下所有文章(docx 叶子)节点。

输出 JSON 到 stdout：[{path:[标题...], token, title, obj_type, has_child}]。
只读取，不改任何东西。用于评估「飞书→Obsidian」迁移的scope与去重。

用法：
  python scripts/scan_feishu_tree.py                 # 打印树概览 + 统计
  python scripts/scan_feishu_tree.py --json out.json # 导出完整叶子清单
"""
import subprocess
import json
import sys
import argparse

SPACE = "7636965310725115074"
ROOT = "FX33wKHwZiMzJqk7BQQctHD3nKh"  # AI 总结笔记


def cli(args):
    cmd = ["lark-cli"] + args
    out = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    if out.returncode != 0:
        print(f"[lark-cli err] {args}\n{out.stderr[:500]}", file=sys.stderr)
        return None
    try:
        return json.loads(out.stdout)
    except Exception:
        return None


def node_list(parent):
    d = cli(["wiki", "+node-list", "--space-id", SPACE,
             "--parent-node-token", parent, "--as", "user", "--json"])
    if d and d.get("ok"):
        return d.get("data", {}).get("nodes", [])
    return []


def walk(parent, path_prefix, acc, stats):
    for n in node_list(parent):
        title = n.get("title", "")
        tok = n.get("node_token")
        has_child = n.get("has_child")
        obj_type = n.get("obj_type")
        cur = path_prefix + [title]
        stats["nodes"] += 1
        if has_child:
            stats["folders"] += 1
            walk(tok, cur, acc, stats)
        else:
            stats["leaves"] += 1
            if obj_type == "docx":
                stats["docx"] += 1
                acc.append({"path": cur, "token": tok, "title": title,
                            "obj_type": obj_type, "has_child": has_child})
            else:
                # 非 docx 叶子（如 bitable/shortcut），记录但不算文章
                acc.append({"path": cur, "token": tok, "title": title,
                            "obj_type": obj_type, "has_child": has_child, "skip": "non-docx"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="导出完整叶子清单到该路径")
    args = ap.parse_args()

    acc = []
    stats = {"nodes": 0, "folders": 0, "leaves": 0, "docx": 0}
    # 根本身不算
    for n in node_list(ROOT):
        title = n.get("title", "")
        tok = n.get("node_token")
        has_child = n.get("has_child")
        obj_type = n.get("obj_type")
        stats["nodes"] += 1
        if has_child:
            stats["folders"] += 1
            walk(tok, [title], acc, stats)
        else:
            stats["leaves"] += 1
            if obj_type == "docx":
                stats["docx"] += 1
                acc.append({"path": [title], "token": tok, "title": title,
                            "obj_type": obj_type, "has_child": has_child})
            else:
                acc.append({"path": [title], "token": tok, "title": title,
                            "obj_type": obj_type, "has_child": has_child, "skip": "non-docx"})

    print(f"=== 飞书树扫描统计（根=AI 总结笔记）===")
    print(f"  节点总数: {stats['nodes']}  文件夹: {stats['folders']}  "
          f"叶子: {stats['leaves']}  其中 docx 文章: {stats['docx']}")
    print(f"\n=== 文章清单（共 {len([a for a in acc if a.get('obj_type')=='docx'])} 篇 docx）===")
    for a in acc:
        if a.get("obj_type") == "docx":
            print("  /".join(a["path"]))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(acc, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 清单已导出: {args.json}")


if __name__ == "__main__":
    main()
