#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/audit_sync.py — Obsidian vault → 飞书 对账/补传（薄壳，2026-09-07 任务5）。

对账 + 幂等补传 + 死链清理 + 登记表 link 回填的**全部逻辑已收编**到
scripts/vault_lifecycle.py 的 `_run_push`（PLAN-20260906 任务5）；
本文件只保留可复用原语（parse_frontmatter / collect_obs / collect_feishu /
classify_missing / classify_only / push_missing 等，供 vault_lifecycle 与测试引用），
CLI 侧做纯转发：--fix/--only/--obs-root 参数语义不变。

身份判定优先级（防误判重复）：feishu_node_token > source_url 提取 token > 路径+标题。
孤儿（飞书有、Obsidian 无）只报告不处理。

用法（语义与旧版一致）：
  python scripts/audit_sync.py                 # 仅报告差异（不改数据）
  python scripts/audit_sync.py --fix           # 对账 + 幂等补传（并写回 feishu_node_token）
  python scripts/audit_sync.py --only PATH     # 只处理单篇（测试/定点补传）
  python scripts/audit_sync.py --obs-root X    # 覆盖 vault 根
也可被监控脚本（audit_sync_watchdog.py）周期调用（其仅传 --fix，完全兼容）。
"""
import os
import re
import sys
import argparse

# 笔记前导 YAML frontmatter 块（---...---），推送飞书时整体剥掉，只留正文。
_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n?", re.S | re.U)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

from articles.feishu import FeishuOutput, _sanitize_title  # noqa: E402

# 跳过的中间产物 / 隐藏目录
SKIP_FILE_PREFIXES = ("_summary_", "_raw_", ".", "~")
SKIP_DIRS = {"notes", ".workbuddy", ".git", "__pycache__", ".obsidian", ".trash"}

# 从 source_url 提取飞书文档 token（docx/<tok> 或 wiki/<tok> 等）
_FEISHU_URL_RE = re.compile(r"(?:docx|wiki|sheet|bitable)[/_]([a-zA-Z0-9_]+)")


def extract_feishu_token_from_url(url: str) -> str:
    """从飞书文档链接提取 node/document token；非飞书链接返回 ''。"""
    if not url:
        return ""
    m = _FEISHU_URL_RE.search(url)
    return m.group(1) if m else ""


def parse_frontmatter(path: str) -> dict:
    """解析笔记前导 --- 块为字典（值去包围引号）。无 frontmatter 返回 {}。"""
    try:
        with open(path, encoding="utf-8") as f:
            txt = f.read()
    except Exception:
        return {}
    if not txt.startswith("---"):
        return {}
    end = txt.find("\n---", 3)
    if end < 0:
        return {}
    fm = {}
    for line in txt[3:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm


def write_feishu_node_token(path: str, token: str) -> bool:
    """在 frontmatter 插入/更新 `feishu_node_token: <token>`。无 frontmatter 则新建块。"""
    try:
        with open(path, encoding="utf-8") as f:
            txt = f.read()
    except Exception:
        return False
    line = f"feishu_node_token: {token}"
    if txt.startswith("---"):
        end = txt.find("\n---", 3)
        if end < 0:
            txt = "---\n" + line + "\n---\n" + txt
        else:
            head = txt[:end]
            if re.search(r"^feishu_node_token\s*:", head, re.M):
                head = re.sub(r"^feishu_node_token\s*:.*$", line, head, flags=re.M)
            else:
                head = head + "\n" + line
            txt = head + txt[end:]
    else:
        txt = "---\n" + line + "\n---\n\n" + txt
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(txt)
        return True
    except Exception:
        return False


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
        if title.startswith("."):
            continue
        rel = f"{prefix}/{title}" if prefix else title
        sub = feishu.list_children(tok)
        if sub:
            leaves.update(collect_feishu(feishu, tok, rel))
        else:
            leaves[rel] = tok
    return leaves


def classify_missing(obs_notes: dict, fei_keys: set, fei_tokens: set) -> list:
    """返回 [(key, obs_path), ...] 飞书缺的笔记。

    身份命中（任一即视为已在飞书、跳过）：
      1. frontmatter feishu_node_token ∈ 飞书节点集
      2. frontmatter source_url 提取出的飞书 token ∈ 飞书节点集
      3. 规范化 key ∈ 飞书 key 集
    """
    missing = []
    for key, path in obs_notes.items():
        fm = parse_frontmatter(path)
        # 1) feishu_node_token（最强，改名/挪位都认）
        tn = (fm.get("feishu_node_token") or "").strip()
        if tn and tn in fei_tokens:
            continue
        # 2) source_url（覆盖 923 镜像篇，结构无关）
        su = (fm.get("source_url") or "").strip()
        if su:
            tok = extract_feishu_token_from_url(su)
            if tok and tok in fei_tokens:
                continue
        # 3) 路径+标题（兜底）
        if key in fei_keys:
            continue
        missing.append((key, path))
    return missing


def classify_only(feishu: FeishuOutput, obs_path: str, root: str) -> list:
    """单篇模式(--only)的轻量缺飞书判定：不扫整树(963 节点)，只按本地身份 + 目标文件夹查重。

    返回 [(key, obs_path)] 若该篇需补传，或 [] 若已存在/已同步。
    判定优先级与 classify_missing 一致：feishu_node_token > source_url > 路径+标题，
    但前两项是本地 frontmatter 判定（无需打飞书），第三项只 list 目标父文件夹（O(1) 非 O(整树)）。
    """
    fm = parse_frontmatter(obs_path)
    # 1) 已有 feishu_node_token → 视为已同步（本地身份，无需扫飞书）
    tn = (fm.get("feishu_node_token") or "").strip()
    if tn:
        return []
    # 2) 飞书来源(source_url) → 视为已同步（本就来自飞书，不重复建）
    su = (fm.get("source_url") or "").strip()
    if su and extract_feishu_token_from_url(su):
        return []
    # 3) 解析目标文件夹(只读 list)，查同名标题是否存在
    rel = os.path.relpath(obs_path, root).replace(os.sep, "/")
    key = _obs_key(rel)
    dirs, base = feishu._split_subdir(key + ".md")
    parent = feishu.ensure_folder_path(dirs) if dirs else feishu.ensure_inbox_node()
    if not parent:
        return [(key, obs_path)]
    leaf_title = _sanitize_title(os.path.splitext(base)[0])
    if feishu._find_child_node(parent, leaf_title):
        return []  # 目标文件夹已存在同名 → 跳过
    return [(key, obs_path)]


def push_missing(feishu: FeishuOutput, missing: list) -> tuple:
    """missing: [(key, obs_path), ...]。逐条幂等补传（已存在则跳过），成功写回 feishu_node_token。"""
    created = skipped = failed = token_written = 0
    for key, obs_path in missing:
        fn = key + ".md"  # save 内部按 '/' 拆子目录、对末段再 sanitize（幂等）
        dirs, base = feishu._split_subdir(fn)
        parent = feishu.ensure_folder_path(dirs) if dirs else feishu.ensure_inbox_node()
        leaf_title = _sanitize_title(os.path.splitext(base)[0])
        if not parent:
            print(f"  ❌ 容器节点建不出，跳过：{key[:60]}")
            failed += 1
            continue
        # 幂等：父节点下已存在同名 → 跳过（保留原节点，写回其 token 以便后续精确匹配）
        exists = feishu._find_child_node(parent, leaf_title)
        if exists:
            print(f"  ✓ 飞书已存在，跳过：{key[:60]}")
            skipped += 1
            etok = exists.get("node_token", "")
            if etok and write_feishu_node_token(obs_path, etok):
                token_written += 1
            continue
        with open(obs_path, "r", encoding="utf-8") as f:
            content = f.read()
        # 推送飞书时剥掉 frontmatter（用户确认 2026-09-05：飞书文档体不应含 YAML 元数据）；
        # 本地笔记文件不变（feishu_node_token 写回仍作用本地 frontmatter）。
        push_body = _FRONTMATTER_RE.sub("", content, count=1).lstrip("\n")
        if feishu.save(push_body, fn, parent_token=parent):
            print(f"  ✅ 已补传飞书：{key[:60]}")
            created += 1
            ntok = ""
            if feishu._last_created:
                ntok = feishu._last_created.get("node_token", "")
            if ntok and write_feishu_node_token(obs_path, ntok):
                token_written += 1
        else:
            print(f"  ❌ 补传失败：{key[:60]}")
            failed += 1
    return created, skipped, failed, token_written


def main():
    p = argparse.ArgumentParser(
        description="Obsidian vault → 飞书 对账/补传（薄壳，逻辑在 vault_lifecycle._run_push）")
    p.add_argument("--fix", action="store_true",
                   help="缺飞书的笔记自动幂等补传(并写回 token)")
    p.add_argument("--only", default="", help="只处理单篇笔记(路径)，用于测试/定点补传")
    p.add_argument("--obs-root", dest="obs_root", default="",
                   help="Obsidian 成品根（覆盖 OBSIDIAN_VAULT_PATH）")
    p.add_argument("--feishu-node", dest="feishu_node", default="",
                   help="[废弃] 飞书根节点参数已不再使用")
    args = p.parse_args()
    if args.feishu_node:
        print("[废弃] --feishu-node 已废弃：飞书根节点统一取 FEISHU_WIKI_PARENT_NODE 配置。")
    import vault_lifecycle as vl  # 延迟导入：允许测试以 fake 拦截
    res = vl._run_push(apply=args.fix, vault=args.obs_root or "",
                       only=args.only or "")
    if res is None:
        print("❌ 对账补传未执行（门禁未过，见上方日志）。")
        sys.exit(1)
    if not args.fix and res.get("missing", 0) > 0:
        print(f"⚠️ 飞书缺 {res['missing']} 篇（仅报告模式）。")
        sys.exit(1)


if __name__ == "__main__":
    main()
