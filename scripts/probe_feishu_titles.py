"""scripts/probe_feishu_titles.py — 存量飞书节点标题诊断探针（只读，不改数据）。

目的：验证「按 source_url 重抓真实标题 → 给节点改名」的可行性 & 覆盖率。
不删除/不创建任何节点，只读取并对比。

用法：
  python scripts/probe_feishu_titles.py                  # 只统计覆盖率 + 列出无 source_url 的节点（不联网）
  python scripts/probe_feishu_titles.py --sample 8      # 额外重抓前 8 个有 source_url 的节点，看真标题质量
  python scripts/probe_feishu_titles.py --account 哥飞   # 只扫某账号（路径片段匹配）

输出：每个文档节点 当前标题 / source_url / 建议标题 / 是否需改；末尾汇总覆盖率。
"""
import os
import re
import sys
import time
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

from articles.feishu import FeishuOutput
from shared.title_norm import choose_node_title


def parse_node_meta(body: str):
    """从笔记正文抽取 (source_url, body_h1)。

    真实情况（探查 2026-08-25）：
    - source_url 既可能在 YAML frontmatter（--- source_url: ... ---），
      也可能写在 markdown 行 `## source_url: [url](url)`（旧落盘格式）。
    - 正文首个 `# ` 是模板写入的「原文章标题」（original_title），即真实标题候选。
    - 节点顶部偶见飞书 docx 的 `<title>节点名</title>` XML 标签，需剥离。
    """
    surl = ""
    h1 = ""
    if not body:
        return surl, h1
    # 剥 XML <title> 标签（飞书 docx 导出会有）
    body = re.sub(r"^\s*<title>.*?</title>\s*", "", body, flags=re.DOTALL)
    # 1) YAML frontmatter
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", body, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            s = line.strip()
            if s.startswith("source_url:"):
                surl = s[len("source_url:"):].strip().strip('"').strip("'")
                break
    # 2) markdown 行：## source_url: [url](url) 或 source_url: url
    if not surl:
        for line in body.splitlines():
            s = line.strip().lstrip("#").strip()
            if s.startswith("source_url:"):
                val = s[len("source_url:"):].strip()
                mm = re.search(r"\((https?://[^)]+)\)", val)
                surl = mm.group(1) if mm else val.strip().strip("[]()")
                break
    # 3) 正文首个 # 标题 = 原文章标题
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            h1 = s[2:].strip()
            break
    return surl, h1


def find_monitor_root(f: FeishuOutput) -> str:
    """从配置父节点往下找【监控】容器；找不到则用配置父节点本身。"""
    parent = f.wiki_parent_node
    if not parent:
        return ""
    for node in f.list_children(parent):
        if node.get("title", "").startswith("【监控】"):
            return node.get("node_token", "")
    return parent


def collect_doc_nodes(f: FeishuOutput, root_token: str, account_filter: str = "",
                       max_nodes: int = 0, sleep: float = 0.25):
    """BFS 收集所有真正的「文档」叶子节点（无子节点、非总览）。

    容器节点（B站/公众号/账号/日更/系列名）即便有 obj_token，也仍会有子节点，
    因此正确判据是「无子节点才是文档叶子」，而非「有 obj_token 就是文档」。
    返回 [(path, node)]。

    max_nodes>0 时收集到上限即停（避免大树遍历超时）；sleep 给飞书 CLI 限速。
    """
    out = []
    visited = set()
    # 栈元素 (node_token, path, node_or_None)；根节点无 node 信息用 None
    stack = [(root_token, "", None)]

    def is_overview(title: str) -> bool:
        return title.startswith("📋 总览") or title.startswith("00_")

    while stack:
        tok, path, node = stack.pop()
        if tok in visited:
            continue
        visited.add(tok)
        time.sleep(sleep)
        kids = f.list_children(tok)
        if kids:
            # 有子节点 → 容器，继续下钻（无论其自身是否有 obj_token）
            for k in kids:
                cp = f"{path}/{k.get('title', '')}" if path else k.get("title", "")
                stack.append((k.get("node_token", ""), cp, k))
        else:
            # 无子节点 → 真正的文档叶子（根节点自身 node=None 时跳过）
            if node is None:
                continue
            title = node.get("title", "")
            if is_overview(title):
                continue
            if account_filter and account_filter not in path:
                continue
            out.append((path, node))
            if max_nodes and len(out) >= max_nodes:
                break
    return out


