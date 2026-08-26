"""scripts/delete_duplicates.py — 按 delete_set.json 删除冗余飞书节点。

前置：先跑 scripts/find_duplicates.py，生成 notes/_audit/delete_set.json（待删 node_token 清单）
和 notes/_audit/duplicates.md（分组报告，含推荐保留节点）。

删除是破坏性、不可逆操作。本脚本默认 --dry-run（只打印不删）；
确认无误后加 --apply 才真正删除。可 --limit N 分批。

用法：
  python scripts/delete_duplicates.py                  # dry-run 预览
  python scripts/delete_duplicates.py --limit 5        # dry-run 前 5 个
  python scripts/delete_duplicates.py --apply          # 真正删除全部
  python scripts/delete_duplicates.py --apply --limit 20  # 先删前 20 个试水
"""
import os
import sys
import json
import argparse
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

DELETE_SET = os.path.join(ROOT, "notes", "_audit", "delete_set.json")
META_PATH = os.path.join(ROOT, "notes", "_audit", "node_meta.json")


def _find_lark_cli():
    import shutil
    cand = shutil.which("lark-cli")
    if cand:
        return cand
    base = r"C:\Users\O1830\.workbuddy\binaries\node\cli-connector-packages"
    p = os.path.join(base, "lark-cli")
    return p if os.path.exists(p) else "lark-cli"


LARK_CLI = _find_lark_cli()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正删除（默认 dry-run 只预览）")
    ap.add_argument("--offset", type=int, default=0, help="跳过前 N 个")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个（0=全部）")
    ap.add_argument("--delay", type=float, default=0.5, help="每次删除间隔（秒）")
    args = ap.parse_args()

    if not os.path.exists(DELETE_SET):
        print(f"❌ 未找到 {DELETE_SET}，请先跑 scripts/find_duplicates.py")
        return
    tokens = json.load(open(DELETE_SET, encoding="utf-8"))
    if args.offset:
        tokens = tokens[args.offset:]
    if args.limit:
        tokens = tokens[:args.limit]

    meta = {}
    if os.path.exists(META_PATH):
        try:
            meta = json.load(open(META_PATH, encoding="utf-8"))
        except Exception:
            pass

    print(f"{'🚨 真正删除模式' if args.apply else '👁 dry-run 预览'}：{len(tokens)} 个节点")
    ok = 0
    for i, nt in enumerate(tokens, 1):
        m = meta.get(nt, {})
        title = m.get("title", "?")
        url = m.get("feishu_url", f"https://r1t40urlzrp.feishu.cn/wiki/{nt}")
        if args.apply:
            cmd = [LARK_CLI, "wiki", "+node-delete", "--node-token", nt,
                   "--obj-type", "wiki", "--include-children=false",
                   "--yes", "--as", "user"]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
                status = "✅" if r.returncode == 0 else f"❌({r.returncode})"
                print(f"  {status} [{i}/{len(tokens)}] {title}  {url}")
                print(f"       {r.stdout.strip()[:120]}")
                ok += 1 if r.returncode == 0 else 0
            except Exception as e:
                print(f"  ❌ERR [{i}/{len(tokens)}] {title}  {url}  -> {e!r}")
            time.sleep(args.delay)
        else:
            print(f"  · [{i}/{len(tokens)}] {title}  {url}")
    if args.apply:
        print(f"\n完成：成功删除 {ok}/{len(tokens)} 个")
        if ok > 0:
            # 删除会改变容器下子节点，必须重建受影响的总览，否则总览文档会残留已删节点。
            print("\n🔄 开始重建受影响的总览...")
            from shared import feishu_overview as fo
            folders = set()
            for nt in tokens:  # tokens 已被 offset/limit 切片，正是本次删除的节点
                p = meta.get(nt, {}).get("path", [])
                if len(p) >= 3:
                    folders.add("【监控】/" + "/".join(p[:-1]))
            for folder in sorted(folders):
                try:
                    n = fo.rebuild(folder)
                    print(f"  ✅ {folder} → 重建 {n} 条")
                except Exception as e:
                    print(f"  ⚠️ {folder} 重建失败: {e}")
    else:
        print(f"\n（dry-run 未删除任何节点。加 --apply 才真正删除；可先 --limit N 试水）")


if __name__ == "__main__":
    main()
