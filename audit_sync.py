"""audit_sync.py — Obsidian ↔ 飞书 一致性审计 + 补传闸门（仅双写模式启用）。

注意（2026-08-08 起默认只写飞书）：本脚本用于「已开启 Obsidian 双写」时核对两端。
默认单写飞书时，Obsidian 侧为空是正常的，运行本脚本只会报「Obsidian 缺大量笔记」噪声，
故单写模式下无需跑；想双写时设 `OBSIDIAN_WRITE=1`（或带 --obsidian）再跑。

背景：2026-08-01 批量双写后发现第11集（标题含 &）静默漏飞书节点，靠人工清点才发现。
本脚本把「双写后必须核对两端」固化成可复用的自动化闸门，避免再靠记忆力兜底。

契约（与 blog-article-skill 双写规则一致）：
- Obsidian 成品根 = OBSIDIAN_VAULT_PATH（即「AI 总结笔记」文件夹）
- 飞书根节点       = FEISHU_WIKI_PARENT_NODE（即「AI 总结笔记」节点）
- 两级树结构对称：容器(文件夹)名一致；文档名 = 文件名去 .md，
  飞书侧对文档标题做 _sanitize_title（&→和 等），比对时同样归一。

用法：
  python audit_sync.py                 # 仅报告差异（不改动任何数据）
  python audit_sync.py --fix          # 报告 + 把「缺飞书」的 Obsidian 笔记幂等补传
  python audit_sync.py --obs-root X --feishu-node Y   # 覆盖路径（高级）

也可被其他脚本导入并调用 run_audit(fix=False)，在批量双写末尾自动跑校验。
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

from articles.feishu import FeishuOutput, _sanitize_title  # noqa: E402

OBS_ROOT = os.getenv("OBSIDIAN_VAULT_PATH", "")

# 跳过的中间产物 / 隐藏目录
SKIP_FILE_PREFIXES = ("_summary_", "_raw_", ".", "~")
SKIP_DIRS = {"notes", ".workbuddy", ".git", "__pycache__", ".obsidian", ".trash"}


def _obs_key(rel: str) -> str:
    """rel 形如 a/b/name.md → 末段做 _sanitize_title（与飞书文档标题一致），其余段原样。"""
    rel = rel[:-3] if rel.lower().endswith(".md") else rel
    parts = [p for p in rel.split("/") if p]
    if not parts:
        return ""
    parts[-1] = _sanitize_title(parts[-1])
    return "/".join(parts)


def collect_obs(root: str) -> dict:
    """返回 {规范化key: 绝对路径}，仅 .md 成品（排除中间产物/隐藏）。"""
    notes = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if not fn.lower().endswith(".md"):
                continue
            if fn.startswith(SKIP_FILE_PREFIXES):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            key = _obs_key(rel)
            if key:
                notes[key] = full
    return notes


def collect_feishu(feishu: FeishuOutput, parent_token: str, prefix: str = "") -> dict:
    """递归列出飞书叶子文档，返回 {规范化key: node_token}。

    容器(文件夹)判定：对其 list_children 非空 → 递归；为空 → 叶子文档。
    叶子文档标题已是 _sanitize_title 后的，与目标 key 直接对齐。
    """
    leaves = {}
    children = feishu.list_children(parent_token)
    for node in children:
        title = node.get("title", "")
        tok = node.get("node_token", "")
        if not tok:
            continue
        rel = f"{prefix}/{title}" if prefix else title
        sub = feishu.list_children(tok)
        if sub:
            leaves.update(collect_feishu(feishu, tok, rel))
        else:
            leaves[rel] = tok
    return leaves


def push_missing(feishu: FeishuOutput, missing: list) -> tuple:
    """missing: [(key, obs_path), ...]。逐条幂等补传（已存在则跳过）。"""
    created = skipped = failed = 0
    for key, obs_path in missing:
        fn = key + ".md"  # save 内部按 '/' 拆子目录、对末段再 sanitize（幂等）
        dirs, base = feishu._split_subdir(fn)
        parent = feishu.ensure_folder_path(dirs) if dirs else feishu.ensure_inbox_node()
        leaf_title = _sanitize_title(os.path.splitext(base)[0])
        if not parent:
            print(f"  ❌ 容器节点建不出，跳过：{key[:60]}")
            failed += 1
            continue
        # 幂等：父节点下已存在同名 → 跳过
        exists = any(n.get("title") == leaf_title for n in feishu.list_children(parent))
        if exists:
            print(f"  ✓ 飞书已存在，跳过：{key[:60]}")
            skipped += 1
            continue
        with open(obs_path, "r", encoding="utf-8") as f:
            content = f.read()
        if feishu.save(content, fn, parent_token=parent):
            print(f"  ✅ 已补传飞书：{key[:60]}")
            created += 1
        else:
            print(f"  ❌ 补传失败：{key[:60]}")
            failed += 1
    return created, skipped, failed


def run_audit(fix: bool = False, obs_root: str = None, feishu_node: str = None) -> tuple:
    """执行审计，返回 (missing_in_feishu: list, orphan_in_feishu: list)。

    仅在 fix=True 时改动数据（补传缺飞书的笔记）。绝不删除任何侧的数据。
    """
    feishu = FeishuOutput()
    if not feishu.is_available():
        print("⚠️ 飞书不可用，无法审计，退出")
        return [], []
    root = obs_root or OBS_ROOT
    if not root or not os.path.isdir(root):
        print(f"⚠️ Obsidian 成品根不存在：{root}")
        return [], []
    parent = feishu_node or feishu.wiki_parent_node
    if not parent:
        print("⚠️ 未配置 FEISHU_WIKI_PARENT_NODE，退出")
        return [], []

    print(f"📂 Obsidian 根：{root}")
    print(f"📁 飞书根节点：{parent}")

    obs = collect_obs(root)
    fei = collect_feishu(feishu, parent)

    obs_keys = set(obs)
    fei_keys = set(fei)

    missing_in_feishu = sorted(obs_keys - fei_keys)   # Obsidian 有、飞书缺 → 待补
    orphan_in_feishu = sorted(fei_keys - obs_keys)    # 飞书有、Obsidian 无 → 待人工复核

    print(f"\n=== 一致性报告 ===")
    print(f"Obsidian 成品：{len(obs_keys)} 篇")
    print(f"飞书文档    ：{len(fei_keys)} 篇")
    print(f"缺飞书（待补）：{len(missing_in_feishu)} 篇")
    print(f"飞书孤儿（待复核）：{len(orphan_in_feishu)} 篇")

    if missing_in_feishu:
        print("\n--- 缺飞书（Obsidian 有、飞书无）---")
        for k in missing_in_feishu:
            print(f"  + {k}")
    if orphan_in_feishu:
        print("\n--- 飞书孤儿（飞书有、Obsidian 无；只报告不删，需人工复核）---")
        for k in orphan_in_feishu:
            print(f"  ? {k}")

    if fix and missing_in_feishu:
        print("\n=== 开始补传 ===")
        missing = [(k, obs[k]) for k in missing_in_feishu]
        c, s, f = push_missing(feishu, missing)
        print(f"\n=== 补传汇总 ===\n新增：{c}  跳过(已存在)：{s}  失败：{f}")
    elif fix:
        print("\n✓ 无缺口，无需补传")

    return missing_in_feishu, orphan_in_feishu


def main():
    import argparse
    p = argparse.ArgumentParser(description="Obsidian ↔ 飞书 双写一致性审计")
    p.add_argument("--fix", action="store_true", help="缺飞书的笔记自动幂等补传")
    p.add_argument("--obs-root", default=OBS_ROOT, help="Obsidian 成品根（覆盖 OBSIDIAN_VAULT_PATH）")
    p.add_argument("--feishu-node", default=None, help="飞书根节点 token（覆盖 FEISHU_WIKI_PARENT_NODE）")
    args = p.parse_args()

    missing, _ = run_audit(fix=args.fix, obs_root=args.obs_root, feishu_node=args.feishu_node)

    # 退出码：仅报告模式且有缺口时返回 1（便于编排/Cron 感知失败）
    if missing and not args.fix:
        sys.exit(1)


if __name__ == "__main__":
    main()
