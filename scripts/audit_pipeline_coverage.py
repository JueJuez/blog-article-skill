# -*- coding: utf-8 -*-
"""管线文档覆盖自检：扫代码里的 CLI 入口与公共函数，列出 docs/PIPELINES.md 没登记的。

目的：防止「入口文档」与真实代码漂移——下一个 Agent 找不到现成能力时就会自己新造一个，
本项目已因此出现过两份并行落盘实现（见 PIPELINES §3 历史教训）。

用法：
    python scripts/audit_pipeline_coverage.py              # 只看未登记的 CLI 入口
    python scripts/audit_pipeline_coverage.py --funcs       # 额外列出各模块公共函数
    python scripts/audit_pipeline_coverage.py --all         # 连已登记的也列（对照用）

判定口径：脚本名/函数名是否出现在 `docs/PIPELINES.md` 文本里。
命中不等于「文档写得好」，只等于「至少被提到过」——漏登记会体现在这里，登记了但描述不对靠人看。
"""
import argparse
import ast
import os
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "PIPELINES.md"
SKIP_DIRS = {"_archive", "_tmp", "tests", "__pycache__", ".git", "node_modules",
             "_scraped", "notes", "transcripts", "_reports", "_meta"}
SCAN_DIRS = ["scripts", "tools", "articles", "videos", "monitors", "shared", "prompts"]


def collect():
    cli, funcs = [], {}
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if any(s in p.parts for s in SKIP_DIRS):
                continue
            rel = p.relative_to(ROOT).as_posix()
            try:
                src = p.read_text(encoding="utf-8")
                tree = ast.parse(src)
            except Exception:
                continue
            if "__main__" in src:
                cli.append((rel, ("add_argument" in src or "argparse" in src),
                            (ast.get_docstring(tree) or "").strip().split("\n")[0][:72]))
            names = []
            for n in tree.body:
                if isinstance(n, ast.FunctionDef) and not n.name.startswith("_"):
                    names.append(n.name)
                elif isinstance(n, ast.ClassDef):
                    for m in n.body:
                        if isinstance(m, ast.FunctionDef) and not m.name.startswith("_"):
                            names.append(f"{n.name}.{m.name}")
            if names:
                funcs[rel] = names
    return cli, funcs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--funcs", action="store_true", help="列出各模块公共函数")
    ap.add_argument("--all", action="store_true", help="连已登记的也列")
    args = ap.parse_args()

    if not DOC.exists():
        print(f"缺少 {DOC}")
        return 2
    doc = DOC.read_text(encoding="utf-8")
    doc_files = set(re.findall(r"[A-Za-z0-9_]+\.py", doc))
    # 文档里函数可能写成 `mod.func_name`（表格里省略括号），故取点号最后一段再匹配
    doc_funcs = {m.split(".")[-1] for m in re.findall(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+", doc)}
    doc_funcs |= set(re.findall(r"\b([a-z_][a-z0-9_]{3,})\(", doc))

    cli, funcs = collect()
    miss = [(r, a, d) for r, a, d in cli if os.path.basename(r) not in doc_files]
    print(f"CLI 入口 {len(cli)} 个，PIPELINES.md 未登记 {len(miss)} 个")
    for rel, argp, d1 in sorted(miss):
        print(f"  [未登记] {rel}  argparse={argp}  {d1}")
    if args.all:
        for rel, argp, d1 in sorted(cli):
            if os.path.basename(rel) in doc_files:
                print(f"  [已登记] {rel}")

    if args.funcs:
        total = sum(len(v) for v in funcs.values())
        print(f"\n公共函数 {total} 个，分布在 {len(funcs)} 个模块（未登记的标 *）")
        for rel, names in sorted(funcs.items()):
            marked = [n if n in doc_funcs else n + "*" for n in names]
            print(f"  {rel}: {', '.join(marked)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
