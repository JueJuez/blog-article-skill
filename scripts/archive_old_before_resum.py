# -*- coding: utf-8 -*-
"""阶段C 重跑前：把批次内旧笔记归档（可回溯），再交由 --force 覆盖落盘。

用法：python scripts/archive_old_before_resum.py notes/_meta/resum_batches/batch_01.json
动作：把批次内每条的 note_path 复制到 notes/_archive/resum_old/<批次名>/（保留相对结构），不删不改原文件。
幂等：目标已存在时跳过。
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAULT = os.getenv("OBSIDIAN_VAULT_PATH") or r"D:\Code\Obsidian\obsidian-path\AI 总结笔记"


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: archive_old_before_resum.py <batch.json>")
        return 2
    batch_path = sys.argv[1]
    batch_name = os.path.splitext(os.path.basename(batch_path))[0]
    items = json.load(open(batch_path, encoding="utf-8"))
    out_root = os.path.join(ROOT, "notes", "_archive", "resum_old", batch_name)
    os.makedirs(out_root, exist_ok=True)
    n = 0
    for it in items:
        src = it.get("note_path") or ""
        if not src or not os.path.exists(src):
            continue
        rel = os.path.relpath(src, VAULT)
        dst = os.path.join(out_root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            continue
        shutil.copy2(src, dst)
        n += 1
    print(f"{batch_name}: 已归档旧版 {n}/{len(items)} -> {os.path.relpath(out_root, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
