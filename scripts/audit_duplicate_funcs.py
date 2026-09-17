# -*- coding: utf-8 -*-
"""代码重复自检：找出完全相同 / 近似重复的函数实现，以及跨文件同名函数。

用途：回答「这个函数是不是已经有现成的」「这两份实现能不能合并」。
与 `audit_pipeline_coverage.py` 互补——那个查「文档有没有登记入口」，这个查「代码里有没有重复实现」。

判定口径（人工复核前只看作线索）：
- 完全相同：归一化（去空行与缩进）后源码一致，且 >= `--min-lines` 行
- 近似重复：源码行数分桶后两两 difflib 比对，相似度 >= `--ratio`
- 同名函数：跨文件同名（可能是多态实现/对称设计，**不一定**是真重复）

用法：
    python scripts/audit_duplicate_funcs.py
    python scripts/audit_duplicate_funcs.py --min-lines 10 --ratio 0.9
"""
import argparse
import ast
import difflib
import pathlib
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP = {"_archive", "_tmp", "tests", "__pycache__", ".git", "node_modules",
        "_scraped", "notes", "transcripts", "_reports", "_meta"}
SCAN = ["scripts", "tools", "articles", "videos", "monitors", "shared", "prompts"]


def collect():
    """返回 [(rel, qualname, lineno, nlines, norm_src)]，含模块级函数与类方法。"""
    out = []
    for d in SCAN:
        for p in sorted((ROOT / d).rglob("*.py")):
            if any(s in p.parts for s in SKIP):
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            rel = p.relative_to(ROOT).as_posix()

            def walk(node, prefix=""):
                for n in node.body:
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        try:
                            body = ast.unparse(n)
                        except Exception:
                            body = ""
                        lines = [l.strip() for l in body.splitlines() if l.strip()]
                        out.append((rel, prefix + n.name, n.lineno, len(lines), "\n".join(lines)))
                    elif isinstance(n, ast.ClassDef):
                        walk(n, prefix + n.name + ".")
            walk(tree)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-lines", type=int, default=6, help="完全相同判定的最小行数")
    ap.add_argument("--ratio", type=float, default=0.82, help="近似重复阈值")
    args = ap.parse_args()

    funcs = collect()
    print(f"函数总数 {len(funcs)}")

    exact = defaultdict(list)
    for rel, qn, ln, nl, src in funcs:
        if nl >= args.min_lines:
            exact[src].append(f"{rel}:{qn}@{ln}")
    dups = {k: v for k, v in exact.items() if len(v) > 1}
    print(f"\n=== 完全相同实现（>={args.min_lines} 行，{len(dups)} 组）===")
    for k, v in sorted(dups.items(), key=lambda x: -len(x[1])):
        print(f"  x{len(v)}  {'; '.join(v)}")

    print(f"\n=== 近似重复（相似度 >={args.ratio}，>=8 行）===")
    buckets = defaultdict(list)
    for f in funcs:
        if f[3] >= 8:
            buckets[f[3] // 4].append(f)
    seen, groups = set(), 0
    for items in buckets.values():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, c = items[i], items[j]
                if a[0] == c[0] or (a[1], c[1]) in seen:
                    continue
                seen.add((a[1], c[1]))
                r = difflib.SequenceMatcher(None, a[4], c[4]).ratio()
                if r >= args.ratio:
                    groups += 1
                    print(f"  {r:.2f}  {a[0]}:{a[1]}@{a[2]}  <->  {c[0]}:{c[1]}@{c[2]}")
    print(f"近似重复组数: {groups}")

    by_name = defaultdict(list)
    for rel, qn, ln, _, _ in funcs:
        base = qn.split(".")[-1]
        if not base.startswith("_"):
            by_name[base].append(f"{rel}@{ln}")
    print("\n=== 同名公共函数（跨文件；可能是多态/对称设计，需人工判）===")
    n = 0
    for name, locs in sorted(by_name.items()):
        if len({l.split("@")[0] for l in locs}) > 1:
            n += 1
            print(f"  {name}: {', '.join(locs)}")
    print(f"同名跨文件函数: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
