"""scripts/find_duplicates.py — 检测飞书里的重复总结节点（不改任何数据）。

重复定义（用户 2026-08-25：飞书总结内容里存在大量重复总结内容）：
  1. 同 source_url：同一原文被总结/落盘多次 → 多个节点指向同一篇文章。
  2. 同正文指纹：归一化正文（剥 frontmatter）sha256 一致 → 即使 source_url 不同，
     正文也逐字相同（模型复用/复制粘贴产物）。

产出（均只读，不删不改）：
  - notes/_audit/node_meta.json  每个节点元数据缓存（供删除脚本复用，支持断点续跑）
  - notes/_audit/duplicates.md   重复分组报告（每组列出成员 + 推荐保留节点）

删除是破坏性操作，本脚本只检测。确认后再用单独脚本按 node_meta.json 删除冗余节点。

用法：
  python scripts/find_duplicates.py --platform 公众号 --limit 10   # 试跑
  python scripts/find_duplicates.py                               # 全量检测
  python scripts/find_duplicates.py --platform 公众号             # 只某平台
"""
import os
import re
import sys
import json
import time
import argparse
import subprocess
import hashlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

from shared import feishu_overview as fo
from articles.dedup import _normalize_url, _hash

SPACE_ID = os.getenv("FEISHU_WIKI_SPACE", "7636965310725115074")
TENANT_DOMAIN = "r1t40urlzrp.feishu.cn"
MONITOR_ROOT = "VnVTwf8vJi7VFgkyKG4cwCTpnfb"
META_PATH = os.path.join(ROOT, "notes", "_audit", "node_meta.json")
OUT_PATH = os.path.join(ROOT, "notes", "_audit", "duplicates.md")


def _find_lark_cli():
    import shutil
    cand = shutil.which("lark-cli")
    if cand:
        return cand
    base = r"C:\Users\O1830\.workbuddy\binaries\node\cli-connector-packages"
    p = os.path.join(base, "lark-cli")
    return p if os.path.exists(p) else "lark-cli"


LARK_CLI = _find_lark_cli()


