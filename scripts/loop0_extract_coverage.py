# -*- coding: utf-8 -*-
"""Loop 0 · T15 提取覆盖率（A4 零成文成本的调参环，PLAN-20261018 §8 三层循环 / §12.3 #14）

**Loop 0 量什么**：30 篇评估源的「标准答案」就是 A1 的必留清单（299 条计分条目）。
把 T15 的提取输出逐条对这份清单算覆盖率 —— 只比提取、不成文，所以改提取 prompt 可以在这个环里
反复转，一次只花提取的钱。

**怎么算**：机械粗筛 + 模型裁可疑项（§12.3 #4 的取向）。本脚本只做**机械半**：

1. 把 T15 清单（含每条的原句片段）拼成一个「提取语料」；
2. 对每条 A1 必留条目算字符 bigram 覆盖率（清单条目与 A1 条目同源自同一文本，真覆盖会共享大量 bigram）；
3. 覆盖率 ≥ 阈值 → 机械判定「提取到了」；低于阈值 → **存疑**，交模型/子 Agent 裁（本脚本不下结论）。

覆盖不足的**原因**分两种，报告里分开列：
  - `extract_miss`：清单没提到 → 提取 prompt 要修的靶；
  - `note_miss`（A1 里已判未保留的条目同样被提取漏掉）→ 说明是提取环节的问题，而不是成文的。

用法：
    # 1) 先派子 Agent 按 _INSTRUCTION.md 产出 A01..A30 的清单到 --checklists
    python scripts/loop0_extract_coverage.py --emit-prompts
    # 2) 子 Agent 写完后算覆盖率
    python scripts/loop0_extract_coverage.py

产物：`<eval-dir>/loop0_report.md` + `<eval-dir>/loop0_suspects.json`。
只读生产数据；不写 vault、不改笔记。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_EVAL_DIR = os.path.join(ROOT, "notes", "_meta", "eval", "20261018-knowledge-layer")
DEFAULT_THRESHOLD = 0.45
_WS_RE = re.compile(r"\s")


def _norm(s: str) -> str:
    return _WS_RE.sub("", s or "")


def bigrams(text: str) -> set:
    t = _norm(text)
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else set()


def coverage(a1_text: str, corpus_bigrams: set) -> float:
    bg = bigrams(a1_text)
    if not bg:
        return 0.0
    return len(bg & corpus_bigrams) / len(bg)


# 共享连续片段的判定档位（由长到短）：A1 条目是「陈述+依据」的打包句，清单是原子条目，
# 光看 bigram 覆盖率会把「讲到了、只是拆开讲」误判成没提取（实测 A24 九个公式、A28 信号理论皆如此）。
# 共享 6 字以上连续片段是「同一句话出现过」的硬信号，作为粗筛的第二判据。
RUN_STEPS = (12, 10, 8, 6)


def shared_run(a1_text: str, item_texts: list) -> int:
    """返回 A1 条目与清单条目共享的最长连续片段档位（RUN_STEPS 之一，0＝无共享）。"""
    t = _norm(a1_text)
    for n in RUN_STEPS:
        if len(t) < n:
            continue
        grams = {t[i:i + n] for i in range(len(t) - n + 1)}
        for it in item_texts:
            s = _norm(it)
            if len(s) < n:
                continue
            if any(g in s for g in grams):
                return n
    return 0


def load_checklists(ck_dir: str) -> dict:
    """读 A01..A30 清单文件（agent 产物；整文件文本即可，解析交给 extract_checklist）。"""
    from prompts.extract_checklist import parse_checklist
    out = {}
    if not os.path.isdir(ck_dir):
        return out
    for name in sorted(os.listdir(ck_dir)):
        if not name.endswith(".md") or name.startswith("_"):
            continue
        with open(os.path.join(ck_dir, name), encoding="utf-8") as fh:
            text = fh.read()
        items = parse_checklist(text)
        out[name[:-3]] = {"raw": text, "items": items}
    return out


def emit_prompts(eval_rows: dict, ck_dir: str) -> str:
    """把版本化的提取指令 + 每篇任务书落盘，供编排方派子 Agent（避免 prompt 只活在会话里）。"""
    from prompts.extract_checklist import rules_text, rules_hash, BEGIN, END
    os.makedirs(ck_dir, exist_ok=True)
    instr = (
        "# Loop 0 · T15 提取任务（只提取，不成文）\n\n"
        f"清单指令版本 hash：`{rules_hash()}`\n\n"
        "## 你要做的\n"
        "1. 用 Read 读取指定的源文件（**只读源，不要读笔记**）。\n"
        "2. 严格按下面「提取清单指令」产出清单块。**Loop 0 只要清单，不要写笔记正文、不要写任何别的解释**。\n"
        f"3. 把 `{BEGIN}` ~ `{END}` 这一整块（含标记行）写入指定的输出文件，别的什么都不写。\n\n"
        "## 提取清单指令（版本化真源：`prompts/extract_checklist.rules_text()`）\n\n"
        + rules_text()
    )
    p = os.path.join(ck_dir, "_INSTRUCTION.md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(instr)
    for eid, row in sorted(eval_rows.items()):
        task = (f"# Loop 0 任务 {eid}\n\n"
                f"- 源文件（只读它）：`{row.get('source_path')}`\n"
                f"- 输出文件（只写这一块）：`{os.path.join(ck_dir, eid + '.md')}`\n\n"
                "严格按 `_INSTRUCTION.md` 执行：只产出 `<!-- EXTRACT_CHECKLIST -->` 块，不要写正文。\n")
        with open(os.path.join(ck_dir, eid + ".task.md"), "w", encoding="utf-8") as fh:
            fh.write(task)
    return instr


def main() -> int:
    ap = argparse.ArgumentParser(description="Loop 0 · T15 提取覆盖率（只读）")
    ap.add_argument("--eval-dir", default=DEFAULT_EVAL_DIR)
    ap.add_argument("--checklists", default="")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--run-min", type=int, default=6, help="共享连续片段档位（≥该值即判命中）")
    ap.add_argument("--out", default="")
    ap.add_argument("--suspects", default="")
    ap.add_argument("--emit-prompts", action="store_true")
    args = ap.parse_args()

    eval_dir = args.eval_dir
    ck_dir = args.checklists or os.path.join(eval_dir, "loop0_checklists")
    from shared.eval_set import load_rows, parse_form
    rows = load_rows(os.path.join(eval_dir, "eval_set.json"))

    if args.emit_prompts:
        emit_prompts(rows, ck_dir)
        print(f"已写指令与任务书 → {ck_dir}")
        return 0

    cks = load_checklists(ck_dir)
    if not cks:
        print(f"[ERR] 没找到清单文件：{ck_dir}（先 --emit-prompts 再派子 Agent）")
        return 2

    form_dir = os.path.join(eval_dir, "forms")
    per, suspects, missing_ck = [], [], []
    tot_items = tot_hit = 0
    for name in sorted(os.listdir(form_dir)):
        if not name.endswith(".md") or name.startswith("README"):
            continue
        eid = name[:-3]
        with open(os.path.join(form_dir, name), encoding="utf-8") as fh:
            parsed = parse_form(fh.read())
        items = parsed["items"]
        ck = cks.get(eid)
        if not ck or not ck["items"]:
            missing_ck.append(eid)
            continue
        # 提取语料：清单条目陈述 + 原句片段（原句是源的原样片段，共享 bigram 的信号最强）
        corpus = bigrams("".join(i.get("text", "") + i.get("quote", "") for i in ck["items"]))
        item_texts = [i.get("text", "") + i.get("quote", "") for i in ck["items"]]
        hit = 0
        for it in items:
            cov = coverage(it["text"], corpus)
            run = shared_run(it["text"], item_texts)
            if cov >= args.threshold or run >= args.run_min:
                hit += 1
            else:
                suspects.append({
                    "eval_id": eid, "a1_text": it["text"], "a1_kept_in_note": it["kept"],
                    "coverage": round(cov, 3), "shared_run": run,
                    "kind": "note_miss" if not it["kept"] else "extract_miss",
                })
        tot_items += len(items)
        tot_hit += hit
        per.append({"eval_id": eid, "a1_items": len(items), "extract_hit": hit,
                    "coverage": round(hit / len(items), 3) if items else None,
                    "ck_items": len(ck["items"]),
                    "bucket": (rows.get(eid) or {}).get("bucket", "")})

    rate = (tot_hit / tot_items) if tot_items else None
    from prompts.extract_checklist import rules_hash
    L = [f"# Loop 0 · T15 提取覆盖率（阈值 {args.threshold}）", "",
         f"- 清单版本 hash：`{rules_hash()}`",
         f"- 覆盖条目：**{tot_hit}/{tot_items} = {rate:.1%}**" if rate is not None else "- 无数据",
         f"- 缺清单的篇目：{len(missing_ck)} {missing_ck or ''}",
         f"- 口径：机械粗筛＝bigram 覆盖率≥{args.threshold} **或** 与清单共享≥{args.run_min} 字连续片段；"
         "低分条目只记「存疑」，最终判定交模型裁（§12.3 #4）。", "",
         "| 篇 | 桶 | 清单条数 | A1 条目 | 提取命中 | 覆盖率 |", "|---|---|---|---|---|---|"]
    for r in per:
        L.append(f"| {r['eval_id']} | {r['bucket']} | {r['ck_items']} | {r['a1_items']} | "
                 f"{r['extract_hit']} | {r['coverage']:.0%} |")
    em = [s for s in suspects if s["kind"] == "extract_miss"]
    nm = [s for s in suspects if s["kind"] == "note_miss"]
    L += ["", f"## 存疑（交模型裁）",
          f"- `extract_miss`（清单没提到）**{len(em)}** 条 —— 提取 prompt 的靶",
          f"- `note_miss`（成文本来就丢了，提取也没提到）**{len(nm)}** 条", ""]
    if suspects:
        L += ["| 篇 | 类型 | 覆盖率 | 最长共享 | A1 条目 |", "|---|---|---|---|---|"]
        for s in sorted(suspects, key=lambda x: (x["eval_id"], x["kind"])):
            L.append(f"| {s['eval_id']} | {s['kind']} | {s['coverage']:.0%} | "
                     f"{s['shared_run']} | {s['a1_text'][:66]} |")

    out = args.out or os.path.join(eval_dir, "loop0_report.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    sp = args.suspects or os.path.join(eval_dir, "loop0_suspects.json")
    with open(sp, "w", encoding="utf-8") as fh:
        json.dump({"threshold": args.threshold, "coverage": rate,
                   "extract_miss": em, "note_miss": nm}, fh, ensure_ascii=False, indent=1)
    print(f"提取覆盖率 {tot_hit}/{tot_items} = {rate:.1%}" if rate is not None else "无数据")
    print(f"存疑 {len(suspects)}（extract_miss {len(em)} / note_miss {len(nm)}）")
    print("报告 →", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
