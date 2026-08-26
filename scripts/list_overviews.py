"""scripts/list_overviews.py — 把各账号下的「📋 总览-*」文档聚合导出为本地清单。

用途（用户 2026-08-25）：飞书标题乱，用户要**手动**改节点标题，不要任何删除/重建。
此脚本只读，产出一份本地 Markdown 清单：
  - 每个总览文档的条目（文章标题 + 飞书直达链接），按「平台 / 账号」分组；
  - 顶部汇总「⚠️ 疑似坏标题」，标出需要手动替换的节点，附直达链接。

实现：
  - 用 `lark-cli wiki +node-list --page-all` 遍历【监控】树，仅下钻 has_child=true 的容器
    （叶子文章 has_child=false，直接跳过），避免列举全部文章节点导致超时。
  - 收集 title 以「📋 总览」或「00_」开头的节点，逐个 docs +fetch 抓正文。
  - 解析正文 markdown 的列表链接项 `[- ](url)` 得到每条标题与直达链接。
  - 疑似坏标题判据（保守，避免误报）：以「未命名」开头；或精确等于模型段标题
    （总结 / 要点提炼 / 摘要 / 概览 / 复盘 / 正文 / 目录 / 概述 / 导读 / 前言 / 简介）。

只读、不修改任何飞书数据。
"""
import os
import re
import sys
import json
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

SPACE_ID = os.getenv("FEISHU_WIKI_SPACE", "7636965310725115074")
# 租户域名（网页可点 URL 用，非 space_id）。来源：feishu_overview.py:235 重建总览
# fallback 域名 `https://r1t40urlzrp.feishu.cn/docx/{obj}`，即本空间租户域名。
TENANT_DOMAIN = "r1t40urlzrp.feishu.cn"
PARENT_NODE = os.getenv("FEISHU_WIKI_PARENT_NODE", "")
MONITOR_ROOT = "VnVTwf8vJi7VFgkyKG4cwCTpnfb"  # 【监控】节点 token（find_monitor_root 实测）

# 疑似坏标题（模型段标题 / 抓取故障产物），仅「整段精确等于段标题」或「未命名开头」，
# 避免误伤真实文章标题（如「复盘一个品牌站…」开头含「复盘」但它是真标题）。
BAD_EXACT = {
    "总结", "总结：", "要点提炼", "要点", "摘要", "概览", "复盘", "正文",
    "目录", "概述", "导读", "前言", "简介", "全文",
}
# 公众号常见误提：把文章开头的寒暄/自我介绍当成了标题
_BAD_GREET_RE = re.compile(
    r"^(大家好|你好|亲爱的|各位朋友|我是|我叫)|大家好[，,、]?\s*我是",
    re.IGNORECASE,
)


def _find_lark_cli():
    import shutil
    cand = shutil.which("lark-cli")
    if cand:
        return cand
    # 兜底：connector 包目录（Bash 工具 PATH 里有，但 subprocess 不一定继承）
    base = r"C:\Users\O1830\.workbuddy\binaries\node\cli-connector-packages"
    p = os.path.join(base, "lark-cli")
    if os.path.exists(p):
        return p
    return "lark-cli"


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


def find_monitor_root():
    if PARENT_NODE:
        for k in list_children(PARENT_NODE):
            if k.get("title", "").strip() == "【监控】":
                return k.get("node_token")
    return MONITOR_ROOT


def collect_overviews(root_token):
    """BFS：仅下钻 has_child=true 的容器（叶子文章不再下钻，省时间）。
    返回 (overviews, articles)：
      - overviews: [(path_list, node)]  总览文档节点
      - articles : [(path_list, node)]  文章/笔记叶子节点（has_child=false 且非总览）
    """
    overviews = []
    articles = []
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
                overviews.append((child_path, node))
                continue
            if node.get("has_child"):
                # 容器：继续下钻
                stack.append((nt, child_path))
            else:
                # 叶子文档（文章/笔记节点）
                articles.append((child_path, node))
    return overviews, articles


_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def fetch_overview_entries(obj_token):
    """抓总览正文，解析出 (title, url) 条目列表。"""
    d = run_cli([
        "docs", "+fetch", "--doc", obj_token,
        "--doc-format", "markdown", "--scope", "full",
    ], timeout=30)
    content = (((d or {}).get("data", {}) or {}).get("document", {}) or {}).get("content", "") or ""
    entries = []
    for line in content.splitlines():
        m = _LINK_RE.search(line)
        if m:
            entries.append((m.group(1).strip(), m.group(2).strip()))
    return entries, content


