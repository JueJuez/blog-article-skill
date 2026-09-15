"""scripts/audit_overwrite_copies.py — `-N` 重名副本审计 / 清理（默认只读）

## 背景（DECISION-20260915 阶段5）

`articles/main.py` 的文件名冲突处理长期是**无条件「禁止覆盖」**：目标名已存在就改名
`X-1.md`。于是每次 `force` 修订都会留一个副本，**并且登记表 filename 会指向副本**；
若事后手工合并删除副本，登记表就指向一个已删文件（2026-09-15 本批 15 条中招，已单独修复）。
全库历史累积了一批这种副本。

## 用法

    python scripts/audit_overwrite_copies.py                 # 只出清单（默认，零改动）
    python scripts/audit_overwrite_copies.py --report r.md   # 同时写 Markdown 清单
    python scripts/audit_overwrite_copies.py --apply         # 执行清理（需显式指定）

`--apply` 的行为（仅在人工核对清单后使用）：
  - `identical`（副本与主文件内容一致）→ 删副本；若登记表指向副本 → 改回主文件
  - `orphan`（主文件不存在）→ 把副本改回主名（正文保留）
  - `differs`（内容不同）→ **不动**，只报告，等人工决定保留哪版

删/改之前统一备份到 `notes/_archive/_overwrite_copies_<时间戳>/`（保留全部原件）。
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

VAULT = os.getenv("OBSIDIAN_VAULT_PATH") or r"D:\Code\Obsidian\obsidian-path\AI 总结笔记"
REGISTRY = os.path.join(ROOT, "notes", "_meta", "summary_registry.json")
COPY_RE = re.compile(r"^(?P<base>.+)-(?P<n>\d+)\.md$")


def _norm(s: str) -> str:
    return re.sub(r"\s", "", s or "")


def scan() -> list:
    """扫描 vault 里所有 `*-N.md`，与主文件对比分类。"""
    reg = json.load(open(REGISTRY, encoding="utf-8"))
    by_filename = {}
    for v in reg.values():
        if isinstance(v, dict) and v.get("filename"):
            by_filename[v["filename"].replace("\\", "/")] = v.get("source_url", "")

    out = []
    for dirpath, _dirs, files in os.walk(VAULT):
        for f in files:
            m = COPY_RE.match(f)
            if not m or not f.endswith(".md"):
                continue
            copy_path = os.path.join(dirpath, f)
            base_path = os.path.join(dirpath, m.group("base") + ".md")
            rel_copy = os.path.relpath(copy_path, VAULT).replace(os.sep, "/")
            rel_base = os.path.relpath(base_path, VAULT).replace(os.sep, "/")
            rec = {
                "copy": rel_copy, "base": rel_base,
                "copy_bytes": os.path.getsize(copy_path),
                "copy_mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(copy_path))),
                "reg_points_to": ("copy" if rel_copy in by_filename
                                  else "base" if rel_base in by_filename else "neither"),
                "base_exists": os.path.exists(base_path),
            }
            if rec["base_exists"]:
                a = _norm(open(copy_path, encoding="utf-8").read())
                b = _norm(open(base_path, encoding="utf-8").read())
                rec["base_bytes"] = os.path.getsize(base_path)
                rec["base_mtime"] = time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(base_path)))
                if a == b:
                    rec["kind"] = "identical"
                else:
                    rec["kind"] = "differs"
                    rec["copy_len"] = len(a)
                    rec["base_len"] = len(b)
                    only_base = len(b) - len(a)
                    rec["len_delta"] = only_base
            else:
                rec["kind"] = "orphan"
            out.append(rec)
    return out


def write_report(path: str, rows: list) -> None:
    kinds = collections.Counter(r["kind"] for r in rows)
    L = []
    A = L.append
    A("# `-N` 重名副本审计清单")
    A("")
    A(f"- 生成：`python scripts/audit_overwrite_copies.py`（默认只读，未改动任何文件）")
    A(f"- 合计 **{len(rows)}** 个副本：" + "、".join(f"{k} {v}" for k, v in kinds.most_common()))
    A("- **默认禁止覆盖**的改名逻辑所致；`--force` 修订正式入口已改为「覆盖同名」"
      "（DECISION-20260915 阶段3），新增副本从此不再产生。")
    A("")
    A("| 类别 | 含义 | 建议动作 |")
    A("|---|---|---|")
    A("| `identical` | 副本与主文件内容完全一致 | 删副本；若登记表指向副本则改回主文件 |")
    A("| `orphan` | 主文件不存在 | 副本改回主名（正文保留） |")
    A("| `differs` | 两版内容不同 | **人工决定保留哪版**，脚本不动 |")
    A("")
    A(f"## 一、`differs`（需人工裁决 {kinds.get('differs', 0)} 条）")
    A("")
    if kinds.get("differs"):
        A("| 主文件 | 副本 | 登记表指向 | 主文件字数 | 副本字数 | 主文件时间 | 副本时间 |")
        A("|---|---|---|---|---|---|---|")
        for r in rows:
            if r["kind"] != "differs":
                continue
            A(f"| `{os.path.basename(r['base'])}` | `{os.path.basename(r['copy'])}` | {r['reg_points_to']} "
              f"| {r.get('base_len', '-')} | {r.get('copy_len', '-')} | {r.get('base_mtime', '-')} "
              f"| {r['copy_mtime']} |")
    else:
        A("无。")
    A("")
    A(f"## 二、`identical`（可直接删副本 {kinds.get('identical', 0)} 条）")
    A("")
    A("| 目录 | 副本 | 登记表指向 |")
    A("|---|---|---|")
    for r in rows:
        if r["kind"] == "identical":
            A(f"| `{os.path.dirname(r['copy'])}` | `{os.path.basename(r['copy'])}` | {r['reg_points_to']} |")
    A("")
    A(f"## 三、`orphan`（主文件缺失，需改回主名 {kinds.get('orphan', 0)} 条）")
    A("")
    A("| 目录 | 副本 | 登记表指向 |")
    A("|---|---|---|")
    for r in rows:
        if r["kind"] == "orphan":
            A(f"| `{os.path.dirname(r['copy'])}` | `{os.path.basename(r['copy'])}` | {r['reg_points_to']} |")
    A("")
    A("## 四、登记表指向副本的条目（**必须修正**）")
    A("")
    bad = [r for r in rows if r["reg_points_to"] == "copy"]
    if bad:
        A(f"共 **{len(bad)}** 条：登记表 filename 指向 `-N` 副本，回链会落到副本而非正式笔记。")
        A("")
        A("| 副本 | 类别 |")
        A("|---|---|")
        for r in bad:
            A(f"| `{r['copy']}` | {r['kind']} |")
    else:
        A("无（登记表均指向主文件）。")
    A("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def apply(rows: list, backup_dir: str) -> dict:
    """按类别执行清理；动作前把受影响文件备份到 backup_dir。"""
    os.makedirs(backup_dir, exist_ok=True)
    reg_path = REGISTRY
    reg = json.load(open(reg_path, encoding="utf-8"))
    by_filename = {v["filename"].replace("\\", "/"): k
                   for k, v in reg.items()
                   if isinstance(v, dict) and v.get("filename")}
    stats = collections.Counter()
    for r in rows:
        copy_path = os.path.join(VAULT, r["copy"].replace("/", os.sep))
        base_path = os.path.join(VAULT, r["base"].replace("/", os.sep))
        # 备份（保留全部原件，避免不可逆）
        dst = os.path.join(backup_dir, r["copy"].replace("/", "__"))
        shutil.copy2(copy_path, dst)
        if r["kind"] == "identical":
            os.remove(copy_path)
            stats["deleted"] += 1
            if r["reg_points_to"] == "copy":
                key = by_filename.get(r["copy"])
                if key:
                    reg[key]["filename"] = r["base"]
                    reg[key]["folder"] = os.path.dirname(r["base"]).replace(os.sep, "/")
                    stats["registry_fixed"] += 1
        elif r["kind"] == "orphan":
            shutil.move(copy_path, base_path)
            stats["renamed"] += 1
            if r["reg_points_to"] == "copy":
                key = by_filename.get(r["copy"])
                if key:
                    reg[key]["filename"] = r["base"]
                    reg[key]["folder"] = os.path.dirname(r["base"]).replace(os.sep, "/")
                    stats["registry_fixed"] += 1
        else:
            stats["skipped_differs"] += 1
    json.dump(reg, open(reg_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return dict(stats)


def _score_pair(r: dict, note_type: str) -> dict:
    """用 content_signals 给同一内容的两版打分，判断该留哪版。

    第一原则是**内容完整**（DECISION-20260915），因此排序键依次是：
      ① 结构缺失更少 → ② 破碎形态信号更少 → ③ 字数更多（完整性代理指标）
    前两键相同时才看字数，避免退化成「只看谁更长」。
    """
    from prompts import content_signals as CS
    out = {}
    for tag in ("base", "copy"):
        path = os.path.join(VAULT, r[tag].replace("/", os.sep))
        if not os.path.exists(path):
            out[tag] = None
            continue
        text = open(path, encoding="utf-8").read()
        body = CS.strip_note(text)
        out[tag] = {
            "words": CS.count_words(body),
            "missing": len(CS.structure_missing(body, note_type)["missing"]),
            "form": len(CS.form_signals(body)["signals"]),
        }
    a, b = out.get("base"), out.get("copy")
    if not a or not b:
        return {"verdict": "manual", "reason": "有一版缺失，需人工看", "base": a, "copy": b}
    ka = (a["missing"], a["form"], -a["words"])
    kb = (b["missing"], b["form"], -b["words"])
    if ka < kb:
        keep, why = "base", "结构更全/信号更少/更长（按序比较）"
    elif kb < ka:
        keep, why = "copy", "结构更全/信号更少/更长（按序比较）"
    else:
        keep, why = "either", "两版判定完全一致，任选"
    delta = abs(a["words"] - b["words"]) / max(1, max(a["words"], b["words"]))
    return {"verdict": keep, "reason": why, "base": a, "copy": b,
            "diff_ratio": round(delta, 3), "close": delta < 0.1}


def write_verdict_report(path: str, rows: list) -> None:
    reg = json.load(open(REGISTRY, encoding="utf-8"))
    nt_by_filename = {}
    for v in reg.values():
        if isinstance(v, dict) and v.get("filename"):
            nt_by_filename[v["filename"].replace("\\", "/")] = v.get("note_type", "")
    pairs = [r for r in rows if r["kind"] == "differs"]
    L = []
    A = L.append
    A("# `differs` 副本裁决建议（**只读生成，未改动任何文件**）")
    A("")
    A("同一份源材料被总结过两次（引擎旧「禁止覆盖」逻辑留下 `-N` 副本），两版内容不同，"
      "需要「留一版、归档另一版」。裁决按 DECISION-20260915 的**内容完整第一**原则排序："
      "① 必备模块缺失更少 → ② 破碎形态信号更少 → ③ 字数更多。")
    A("")
    stats = collections.Counter()
    A("| 目录 | 主文件字数 | 副本字数 | 主/副本缺模块 | 建议保留 | 两版字数差 |")
    A("|---|---|---|---|---|---|")
    detail = []
    for r in pairs:
        nt = nt_by_filename.get(r["base"], "")
        v = _score_pair(r, nt)
        if v["verdict"] == "base":
            stats["keep_base"] += 1
            keep_s = "**主文件**"
        elif v["verdict"] == "copy":
            stats["keep_copy"] += 1
            keep_s = "副本"
        else:
            stats["either"] += 1
            keep_s = "任选"
        b, c = v.get("base") or {}, v.get("copy") or {}
        A(f"| `{os.path.dirname(r['copy'])}` | {b.get('words', '-')} | {c.get('words', '-')} "
          f"| {b.get('missing', '-')}/{c.get('missing', '-')} | {keep_s} "
          f"| {v.get('diff_ratio', '-')} |")
        detail.append((r, v, nt))
    A("")
    A("## 汇总")
    A("")
    A(f"- 建议保留**主文件**：{stats['keep_base']} 组")
    A(f"- 建议保留**副本**：{stats['keep_copy']} 组")
    A(f"- **两版判定一致、任选**：{stats['either']} 组")
    close = [d for d in detail if d[1].get("close")]
    A(f"- 其中**两版字数差 < 10%**（差异很小，留哪版影响不大）：{len(close)} 组")
    A("")
    A("## 需要特别留意的组（两版差异大 或 建议留副本）")
    A("")
    for r, v, nt in detail:
        if v["verdict"] == "copy" or (v.get("diff_ratio", 0) > 0.4):
            A(f"- `{os.path.basename(r['base'])}`：主 {v['base']['words']} 字 / 副本 "
              f"{v['copy']['words']} 字 → **建议保留 {v['verdict']}**（{v['reason']}）")
    A("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="")
    ap.add_argument("--verdict", default="", help="生成 differs 组的「留哪版」建议清单（只读）")
    ap.add_argument("--apply", action="store_true", help="执行清理（默认只读）")
    args = ap.parse_args()

    rows = scan()
    kinds = collections.Counter(r["kind"] for r in rows)
    print(f"扫描完成：{len(rows)} 个 `-N` 副本")
    for k, v in kinds.most_common():
        print(f"  {k:<10} {v}")
    bad = sum(1 for r in rows if r["reg_points_to"] == "copy")
    print(f"  登记表指向副本的：{bad}")
    for r in rows[:12]:
        print(f"   - [{r['kind']}] {r['copy']}  (base_exists={r['base_exists']}, reg→{r['reg_points_to']})")

    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        write_report(args.report, rows)
        print(f"清单已写入 {args.report}")

    if args.verdict:
        os.makedirs(os.path.dirname(os.path.abspath(args.verdict)), exist_ok=True)
        write_verdict_report(args.verdict, rows)
        print(f"裁决建议已写入 {args.verdict}")
        pairs = [r for r in rows if r["kind"] == "differs"]
        reg = json.load(open(REGISTRY, encoding="utf-8"))
        nt_by_filename = {v["filename"].replace("\\", "/"): v.get("note_type", "")
                          for v in reg.values()
                          if isinstance(v, dict) and v.get("filename")}
        st = collections.Counter()
        for r in pairs:
            v = _score_pair(r, nt_by_filename.get(r["base"], ""))
            st[v["verdict"]] += 1
        print(f"  differs {len(pairs)} 组 → 建议留主文件 {st['base']}，留副本 {st['copy']}，"
              f"任选 {st['either']}，需人工 {st['manual']}")

    if args.apply:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup = os.path.join(ROOT, "notes", "_archive", f"_overwrite_copies_{stamp}")
        stats = apply(rows, backup)
        print(f"清理完成：{stats}；原件已备份到 {backup}")
    else:
        print("（只读模式，未改动任何文件；如需执行请加 --apply）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