def run_cli(args, timeout=60):
    cmd = [LARK_CLI] + args + ["--as", "user", "--format", "json"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if not r.stdout.strip():
        return {}
    try:
        return json.loads(r.stdout)
    except Exception:
        return {}


def list_children(parent_token):
    d = run_cli([
        "wiki", "+node-list", "--space-id", SPACE_ID,
        "--parent-node-token", parent_token, "--page-all", "--page-size", "50",
    ])
    return (d.get("data", {}) or {}).get("nodes", []) or []


def is_overview(title):
    return title.startswith("📋 总览") or title.startswith("00_")


def collect_articles(root_token):
    out = []
    stack = [(root_token, [])]
    seen = set()
    while stack:
        tok, path = stack.pop()
        if tok in seen:
            continue
        seen.add(tok)
        for node in list_children(tok):
            title = node.get("title", "")
            nt = node.get("node_token", "")
            child_path = path + [title]
            if is_overview(title):
                continue
            if node.get("has_child"):
                stack.append((nt, child_path))
            else:
                out.append((child_path, node))
    return out


def fetch_body(obj_token):
    if not obj_token:
        return ""
    d = run_cli([
        "docs", "+fetch", "--doc", obj_token,
        "--doc-format", "markdown", "--scope", "full",
    ], timeout=30)
    return (((d or {}).get("data", {}) or {}).get("document", {}) or {}).get("content", "") or ""


def _strip_frontmatter(body: str) -> str:
    """剥 YAML frontmatter（---...---）只留正文，用于内容指纹。"""
    if body.startswith("---"):
        m = re.match(r"^---\s*\n.*?\n---\s*\n?", body, re.S)
        if m:
            return body[m.end():]
    return body


def content_fingerprint(body: str) -> str:
    text = _strip_frontmatter(body or "")
    text = re.sub(r"\s+", "", text).lower()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_meta():
    if os.path.exists(META_PATH):
        try:
            return json.load(open(META_PATH, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_meta(meta):
    os.makedirs(os.path.dirname(META_PATH), exist_ok=True)
    json.dump(meta, open(META_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="", help="只处理路径含该片段的平台")
    ap.add_argument("--account", default="", help="只处理路径含该账号的节点")
    ap.add_argument("--limit", type=int, default=0, help="最多检查 N 个节点")
    ap.add_argument("--delay", type=float, default=0.1, help="每次抓取间隔（秒）")
    args = ap.parse_args()

    meta = load_meta()  # 断点续跑：已抓过的节点跳过
    print(f"📂 监控根: {MONITOR_ROOT} · 已缓存节点 {len(meta)}")

    articles = collect_articles(MONITOR_ROOT)
    print(f"📄 文章节点共 {len(articles)} 个")
    if args.platform:
        articles = [a for a in articles if args.platform in a[0]]
        print(f"   过滤平台={args.platform!r} → {len(articles)} 个")
    if args.account:
        articles = [a for a in articles if args.account in a[0]]
        print(f"   过滤账号={args.account!r} → {len(articles)} 个")
    if args.limit:
        articles = articles[:args.limit]

    total = len(articles)
    for i, (path, node) in enumerate(articles, 1):
        nt = node.get("node_token", "")
        if nt in meta:
            continue  # 续跑跳过
        title = node.get("title", "")
        obj = node.get("obj_token", "")
        body = fetch_body(obj)
        src = fo.extract_source_url(body)
        src_norm = _normalize_url(src) if src else ""
        fp = content_fingerprint(body)
        meta[nt] = {
            "title": title,
            "path": path,
            "obj_token": obj,
            "feishu_url": f"https://{TENANT_DOMAIN}/wiki/{nt}",
            "source_url": src,
            "source_norm": src_norm,
            "content_fp": fp,
            "body_len": len(body),
        }
        if i % 20 == 0:
            save_meta(meta)
            print(f"  …已抓 {i}/{total}")
        time.sleep(args.delay)
    save_meta(meta)
    print(f"✅ 元数据缓存完成：{len(meta)} 个节点 → {META_PATH}")

    # 分组
    by_src = {}
    by_fp = {}
    for nt, m in meta.items():
        if m.get("source_norm"):
            by_src.setdefault(m["source_norm"], []).append(nt)
        if m.get("content_fp"):
            by_fp.setdefault(m["content_fp"], []).append(nt)

    dup_src = {k: v for k, v in by_src.items() if len(v) > 1}
    dup_fp = {k: v for k, v in by_fp.items() if len(v) > 1}

    print(f"\n🔎 重复分组：")
    print(f"   同 source_url 重复组: {len(dup_src)}（涉及 {sum(len(v) for v in dup_src.values())} 节点）")
    print(f"   同正文指纹重复组: {len(dup_fp)}（涉及 {sum(len(v) for v in dup_fp.values())} 节点）")

    # 生成报告
    def node_line(nt):
        m = meta[nt]
        return f"- [{m['title']}]({m['feishu_url']}) · 正文{m['body_len']}字" + (f" · 原文 {m['source_url']}" if m['source_url'] else "")

    def keep_nt(nts):
        # 推荐保留：正文最长者（最完整）；并列取首个
        return max(nts, key=lambda x: meta[x]["body_len"])

    lines = ["# 飞书重复总结检测报告（只读 · 待确认后删除）\n"]
    lines.append(f"> 检测节点总数: {len(meta)}  ")
    lines.append(f"> 同 source_url 重复: {len(dup_src)} 组 / {sum(len(v) for v in dup_src.values())} 节点  ")
    lines.append(f"> 同正文指纹重复: {len(dup_fp)} 组 / {sum(len(v) for v in dup_fp.values())} 节点\n")

    # 合并去重：同一组可能同时命中两种，统一处理
    groups = []  # (type, key, members)
    for k, v in dup_src.items():
        groups.append(("source", k, v))
    for k, v in dup_fp.items():
        # 若整组已完全被 source 组覆盖则跳过（避免重复报告）
        groups.append(("content", k, v))

    # 标记每组待删节点
    delete_set = set()
    lines.append("\n## 一、同 source_url 重复（同一原文被总结多次）\n")
    for k, v in dup_src.items():
        keep = keep_nt(v)
        keep_fp = meta[keep]["content_fp"]
        to_del, flagged = [], []
        for x in v:
            if x == keep:
                continue
            # 防御门禁：仅当正文指纹也与保留节点一致（确为同一内容副本）才自动删；
            # 正文不同却共享 URL（多为脏 URL 误分组）→ 仅标记待核，不自动删，杜绝误删不同内容。
            if meta[x]["content_fp"] == keep_fp:
                to_del.append(x)
            else:
                flagged.append(x)
        delete_set.update(to_del)
        lines.append(f"\n### 原文 {k}\n")
        lines.append(f"- ✅ 保留: {node_line(keep)}")
        for x in to_del:
            lines.append(f"- ❌ 删除(正文一致): {node_line(x)}")
        for x in flagged:
            lines.append(f"- ⚠️ 待核(共享URL但正文不同，未自动删): {node_line(x)}")
        lines.append("")

    lines.append("\n## 二、同正文指纹重复（正文逐字相同，source 可能不同）\n")
    for k, v in dup_fp.items():
        # 仅展示未被 source 组已覆盖的（避免与第一节完全重复）
        new = [x for x in v if x not in delete_set and x not in {keep_nt(g[2]) for g in groups if g[0] == "source" and set(g[2]) == set(v)}]
        if len(v) <= 1:
            continue
        keep = keep_nt(v)
        to_del = [x for x in v if x != keep and x not in delete_set]
        if not to_del:
            continue
        delete_set.update(to_del)
        lines.append(f"\n### 指纹 {k}\n")
        lines.append(f"- ✅ 保留: {node_line(keep)}")
        for x in to_del:
            lines.append(f"- ❌ 删除: {node_line(x)}")
        lines.append("")

    lines.append(f"\n---\n\n## 汇总\n")
    lines.append(f"- 待删除节点总数（去重后）: **{len(delete_set)}**")
    lines.append(f"- 待保留节点总数: {len(meta) - len(delete_set)}")
    lines.append(f"\n> 确认无误后，运行删除脚本（按 node_meta.json 的 delete_set）执行 `lark-cli wiki +node-delete --yes`。")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    # 备份 delete_set 供删除脚本
    with open(os.path.join(os.path.dirname(OUT_PATH), "delete_set.json"), "w", encoding="utf-8") as fh:
        json.dump(sorted(delete_set), fh, ensure_ascii=False, indent=2)
    print(f"✅ 报告: {OUT_PATH}")
    print(f"✅ 待删节点清单: notes/_audit/delete_set.json ({len(delete_set)} 个)")


if __name__ == "__main__":
    main()
