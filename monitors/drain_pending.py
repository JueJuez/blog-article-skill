"""monitors/drain_pending.py — 单篇 pending 落盘标准入口（代码门禁，防字段名漂移）。

FORCE_AGENT_MODE=1 下，执行模型（子 Agent）先把每条 pending 的总结写到
<temp_dir>/sum_<index>.md，再调用本脚本落盘。标题字段、标签、folder 映射全部
固化在此处，子 Agent 不再手搓，避免「未命名笔记」类 bug 重现。

用法：
  python monitors/drain_pending.py --indices 0,1,2 --apply
  python monitors/drain_pending.py --indices 0-5 --dry-run   # 只打印，不落盘
  python monitors/drain_pending.py --all --apply             # 落全部（默认只飞书）

输出契约：默认只写飞书（obsidian=False）。双写加 --obsidian。
"""
import os
import sys
import json
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PENDING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pending_summaries.json")
DEFAULT_TEMP = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Temp"
)

TAGMAP = {
    'structured': '结构化复盘', 'key_points': '要点提炼', 'case': '案例拆解',
    'opinion': '观点卡', 'roundup': '盘点', 'interview': '访谈', 'reading': '读书笔记',
}


def load_pending():
    if not os.path.exists(PENDING_PATH):
        return []
    return json.load(open(PENDING_PATH, encoding="utf-8"))


def parse_indices(spec: str, total: int):
    idx = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            idx.update(range(int(a), int(b) + 1))
        else:
            idx.add(int(part))
    return sorted(i for i in idx if 0 <= i < total)


def _extract_h1(content: str) -> str:
    """取总结文件首个 # 标题行（子 Agent 已从正文提炼的有信息量标题）。"""
    for ln in content.splitlines():
        s = ln.strip()
        if s.startswith("#"):
            h = s.lstrip("#").strip()
            if h:
                return h
    return ""


def drain(indices, temp_dir, apply, obsidian):
    pending = load_pending()
    total = len(pending)
    if not indices:
        indices = list(range(total))
    print(f"pending 共 {total} 条，本次处理索引：{indices}")

    bad = 0
    for i in indices:
        if i >= total:
            print(f"  ⚠️ 索引 {i} 越界，跳过")
            continue
        it = pending[i]
        # 关键门禁：pending 字段是 title，映射到落盘 original_title 参数
        title = it.get("title") or it.get("original_title", "")
        folder = it.get("folder", "")
        acct = folder.split("/")[-1] if folder else ""
        nt = it.get("note_type", "")
        tags = [TAGMAP.get(nt, "文章总结"), acct] if acct else [TAGMAP.get(nt, "文章总结")]
        sum_file = os.path.join(temp_dir, f"sum_{i}.md")

        if not apply:
            status = "OK" if os.path.exists(sum_file) else "缺sum文件"
            flag = "" if title else " ⚠️标题为空"
            print(f"  [dry-run {i}] title={title[:28]!r} tags={tags} folder={folder!r} ({status}){flag}")
            continue

        if not os.path.exists(sum_file):
            print(f"  ⚠️ 索引 {i} 缺总结文件 {sum_file}，跳过")
            bad += 1
            continue
        content = open(sum_file, encoding="utf-8").read()
        # 标题机械优先序（用户 2026-08-25 决策：不依赖模型）：
        #   来源侧标题（title，已由 derive_title_from_body 确定性提炼）> 总结 H1 > 来源；
        #   全程 normalize_title 清洗（去模型段标题/非法字符/折叠空白/截断）。
        # 旧逻辑 node_title = _extract_h1(content) or title 把"模型自创的段标题"
        # 当节点标题，是飞书标题乱/错的根因，已废弃。
        from shared.title_norm import choose_node_title
        node_title = choose_node_title(title, _extract_h1(content))
        # L1 硬卡①：标题为空则直接报错跳过，绝不静默落盘成「未命名笔记」
        if not node_title.strip():
            print(f"  ❌ 索引 {i} 标题为空，跳过落盘（防未命名笔记）")
            bad += 1
            continue

        from articles.main import save_summary_only
        res = save_summary_only({
            "summarized_content": content,
            "original_url": it.get("url", ""),
            "author": it.get("author", ""),
            "tags": tags,
            "original_title": node_title,
            "publish_time": it.get("publish_time", 0),
            "folder": folder,
            "obsidian": obsidian,
        })
        fn = res.get("filename") or ""
        ok = res.get("success")
        # L1 硬卡②：落盘后自检文件名，含「未命名笔记」或失败即记为门禁失败
        if (not ok) or ("未命名笔记" in fn):
            bad += 1
            print(f"  ⚠️ [{i}] {node_title[:28]} => 失败/异常文件名: {fn or res.get('message')}")
        else:
            print(f"  [{i}] {node_title[:28]} => OK {fn}")

    print(f"\n完成：处理 {len(indices)} 项，异常 {bad} 项")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--indices", default="", help="逗号/连字符范围，如 0,1,2 或 0-5")
    ap.add_argument("--all", action="store_true", help="处理全部")
    ap.add_argument("--temp-dir", default=DEFAULT_TEMP, help="sum_<i>.md 所在目录")
    ap.add_argument("--apply", action="store_true", help="实际落盘（默认 dry-run）")
    ap.add_argument("--obsidian", action="store_true", help="同时写 Obsidian（默认只飞书）")
    args = ap.parse_args()

    pending = load_pending()
    if args.all:
        idx = list(range(len(pending)))
    else:
        idx = parse_indices(args.indices, len(pending))
    bad = drain(idx, args.temp_dir, args.apply, args.obsidian)
    if args.apply and bad:
        sys.exit(2)


if __name__ == "__main__":
    main()
