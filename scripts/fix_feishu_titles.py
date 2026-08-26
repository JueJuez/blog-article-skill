"""scripts/fix_feishu_titles.py — 存量飞书节点标题修复（破坏性，需 --apply 显式开启）。

背景（用户 2026-08-25）：历史落盘的部分飞书节点标题损坏——表现为「未命名笔记-<时间戳>」
或模型自创段标题（总结/要点…），而非真实文章标题。新落盘已通过 shared.title_norm
机械修正；存量需 remediation。

机制约束：飞书无「原地改名节点」API（wiki 只有 create/delete/move/copy；docs +update 只能
改正文不能改标题）。故修复 = 对损坏节点「删旧 + 用正确标题新建」（feishu.save upsert），
会改变节点 URL → 全部完成后必须重生成总览（--regen-overviews 调 feishu_overview.rebuild）。

真实标题如何机械恢复（探查结论）：
- 损坏节点正文首个 `# ` 即模板写入的「原文章标题」（original_title），是可靠真实标题。
- 节点正文含 source_url（markdown 行 `## source_url: [url](url)` 或 YAML frontmatter），
  可联网重抓进一步确认/修正标题。
- 仅处理「标题损坏」的节点（未命名笔记 / 模型段标题），正确标题的节点一律不动。

安全设计：
- 默认 dry-run：只打印 当前→建议，零数据改动。
- --apply：分批（--batch），限速 sleep，断点续跑（state 记已处理 node_token）。
- --account 限定单账号先试点。每节点二次确认 proposed != current 且非段标题。
- 进度写 state，中断后续跑同命令接着处理。

用法：
  python scripts/fix_feishu_titles.py --dry-run             # 全量预览（本地恢复，不联网）
  python scripts/fix_feishu_titles.py --sample 5           # 额外联网重抓前5个验证
  python scripts/fix_feishu_titles.py --account 中金点睛 --apply --batch 10
  python scripts/fix_feishu_titles.py --apply --batch 50 --regen-overviews
"""
import os
import re
import sys
import json
import time
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

from articles.feishu import FeishuOutput
from shared.title_norm import choose_node_title, is_generic_section_header

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".fix_titles_state.json")


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            return json.load(open(STATE_PATH, encoding="utf-8"))
        except Exception:
            pass
    return {"processed": [], "touched_folders": []}


def save_state(st):
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=2)


def parse_node_meta(body: str):
    """返回 (source_url, body_h1)。详见 probe_feishu_titles.parse_node_meta。"""
    surl = ""
    h1 = ""
    if not body:
        return surl, h1
    body = re.sub(r"^\s*<title>.*?</title>\s*", "", body, flags=re.DOTALL)
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", body, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            s = line.strip()
            if s.startswith("source_url:"):
                surl = s[len("source_url:"):].strip().strip('"').strip("'")
                break
    if not surl:
        for line in body.splitlines():
            s = line.strip().lstrip("#").strip()
            if s.startswith("source_url:"):
                val = s[len("source_url:"):].strip()
                mm = re.search(r"\((https?://[^)]+)\)", val)
                surl = mm.group(1) if mm else val.strip().strip("[]()")
                break
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            h1 = s[2:].strip()
            break
    return surl, h1


def is_broken_title(t: str) -> bool:
    return t.startswith("未命名笔记") or is_generic_section_header(t)


def find_monitor_root(f: FeishuOutput) -> str:
    parent = f.wiki_parent_node
    if not parent:
        return ""
    for node in f.list_children(parent):
        if node.get("title", "").startswith("【监控】"):
            return node.get("node_token", "")
    return parent


def collect_leaf_docs(f: FeishuOutput, root_token: str, account_filter: str = "",
                      max_nodes: int = 0, sleep: float = 0.25):
    out = []
    visited = set()
    stack = [(root_token, "", None, "")]
    seen_paths = set()

    def is_overview(t):
        return t.startswith("📋 总览") or t.startswith("00_")

    while stack:
        tok, path, node, parent_tok = stack.pop()
        if tok in visited:
            continue
        visited.add(tok)
        time.sleep(sleep)
        kids = f.list_children(tok)
        if kids:
            for k in kids:
                cp = f"{path}/{k.get('title', '')}" if path else k.get("title", "")
                stack.append((k.get("node_token", ""), cp, k, tok))
        else:
            if node is None:
                continue
            title = node.get("title", "")
            if is_overview(title):
                continue
            if account_filter and account_filter not in path:
                continue
            out.append((path, node, parent_tok))
            if max_nodes and len(out) >= max_nodes:
                break
    return out


def fetch_real_title(source_url: str, cache: dict):
    if not source_url or not source_url.startswith("http"):
        return None
    if source_url in cache:
        return cache[source_url]
    try:
        from articles.fetch import fetch_web_content
        res = fetch_web_content(source_url)
        t = (res[0] or "").strip() if isinstance(res, tuple) and len(res) >= 1 else ""
        cache[source_url] = t
        return t
    except Exception:
        cache[source_url] = ""
        return None


