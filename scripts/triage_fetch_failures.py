"""scripts/triage_fetch_failures.py — 抓取完成后对失败条目做只读分类。

设计前提（见 fetch_up_range.py:446：rc = 0 if ok else 1）：
- 抓取阶段「只抓 + 记失败」，不做进程内重试；重试 = 断点续抓（done_urls 跳过 rc=0）。
- 本脚本不修改任何文件，仅读取 <author>_fetch_results.json，把失败分成：
    A   环境瞬时（子进程级：segfault/超时/空返回）→ 重跑即自愈
    B1  无字幕(ASR 关) → 需 --with-asr 或接受跳过
    B2  风控 412 → 已被 risk_skip 主动搁置，冷却+换 cookie 后才碰
    B3  视频失效(404/private) → 硬伤，跳过
    B4  其他(带 error) → 先重跑一次(可能瞬时)，仍失败则人工

用法:
    python scripts/triage_fetch_failures.py --author 夏鹏本鹏
    python scripts/triage_fetch_failures.py --results notes/_scraped/夏鹏本鹏_fetch_results.json
"""
import argparse
import glob
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_results(args) -> str:
    if args.results:
        return args.results
    if args.author:
        cand = os.path.join(ROOT, "notes", "_scraped", f"{args.author}_fetch_results.json")
        if os.path.exists(cand):
            return cand
    if args.uid:
        hits = glob.glob(os.path.join(ROOT, "notes", "_scraped", f"*_fetch_results.json"))
        for h in hits:
            # 兼容 bili_<uid>... 命名
            if args.uid in os.path.basename(h):
                return h
    print("找不到 results 文件，请用 --results 指定路径", file=sys.stderr)
    sys.exit(2)


def _classify(ent: dict) -> str:
    rc = ent.get("rc")
    # 子进程级失败：无有效 results 块
    if rc in (3221225477, -9, 143):
        return "A"
    # rc==1：可能是批级空失败，或单条失败(带 error)
    if rc == 1:
        st = ent.get("stdout_tail") or ""
        rec = None
        try:
            rec = json.loads(st)
        except Exception:
            rec = None
        if not rec or not isinstance(rec, dict) or "error" not in rec:
            # 批级空失败（子进程没吐 BATCH_RESULTS）
            return "A"
        err = (rec.get("error") or "").lower()
        if any(k in err for k in ("字幕", "subtitle", "asr", "无字幕", "caption", "cc ")):
            return "B1"
        if any(k in err for k in ("412", "风控", "risk", "rate", "频率")):
            return "B2"
        if any(k in err for k in ("404", "not found", "失效", "private", "不存在",
                                  "deleted", "forbidden", "403")):
            return "B3"
        return "B4"
    # 其他非 0（不应出现）
    return "B4"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--author", default="")
    ap.add_argument("--uid", default="")
    ap.add_argument("--results", default="")
    args = ap.parse_args()

    path = _load_results(args)
    raw = json.load(open(path, encoding="utf-8"))
    # 按 idx 去重：成功(rc=0)优先；否则保留最新一次（文件按批次时间序追加）。
    # 背景：fetch_up_range 的 results.append 累积不 dedupe，同 idx 多次失败会留多条。
    by_idx = {}
    for e in raw:
        idx = e.get("idx")
        if idx is None:
            continue
        cur = by_idx.get(idx)
        if cur is None:
            by_idx[idx] = e
        elif e.get("rc") == 0:
            by_idx[idx] = e  # 成功覆盖任何失败
        elif cur.get("rc") != 0:
            by_idx[idx] = e  # 都是失败，取最新
    data = list(by_idx.values())
    fails = [e for e in data if e.get("rc") != 0]
    total = len(data)
    ok = total - len(fails)

    buckets = defaultdict(list)
    for e in fails:
        buckets[_classify(e)].append(e)

    label = {
        "A":  "A·环境瞬时(子进程级:segfault/超时/空返回) → 重跑即自愈",
        "B1": "B1·无字幕(ASR关) → 需 --with-asr 或接受跳过",
        "B2": "B2·风控412 → 已被 risk_skip 搁置，冷却+换cookie后才碰",
        "B3": "B3·视频失效(404/private) → 硬伤，跳过",
        "B4": "B4·其他(带error) → 先重跑一次，仍失败人工",
    }

    print(f"\n=== 抓取结果 triage: {os.path.basename(path)} ===")
    print(f"总计 {total} | 成功入队 {ok} | 失败 {len(fails)}")
    print("=" * 60)
    for k in ("A", "B1", "B2", "B3", "B4"):
        lst = buckets.get(k, [])
        if not lst:
            continue
        idxs = [e.get("idx") for e in lst]
        print(f"\n[{k}] {label[k]}  (共 {len(lst)})")
        print(f"    idx: {idxs}")
        # 采样一条 error 文本
        sample = None
        for e in lst:
            try:
                rec = json.loads(e.get("stdout_tail") or "{}")
                if rec.get("error"):
                    sample = rec["error"][:160]
                    break
            except Exception:
                continue
        if sample:
            print(f"    err样本: {sample}")

    # 处理建议
    print("\n" + "=" * 60)
    print("处理建议（抓完后再执行，均为只读/幂等）:")
    if buckets.get("A") or buckets.get("B4"):
        print(f"  · 重跑自愈类(A+B4, {len(buckets.get('A', []))+len(buckets.get('B4', []))} 条):")
        print(f"      python scripts/launch_fetch_up_detached.py --uid <UID> --author <名> --start 1 --end {total}")
        print(f"      （done_urls 自动跳过已 rc=0，只重试失败项；默认 --batch-size 1）")
    if buckets.get("B1"):
        print(f"  · 无字幕类(B1, {len(buckets['B1'])} 条): 二选一")
        print(f"      (a) 开 ASR 补抓: launch_fetch_up_detached.py ... --with-asr")
        print(f"      (b) 接受跳过: 这些视频本就无 CC 字幕")
    if buckets.get("B2"):
        print(f"  · 风控412(B2, {len(buckets['B2'])} 条): 已在 risk_skip 集合，冷却/换 cookie 后单独处理")
    if buckets.get("B3"):
        print(f"  · 视频失效(B3, {len(buckets['B3'])} 条): 硬伤，直接跳过（重跑无用）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
