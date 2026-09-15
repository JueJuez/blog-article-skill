# -*- coding: utf-8 -*-
"""登记表与副本确定性修复（2026-09-16）：
1) 单副本组：identical → 副本归档（不删除）；orphan（主文件缺失）→ 副本改回主名；
2) 登记表悬空中「路径漂移」条目：按 basename 找到实际位置后回写 filename/folder。
幂等：重复执行无副作用（找不到待处理项即跳过）。执行前自动备份登记表。
"""
import json
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REG = os.path.join(ROOT, "notes", "_meta", "summary_registry.json")
VAULT = os.getenv("OBSIDIAN_VAULT_PATH") or r"D:\Code\Obsidian\obsidian-path\AI 总结笔记"
ARCH = os.path.join(ROOT, "notes", "_archive", "_overwrite_copies_20260916")

shutil.copy2(REG, REG + ".bak_copies_20260916")
print("登记表已备份")

byname = {}
for root, _, files in os.walk(VAULT):
    for f in files:
        if f.endswith(".md"):
            byname.setdefault(f, os.path.join(root, f))

reg = json.load(open(REG, encoding="utf-8"))
reg_by_fn = {}
for k, v in reg.items():
    if isinstance(v, dict) and v.get("filename"):
        reg_by_fn[v["filename"].replace(os.sep, "/")] = k

# ---------- 1) 单副本组（orphan / identical） ----------
copy_re = re.compile(r"^(.*)-(\d+)\.md$")
pairs = {}
for root, _, files in os.walk(VAULT):
    for f in files:
        m = copy_re.match(f)
        if not m:
            continue
        base_path = os.path.join(root, m.group(1) + ".md")
        pairs.setdefault(base_path, []).append(os.path.join(root, f))

os.makedirs(ARCH, exist_ok=True)
actions = []
for base_path, copies in pairs.items():
    if len(copies) != 1:          # 多副本组不自动处理，避免误伤
        continue
    copy = copies[0]
    if not os.path.exists(base_path):
        actions.append(("orphan", copy, base_path))
    elif open(base_path, encoding="utf-8").read() == open(copy, encoding="utf-8").read():
        actions.append(("identical", copy, base_path))

n_orph = sum(1 for a in actions if a[0] == "orphan")
n_ident = sum(1 for a in actions if a[0] == "identical")
print(f"待处理 {len(actions)} 组（orphan={n_orph}, identical={n_ident}）")

for kind, copy, base_path in actions:
    cf = os.path.relpath(copy, VAULT).replace(os.sep, "/")
    bf = os.path.relpath(base_path, VAULT).replace(os.sep, "/")
    if kind == "identical":
        shutil.move(copy, os.path.join(ARCH, os.path.basename(copy)))
    else:
        shutil.move(copy, base_path)
    for fn in (cf, bf):
        k = reg_by_fn.get(fn)
        if k is not None:
            reg[k]["filename"] = bf
            if reg[k].get("folder"):
                reg[k]["folder"] = os.path.dirname(bf)
            reg[k].pop("obsidian_link", None)

# ---------- 2) 路径漂移回写 ----------
fixed = 0
for k, v in reg.items():
    if not isinstance(v, dict):
        continue
    fn = v.get("filename") or ""
    if not fn:
        continue
    if os.path.exists(os.path.join(VAULT, fn.replace("/", os.sep))):
        continue
    real = byname.get(os.path.basename(fn))
    if real:
        rel = os.path.relpath(real, VAULT).replace(os.sep, "/")
        v["filename"] = rel
        if v.get("folder"):
            v["folder"] = os.path.dirname(rel)
        fixed += 1
print(f"路径漂移修正：{fixed} 条")

json.dump(reg, open(REG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("登记表已回写")