def read_body(f: FeishuOutput, obj_token: str) -> str:
    try:
        r = f._run_cli_command(["docs", "+fetch", "--doc", obj_token,
                                "--doc-format", "markdown", "--scope", "full",
                                "--as", "user", "--json"])
        return (r.get("data", {}).get("document", {}).get("content", "")
                if isinstance(r, dict) else "") or ""
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="仅预览，不改动（默认）")
    ap.add_argument("--apply", action="store_true", help="真正执行删除+重建")
    ap.add_argument("--sample", type=int, default=0, help="额外联网重抓前 N 个验证质量")
    ap.add_argument("--account", default="", help="限定账号（路径片段）")
    ap.add_argument("--batch", type=int, default=20, help="本次最多改 N 个（--apply 时）")
    ap.add_argument("--max-nodes", type=int, default=0, help="遍历最多收集 N 个文档节点")
    ap.add_argument("--sleep", type=float, default=0.3, help="CLI 限速休眠秒数")
    ap.add_argument("--regen-overviews", action="store_true", help="完成后重建涉及 folder 的总览")
    args = ap.parse_args()
    if not args.apply:
        args.dry_run = True

    f = FeishuOutput()
    if not f.is_available():
        print("❌ 飞书不可用")
        return
    root = find_monitor_root(f)
    if not root:
        print("❌ 找不到监控根节点")
        return

    st = load_state()
    processed = set(st.get("processed", []))
    touched = set(st.get("touched_folders", []))
    cache = {}

    docs = collect_leaf_docs(f, root, args.account, max_nodes=args.max_nodes, sleep=args.sleep)
    print(f"📄 收集文档节点 {len(docs)} 个" + (f"（账号={args.account!r}）" if args.account else ""))

    broken = recoverable = applied = skipped = 0
    for path, node, parent_tok in docs:
        cur = node.get("title", "")
        if not is_broken_title(cur):
            continue
        broken += 1
        obj = node.get("obj_token", "")
        body = read_body(f, obj) if obj else ""
        surl, body_h1 = parse_node_meta(body)
        # 本地恢复（正文首个 # = 原文章标题）
        proposed = choose_node_title(body_h1, "") if body_h1 else ""
        if not proposed:
            # 试试 source_url 重抓
            if surl and (args.sample or args.apply):
                real = fetch_real_title(surl, cache)
                proposed = choose_node_title(real, "") if real else ""
        if not proposed or proposed == cur or is_generic_section_header(proposed):
            continue
        recoverable += 1
        folder = "/".join(path.split("/")[:-1])

        if args.dry_run and not args.apply:
            print(f"★ {path}\n    当前: {cur}\n    正文H1: {body_h1}\n    建议: {proposed}"
                  + (f"\n    URL: {surl}" if surl else ""))
            continue
        if args.apply:
            if node.get("node_token") in processed:
                skipped += 1
                continue
            if applied >= args.batch:
                print(f"  ⏸️ 已达本批 {args.batch} 上限，停止（续跑：同命令再跑一次）")
                break
            try:
                content = read_body(f, obj)
                f.delete_node(node.get("node_token"))
                time.sleep(0.6)
                ok = f.save(content, filename=proposed, parent_token=parent_tok, title=proposed)
                if ok:
                    processed.add(node.get("node_token"))
                    touched.add(folder)
                    applied += 1
                    print(f"  ✅ {cur} → {proposed}")
                else:
                    print(f"  ⚠️ 重建失败：{cur}")
            except Exception as e:
                print(f"  ⚠️ 处理异常 {cur}: {e}")
            time.sleep(args.sleep)

    st["processed"] = list(processed)
    st["touched_folders"] = list(touched)
    save_state(st)

    print(f"\n=== 小结 ===")
    print(f"收集节点 : {len(docs)}")
    print(f"损坏节点 : {broken}")
    print(f"可恢复   : {recoverable}")
    if args.apply:
        print(f"本批已改 : {applied}（续跑可继续）")
        print(f"累计已处理: {len(processed)}")
        if args.regen_overviews and touched:
            print(f"🔄 重建总览：{len(touched)} 个 folder")
            from shared import feishu_overview as fo
            for fol in sorted(touched):
                try:
                    dirs = [d for d in fol.split("/") if d]
                    parent_token = f.ensure_folder_path(dirs) if dirs else f.wiki_parent_node
                    if not parent_token:
                        print(f"  ⚠️ 找不到容器，跳过：{fol}")
                        continue
                    n = fo.rebuild(fol, parent_token=parent_token)
                    print(f"  ✅ {fol} → 重建 {n} 条")
                except Exception as e:
                    print(f"  ⚠️ 重建总览失败 {fol}: {e}")


if __name__ == "__main__":
    main()
