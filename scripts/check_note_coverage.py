# -*- coding: utf-8 -*-
"""当前生产主流程不逐篇调用本工具。
显式运行默认仅报告覆盖疑点（有疑点也 exit 0），源/包不可用仍报错；
只有用户显式要求 --emit-patch 时才生成下述补稿任务协议，非默认第三轮。

scripts/check_note_coverage.py — pack_only 条目写作端两环的消费入口（F7 接线）。

组合流程（VERDICT §七）：po 写作 → **机械覆盖自查（本脚本，零模型）** → 缺项定向补稿
→ 只插不改核验 → 落盘。仅适用于 `generation_context.distill_input_mode == "pack_only"`
的条目；其他条目维持现役单轮派工，本脚本报 `not_pack_only` 正常退出。

用法（编排方/子 Agent 在写稿后、`save_pending.py` 落盘前调用）：
    python scripts/check_note_coverage.py --queue monitors --key <url> --file <draft.md>
    # 输出 JSON 报告；exit 0=无可疑 / 1=有可疑块（走补稿）/ 4=无法核验（源或包失效）

补稿材料生成（可疑块 >0 时，派补稿子 Agent 用；不附源全文）：
    python scripts/check_note_coverage.py --queue scys --key <topicId> --file <draft.md> \
        --emit-patch _tmp/patch_task.md

补稿后「只插不改」核验（F7 停止条件：引入确定性改动即停）：
    python scripts/check_note_coverage.py --queue scys --key <topicId> \
        --file <patched.md> --verify-insert-only <原稿.md>
    # exit 0=纯插入通过 / 5=发现 replace/delete（停止，不落盘）

协议（VERDICT §六/§七 + 意图树 v6 三出口，补稿 Agent 必守）：
1. 对每个可疑条目**先核对「改写也算含」**——成稿已以其他表述覆盖该主题的，勾销不补；
2. **出口②·核实为可舍弃**：确属重复表述/元信息/过程叙述/寒暄/与主线无关的岔题
   （或 support 且主线放得下），勾销不补，并在稿尾记录一句舍弃理由
   （`舍弃：条目 iX —— 一句理由`）；
3. **出口③·缺才补**：主干（trunk）内容确认缺失才**纯插入**（定点插段），
   原稿其余逐字不动；
4. 补稿后**刷新 EXTRACT_CHECKLIST（仅新增覆盖的条目补登记）**——出口②勾销的
   条目不是新增覆盖，不登记。
"""
import argparse
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

QUEUE_PATHS = {
    "monitors": os.path.join(BASE_DIR, "monitors", "pending_summaries.json"),
    "scys": os.path.join(BASE_DIR, "notes", "_scraped", "scys", "pending_summaries.json"),
}
KEY_FIELDS = {"monitors": "url", "scys": "topicId"}


def _load_entry(queue: str, key: str) -> dict:
    path = QUEUE_PATHS[queue]
    items = json.load(open(path, encoding="utf-8"))
    field = KEY_FIELDS[queue]
    entry = next((e for e in items if str(e.get(field, "")) == str(key)), None)
    if entry is None:
        raise SystemExit(f"[ERR] 队列中找不到 {field}={key}")
    return entry