def fetch_real_title(source_url: str):
    """按 source_url 重抓真实标题。返回 (title_or_None, err_or_None)。"""
    if not source_url or not source_url.startswith("http"):
        return None, "no-http-url"
    try:
        from articles.fetch import fetch_web_content
        res = fetch_web_content(source_url)
        if isinstance(res, tuple) and len(res) >= 1:
            t = res[0] or ""
            return (t.strip() if t else ""), None
        return None, "bad-return"
    except Exception as e:  # noqa
        return None, f"exc:{type(e).__name__}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0,
                    help="重抓前 N 个有 source_url 的节点以验证真实标题质量（默认 0=不联网）")
    ap.add_argument("--account", default="", help="只扫路径含该片段的账号")
    ap.add_argument("--max-nodes", type=int, default=0, help="最多收集 N 个文档节点即停（防大树超时）")
    ap.add_argument("--sleep", type=float, default=0.25, help="list_children 之间休眠秒数（限速）")
    ap.add_argument("--out", default="", help="报告写入文件（便于后台运行后查看）")
    args = ap.parse_args()

    f = FeishuOutput()
    if not f.is_available():
        print("❌ 飞书不可用（FEISHU_WIKI_SPACE / lark-cli 未配置）")
        return

    root = find_monitor_root(f)
    if not root:
        print("❌ 找不到监控根节点")
        return
    print(f"📂 监控根：{root}")

    docs = collect_doc_nodes(f, root, args.account, max_nodes=args.max_nodes, sleep=args.sleep)
    print(f"📄 文档叶子节点共 {len(docs)} 个" +
          (f"（账号过滤={args.account!r}）" if args.account else ""))

    total = broken = with_url = recoverable = would_change = 0
    fetched_ok = fetched_fail = fetched_count = 0
    no_url_broken = []
    change_samples = []
    from shared.title_norm import is_generic_section_header

    for path, node in docs:
        total += 1
        cur_title = node.get("title", "")
        body = ""
        try:
            r = f._run_cli_command(["docs", "+fetch", "--doc", node.get("obj_token", ""),
                                    "--doc-format", "markdown", "--scope", "full",
                                    "--as", "user", "--json"])
            body = (r.get("data", {}).get("document", {}).get("content", "")
                    if isinstance(r, dict) else "") or ""
        except Exception:
            body = ""
        surl, body_h1 = parse_node_meta(body)
        if surl:
            with_url += 1

        # 真正损坏：未命名笔记 / 模型段标题（总结/要点/摘要…）
        is_broken = cur_title.startswith("未命名笔记") or is_generic_section_header(cur_title)
        if not is_broken:
            continue
        broken += 1

        # 本地可恢复：正文首个 # 即模板写入的「原文章标题」
        proposed = choose_node_title(body_h1, "") if body_h1 else ""
        if proposed and proposed != cur_title:
            recoverable += 1
            would_change += 1
            # 联网验证：source_url 重抓到的标题是否一致/更优
            if surl and args.sample > 0 and fetched_count < args.sample:
                real, err = fetch_real_title(surl)
                fetched_count += 1
                if real:
                    fetched_ok += 1
                    alt = choose_node_title(real, "")
                    change_samples.append((path, cur_title, surl, body_h1, alt))
                else:
                    fetched_fail += 1
            elif not surl and len(no_url_broken) < 30:
                no_url_broken.append((path, cur_title, body_h1))

    print("\n--- 诊断汇总 ---")
    print(f"文档节点总数        : {total}")
    print(f"标题损坏(未命名/段标题): {broken}")
    print(f"  含 source_url     : {with_url}")
    print(f"  本地可恢复(正文H1): {recoverable}")
    if args.sample:
        print(f"联网重抓样本        : {fetched_count}")
        print(f"  成功              : {fetched_ok}")
        print(f"  失败              : {fetched_fail}")

    if args.sample and change_samples:
        print("\n--- 联网重抓样本（当前 → 正文H1 → source_url重抓）---")
        for path, cur, surl, h1, alt in change_samples[:20]:
            print(f"★ {path}")
            print(f"    当前   : {cur}")
            print(f"    正文H1 : {h1}")
            print(f"    URL重抓: {alt}  ({surl})")
    if no_url_broken:
        print(f"\n--- 损坏但无 source_url 的节点（共 {len(no_url_broken)} 个样本）---")
        for path, cur, h1 in no_url_broken:
            print(f"  · {path} | 当前:{cur} | 正文H1:{h1!r}")

    report = []
    report.append("=== 诊断汇总 ===")
    report.append(f"文档节点总数        : {total}")
    report.append(f"标题损坏            : {broken}")
    report.append(f"本地可恢复(正文H1)  : {recoverable}")
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write("\n".join(report) + "\n")
                if change_samples:
                    fh.write("\n--- 联网重抓样本 ---\n")
                    for path, cur, surl, h1, alt in change_samples[:20]:
                        fh.write(f"★ {path}\n  当前:{cur}\n  正文H1:{h1}\n  URL重抓:{alt}\n  {surl}\n")
                if no_url_broken:
                    fh.write(f"\n--- 无source_url损坏节点(共{len(no_url_broken)}) ---\n")
                    for path, cur, h1 in no_url_broken:
                        fh.write(f"  · {path} | {cur} | {h1!r}\n")
            print(f"\n📝 报告已写入 {args.out}")
        except Exception as e:
            print(f"⚠️ 写报告失败：{e}")


if __name__ == "__main__":
    main()
