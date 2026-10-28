# -*- coding: utf-8 -*-
"""scripts/distill_prepare.py — S9 有限接线：长源两阶段的派单/回收/装配入口。

PLAN-20261026-low-cost-quality §8.4（S9 入口）/ §9.11-S9（协议）。**默认不改变生产行为**：
只有显式跑 `--prepare` 才会把材料包块写进队列条目的 prompt；`--plan / --emit / --ingest`
只读写各自的评估目录与 pack 存储。

## 四步链（与 `run_async_review.py` 同构）

    只读路由 --plan  →  派提炼 --emit --out DIR  →  子 Agent 提炼  →  回收 --ingest --dir DIR
                                                                    └→ 装配 --prepare

| 步骤 | 命令 | 写哪里 |
|---|---|---|
| 看路由 | `--plan` | 不写任何文件 |
| 派提炼 | `--emit --out DIR` | `DIR/<url_key>_distill.txt` + `DIR/manifest.json` |
| 回收 | `--ingest --dir DIR` | 校验通过才落 `notes/_meta/distill_packs/<url_key>.json` |
| 装配 | `--prepare` | 队列条目 prompt + generation_context（`--dry-run` 只报告） |

## 口径（不要在这里重新发明）

- 路由阈值、包块文案、版本对账、回退语义全在 `shared/distill_dispatch.py`，本脚本只做 I/O。
- pack 机械校验（`shared/distill_pack.validate_pack`）不通过 → **不落盘、不消费**，该篇降级单轮。
- 装配后 `generation_context.prompt_sha256` 同步为装配后 prompt 的 hash，
  `validate_dispatch` 仍须通过；材料包键记 `distill_pack_key` / `distill_pack_source_sha256`。
- 队列默认取 `monitors/pending_summaries.json`；scys 用 `--queue scys`。
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

QUEUES = {
    "monitors": os.path.join(ROOT, "monitors", "pending_summaries.json"),
    "scys": os.path.join(ROOT, "notes", "_scraped", "scys", "pending_summaries.json"),
}
DEFAULT_LOG_DIR = os.path.join(ROOT, "notes", "_meta", "distill_dispatch")


def _queue_path(name: str) -> str:
    return QUEUES.get(name, name)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="S9 长源两阶段派发（plan/emit/ingest/prepare）")
    ap.add_argument("--queue", default="monitors",
                    help="monitors | scys | 直接给队列 JSON 路径")
    ap.add_argument("--store-dir", default="", help="pack 存储目录（默认 notes/_meta/distill_packs）")
    ap.add_argument("--plan", action="store_true", help="只读：逐条路由判定与 pack 状态")
    ap.add_argument("--emit", action="store_true", help="为待提炼长源写自包含任务文件")
    ap.add_argument("--ingest", action="store_true", help="回收提炼回包，校验通过才落 pack")
    ap.add_argument("--prepare", action="store_true", help="把材料包块装配进队列条目 prompt")
    ap.add_argument("--out", default="", help="--emit 输出目录")
    ap.add_argument("--dir", default="", help="--ingest 输入目录（含 manifest.json）")
    ap.add_argument("--limit", type=int, default=0, help="--emit 最多出几篇（0=不限）")
    ap.add_argument("--log", default="", help="--prepare 的 JSONL 留痕路径（空=自动生成）")
    ap.add_argument("--dry-run", action="store_true", help="--prepare 只报告不写队列")
    ap.add_argument("--json", action="store_true", help="只输出 JSON 报告")
    args = ap.parse_args(argv)

    from shared import distill_dispatch as DD

    queue = _queue_path(args.queue)
    report = {"queue": queue}
    if not os.path.exists(queue):
        report["error"] = "队列文件不存在"
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 1

    if args.plan:
        plan = DD.plan_queue(queue, store_dir=args.store_dir)
        report["plan"] = {"total": plan["total"], "counts": plan["counts"]}
        if not args.json:
            report["rows"] = plan["rows"]
    if args.emit:
        out = args.out or os.path.join(DEFAULT_LOG_DIR, "tasks_" + time.strftime("%Y%m%d_%H%M%S"))
        manifest = DD.emit_distill_tasks(queue, out, store_dir=args.store_dir)
        if args.limit:
            manifest["tasks"] = manifest["tasks"][:args.limit]
            with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=1)
        report["emit"] = {"out": out, "tasks": len(manifest["tasks"]),
                          "skipped": len(manifest["skipped"])}
        if not args.json:
            report["emit"]["detail"] = manifest["tasks"]
    if args.ingest:
        if not args.dir:
            report["error"] = "--ingest 需要 --dir"
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return 2
        res = DD.ingest_distill_results(args.dir, store_dir=args.store_dir)
        report["ingest"] = {"saved": len(res["saved"]), "failed": len(res["failed"]),
                            "missing": len(res["missing"])}
        if not args.json:
            report["ingest"]["detail"] = res
    if args.prepare:
        log_path = args.log
        if not log_path and not args.dry_run:
            os.makedirs(DEFAULT_LOG_DIR, exist_ok=True)
            log_path = os.path.join(DEFAULT_LOG_DIR, "dispatch_%s.jsonl"
                                    % time.strftime("%Y%m%d_%H%M%S"))
        res = DD.prepare_queue(queue, store_dir=args.store_dir, log_path=log_path,
                               dry_run=args.dry_run)
        stripped = len([f for f in res["fallback"] if f.get("stripped")])
        report["prepare"] = {"prepared": len(res["prepared"]), "already": len(res["already"]),
                             "stale_stripped": len(res["stale"]),
                             "fallback_stripped": stripped,
                             "fallback": len(res["fallback"]), "dry_run": args.dry_run,
                             "log": log_path}
        if not args.json:
            report["prepare"]["fallback_detail"] = res["fallback"]

    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
