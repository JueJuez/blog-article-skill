# -*- coding: utf-8 -*-
"""管线门禁：拦截「新增未登记的管线入口」与「新增重复函数实现」的单一真源。

判定逻辑只在本文维护一份，由测试强制生效：
- `tests/test_pipeline_gate.py` → 导入本模块的 `check()` 断言返回空。
  配合项目纪律「改动后必跑全量测试」，这条测试跑绿即门禁通过。
  （不依赖 git hook / 任何 git 配置——纯项目内机制，换机克隆后零接线。）

两条硬门禁（`check()` 返回非空 = 拦截）：
  1. 存在未登记到 `docs/PIPELINES.md` 的 CLI 入口（复用 audit_pipeline_coverage）
  2. 出现「完全相同」的函数实现组，且该文件组不在 `/ALLOWED_DUP` 基线内（= 新增重复）

软提示（只打印、不拦截）：近似重复、同名跨文件函数——前者相似度噪声大，
后者可能是多态/对称设计（PIPELINES §10.1 已注明需人工判）。

用法：
    python scripts/pipeline_gate.py      # 命令行直跑（诊断用）
退出码：0 = 通过；1 = 拦截。
"""
import os
import pathlib
import re
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "PIPELINES.md"
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import audit_pipeline_coverage as cov  # noqa: E402
import audit_duplicate_funcs as dup  # noqa: E402

MIN_EXACT_LINES = 6  # 与 audit_duplicate_funcs 默认对齐：少于该行数的重复不拦截

# 「完全相同实现」的已知文件组基线。
# 语义：这些组的重复是「已接受 / 已冻结」的存量，放行；门禁只堵「新增重复」——
# 一旦出现不在基线的文件组（新函数 / 新文件复制了现成实现）即拦截。
# 变更纪律：真要复用现成实现时，先把旧重复清理/合并；确需接受的，才慎重追加到本
# 基线并写明理由（这正是有意的「摩擦」，避免无记录地新增重复）。
ALLOWED_DUP = {
    # lark-cli 历史迁移脚本（PIPELINES §11 已标「已冻结」）：_find_lark_cli / run_cli / …
    frozenset({"scripts/delete_duplicates.py", "scripts/find_duplicates.py",
               "scripts/rename_list.py"}),
    frozenset({"scripts/find_duplicates.py", "scripts/list_overviews.py",
               "scripts/rename_list.py"}),
    # scys 两条近亲落盘脚本的小工具重复 extract_tags_and_strip：当前接受，未来应下沉去重
    frozenset({"scripts/land_scys_batch.py", "scripts/land_scys_by_key.py"}),
}


def _unregistered_cli():
    """PIPELINES.md 未登记的 CLI 入口 [(rel, argparse, docline)]。逻辑对齐 cov.main。"""
    cli, _ = cov.collect()
    doc = DOC.read_text(encoding="utf-8")
    doc_files = set(re.findall(r"[A-Za-z0-9_]+\.py", doc))
    return [(r, a, d) for r, a, d in cli if os.path.basename(r) not in doc_files]


def _exact_dup_groups():
    """{归一化源码: [文件路径...]}，仅保留出现 >1 次的组。逻辑对齐 dup.main。"""
    by_src = defaultdict(list)
    for rel, _qn, _ln, nl, src in dup.collect():
        if nl >= MIN_EXACT_LINES:
            by_src[src].append(rel)
    return {src: locs for src, locs in by_src.items() if len(locs) > 1}


def check():
    """返回问题清单（空 = 通过）。不进文件 / 不打 stdout 的纯判定，便于单测。"""
    problems = []

    miss = _unregistered_cli()
    if miss:
        problems.append(f"[门禁1] {len(miss)} 个 CLI 入口未登记到 docs/PIPELINES.md：")
        for r, a, d in sorted(miss):
            problems.append("    %s  argparse=%s  %s" % (r, a, d))

    new_dups = [locs for locs in _exact_dup_groups().values()
                if frozenset(locs) not in ALLOWED_DUP]
    if new_dups:
        problems.append("[门禁2] 发现基线外的完全重复实现（疑似新增重复）：")
        for locs in sorted(new_dups, key=len):
            problems.append("    " + "; ".join(sorted(locs)))

    return problems


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    problems = check()
    if not problems:
        print("管线门禁通过：无未登记 CLI、无新增完全重复实现。")
        return 0
    print("\n".join(problems))
    print("\n管线门禁拦截：请解决上述问题（新增/剩余重复后，可对照 "
          "scripts/pipeline_gate.py:ALLOWED_DUP 基线）。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())