def is_bad_title(title):
    t = title.strip()
    if t.startswith("未命名"):
        return True
    if t in BAD_EXACT:
        return True
    if _BAD_GREET_RE.search(t):
        return True
    return False


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="", help="只处理某平台（如 B站 / 公众号 / 生财有术）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 个总览（测试用，0=全部）")
    ap.add_argument("--out", default=os.path.join(ROOT, "notes", "_audit", "overview_index.md"))
    args = ap.parse_args()

    root = find_monitor_root()
    print(f"📂 监控根: {root}")
    overviews, articles = collect_overviews(root)
    print(f"📋 总览文档 {len(overviews)} 个 · 📄 文章节点 {len(articles)} 个")

    if args.platform:
        overviews = [o for o in overviews if args.platform in o[0]]
        articles = [a for a in articles if args.platform in a[0]]
        print(f"   过滤平台={args.platform!r} → 总览 {len(overviews)} / 文章 {len(articles)}")

    # 文章节点按账号分组（path 去掉最后的节点标题）
    art_groups = {}
    art_flagged = []
    for path, node in articles:
        account_path = tuple(path[:-1])
        title = node.get("title", "")
        url = f"https://{TENANT_DOMAIN}/wiki/{node.get('node_token','')}"
        art_groups.setdefault(account_path, []).append((title, url))
        if is_bad_title(title):
            art_flagged.append((account_path, title, url))

    # 总览按账号分组（仅作参照，注明其链接在飞书里多为本地路径）
    ov_groups = {}
    for path, node in overviews:
        account_path = tuple(path[:-1])
        ov_groups.setdefault(account_path, []).append(
            (path[-1], f"https://{TENANT_DOMAIN}/wiki/{node.get('node_token','')}")
        )

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    lines = []
    lines.append("# 飞书节点清册（按账号聚合 · 用于手动替换坏标题）\n")
    lines.append("> 只读导出，不修改任何飞书数据。\n")
    lines.append(f"> 扫描结果：总览文档 {len(overviews)} 个（仅 B站系列，且链接多为本地 .md 路径，飞书内不可点）；")
    lines.append(f" 文章节点 {len(articles)} 个，其中疑似坏标题 {len(art_flagged)} 个（见下方第一节，附飞书直达链接）。\n")

    # 第一节：坏标题直达
    lines.append("\n## ⚠️ 需手动替换的坏标题（飞书直达，点开改标题即可）\n")
    if art_flagged:
        for apath, title, url in art_flagged:
            lines.append(f"- [{title}]({url}) — `{' / '.join(apath)}`")
    else:
        lines.append("（未发现疑似坏标题）")
    lines.append("")

    # 第二节：各账号文章节点全量（可逐屏核对）
    lines.append("\n---\n")
    lines.append("\n## 📁 各账号文章节点\n")
    for apath in sorted(art_groups.keys()):
        loc = " / ".join(apath)
        lines.append(f"\n### {loc}  （{len(art_groups[apath])} 篇）\n")
        for title, url in art_groups[apath]:
            mark = " ⚠️" if is_bad_title(title) else ""
            lines.append(f"- [{title}]({url}){mark}")
        lines.append("")

    # 第三节：总览文档（参照）
    if ov_groups:
        lines.append("\n---\n")
        lines.append("\n## 📋 各账号总览文档（参照 · 链接在飞书内多为本地路径，不可点）\n")
        for apath in sorted(ov_groups.keys()):
            loc = " / ".join(apath)
            lines.append(f"\n### {loc}\n")
            for ovt, ovlink in ov_groups[apath]:
                lines.append(f"- {ovt} · [打开总览]({ovlink})")
            lines.append("")

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print(f"\n✅ 报告已写入: {args.out}")
    print(f"   文章节点: {len(articles)}  疑似坏标题: {len(art_flagged)}")
    if art_flagged:
        print("\n⚠️ 疑似坏标题（前 30）:")
        for apath, title, url in art_flagged[:30]:
            print(f"   · [{title}]({url})  ({' / '.join(apath)})")


if __name__ == "__main__":
    main()