def _emit_patch_task(path: str, draft: str, source_text: str, pack: dict,
                     issues: list, semantic_fix: bool = False) -> None:
    """生成补稿任务文件：协议说明 + 可疑条目素材正文（逐字取自同版源）+ 成稿全文。

    与 F5c 验证过的补稿形态一致（patch_material_D1.md）：只给成稿 + 候选素材，
    **不给源文件**——防补稿环节演变成整篇重写。

    F3R（PLAN-20261030-intent-tree-summary §9.3）：补稿前先做一次语义核对（速览/结论、
    关键案例归属、条件强度）。生产默认 `semantic_fix=False`——语义问题**只报不改**，
    覆盖缺口仍只做纯插入，`--verify-insert-only` 语义不变。隔离复验传 `True`：允许
    定点替换/删除写错的事实并逐条记录，该稿作为独立修正版本、不声称通过只插不改。
    """
    from shared.distill_pack import source_excerpt, source_span_issues
    from shared.note_coverage import SEMANTIC_REVIEW_PRIORITIES
    by_id = {str(it.get("id")): it for it in (pack.get("items") or [])}
    semantic_lines = SEMANTIC_REVIEW_PRIORITIES.split("\n")
    lines = [
        "# 定向补稿任务（机械自查可疑条目）",
        "",
        "协议（三出口：先核对、可舍弃、缺才补）：",
        "1. **改写也算含**：成稿已以其他表述覆盖该主题的，勾销不补；",
        "2. **出口②·核实为可舍弃**：确属重复表述/元信息/过程叙述/寒暄/与主线无关的岔题",
        "   （或 support 且主线放得下），勾销不补，并在稿尾记一行",
        "   `舍弃：条目 <id> —— <一句舍弃理由>`；",
        "3. **出口③·缺才补**：主干（trunk）内容确认缺失才补：在合适位置**纯插入**，",
        "   原稿其余逐字不动；",
        "4. 补完后**仅对新增覆盖的条目**补登稿内 EXTRACT_CHECKLIST（出口②勾销的不登记）。",
        "5. **语义核对（先于补稿）**：" + semantic_lines[0],
    ]
    lines += ["   " + ln for ln in semantic_lines[1:]]
    if semantic_fix:
        lines += [
            "   语义问题可按**定点替换 / 删除**修正，逐条记录「原句 → 修订句 → 源行 + 理由」；",
            "   该稿为独立修正版本，不适用「只插不改」声明。覆盖缺口仍按出口③处理。",
        ]
    else:
        lines += [
            "   语义问题**只报不改**（写进修补说明：原句 → 问题 → 源行）；本任务对覆盖缺口只做纯插入。",
        ]
    lines.append("")
    for x in issues:
        it = by_id.get(str(x.get("ref")))
        if not it:
            continue
        spans = it.get("source_spans") or []
        bad = source_span_issues(spans, source_text)
        tier = str(it.get("tier") or "trunk")
        lines.append(f"## 条目 {it.get('id')}｜{tier}｜{it.get('label')}｜"
                     f"命中率 {x['rate']}")
        if tier == "drop":
            lines.append("（该条目档位为 drop：默认丢。除非成稿明显需要它，按出口②勾销即可）")
        if bad:
            lines.append(f"（素材提取失败：{bad[0]}；请按行段回查源文件 "
                         f"{it.get('source_lines')}）")
        else:
            lines.append(source_excerpt(spans, source_text))
        if it.get("notes"):
            lines.append(f"\n（素材注：{it['notes']}）")
        lines.append("")
    draft_heading = ("# 成稿全文（语义问题可定点修正；其余不动）" if semantic_fix else
                     "# 成稿全文（补稿只许纯插入，其余不动）")
    lines += ["---", "", draft_heading, "", draft]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", required=True, choices=sorted(QUEUE_PATHS))
    ap.add_argument("--key", required=True, help="monitors 传 url；scys 传 topicId")
    ap.add_argument("--file", required=True, help="成稿 md 路径")
    ap.add_argument("--emit-patch", help="可疑块>0 时生成补稿任务文件到该路径")
    ap.add_argument("--verify-insert-only", metavar="ORIG",
                    help="补稿后核验：--file 为补稿稿，ORIG 为补稿前原稿")
    args = ap.parse_args()

    patched = open(args.file, encoding="utf-8").read()

    if args.verify_insert_only:
        from shared.note_coverage import insert_only_violations
        orig = open(args.verify_insert_only, encoding="utf-8").read()
        v = insert_only_violations(orig, patched)
        print(json.dumps({"status": "insert_only", "violations": v},
                         ensure_ascii=False, indent=1))
        return 5 if v else 0

    entry = _load_entry(args.queue, args.key)
    ctx = entry.get("generation_context") or {}

    if ctx.get("distill_input_mode") != "pack_only":
        print(json.dumps({"status": "not_pack_only", "suspects": 0},
                         ensure_ascii=False))
        return 0

    src_path = entry.get("raw_file") or entry.get("output") or ""
    try:
        source_text = open(src_path, encoding="utf-8").read()
    except (OSError, UnicodeError):
        print(json.dumps({"status": "source_unavailable", "path": src_path},
                         ensure_ascii=False))
        return 4

    from shared.distill_dispatch import load_valid_pack_for_source
    pack, why = load_valid_pack_for_source(entry.get("url", ""), source_text,
                                           title=entry.get("title", ""))
    if pack is None:
        print(json.dumps({"status": "pack_unavailable", "reason": why},
                         ensure_ascii=False))
        return 4

    from shared.note_coverage import coverage_report, LABEL_3GRAM_THRESHOLD, CALIBRATION_REF
    rep = coverage_report(source_text, pack, patched)
    rep["status"] = "checked"
    rep["threshold"] = LABEL_3GRAM_THRESHOLD
    rep["calibration"] = CALIBRATION_REF
    rep["report_only"] = not bool(args.emit_patch)
    rep["next_action"] = ("explicit_patch_requested" if args.emit_patch else
                          "save_with_existing_gates")
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    if rep["issues"] and args.emit_patch:
        _emit_patch_task(args.emit_patch, patched, source_text, pack, rep["issues"])
        rep_out = f"补稿任务已生成: {args.emit_patch}"
        print(rep_out)
    # 覆盖可疑只是候选信号；默认不阻塞原保存入口、不生成第三轮模型任务。
    return 1 if rep["issues"] and args.emit_patch else 0


if __name__ == "__main__":
    sys.exit(main())
