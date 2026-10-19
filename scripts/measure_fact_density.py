# -*- coding: utf-8 -*-
"""事实密度量尺：笔记里「能在源中找到的数值」出现次数 ÷ 千字。

**用途**：对照轮判据之一（`PLAN-20261019-short-source-trim` §4 判据 3；`PLAN-20261018` §8 同款）。
只量「密度」这一件事，不判类型、不判保留率（保留率走 `score_eval_set.py`）。

**口径**（与计划 §1 一致，改动须同步计划）：
- 只数**正文**里的阿拉伯数字串，**跳过标题行**（`#`/`##`…）——否则 `### 10.1` 这类节号会被当成数据；
- **源侧另跳过抓取注入的元信息块**（`> 标题：`/`> 时间：`/`> 原始文章内容`）——那里的数字是集数、
  发布日期与时间戳，不是源内容；不排除会把「标题里的 100 倍」算成笔记可复用的数值；
- 数值按数字串比对，**忽略 `%` 与单位差异**（源写 `97%`、笔记写 `97` 算命中）；
- 分母用 `prompts.verifier.count_note_words`（与变长口径同一个字数定义）。

**两种跑法**：
    python scripts/measure_fact_density.py --dir <round_dir>
        对照集的**原稿**（`eval_set.json` 的 `note_path`）vs 该轮 `notes/<eid>.md`。
    python scripts/measure_fact_density.py --dir <round_dir> --against <round_dir>
        A/B 两臂对打（`--dir` 为左臂；表头写成 生产 / 对照 由 `--labels` 定）。

退出码：0 正常；2 目录缺 `eval_set.json` 或 `notes/`。
"""
import argparse
import json
import os
import re
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prompts.verifier import count_note_words  # noqa: E402
from shared.eval_set import load_rows  # noqa: E402

NUM = re.compile(r"\d+(?:\.\d+)?")

CALIBER = ("口径：正文阿拉伯数字串（跳过 `#` 标题行；源侧另跳过 `> 标题/时间/原始文章内容` 元信息块）÷ 千字；"
           "数值按串比对，忽略 % 与单位；分母 = count_note_words")

# 抓取管线注入的元信息块：数字是集数 / 发布日期 / 时间戳，不属于源内容。
_META = re.compile(r"^>\s*(标题|时间|原始文章内容|来源|链接|URL|作者|发布)")


def _read(path):
    try:
        return open(path, encoding="utf-8").read()
    except (OSError, UnicodeError):
        return ""


def _content_lines(text):
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if _META.match(s):
            continue
        yield s


def source_numbers(source_text):
    """源内容里可被笔记复用的数值集合（去标题行与元信息块）。"""
    return set(NUM.findall("\n".join(_content_lines(source_text))))


def density(note_text, source_text):
    """→ (密度, 命中数, 笔记内数值总数, 笔记字数)"""
    src_nums = source_numbers(source_text)
    nums = []
    for s in _content_lines(note_text):
        nums.extend(NUM.findall(s))
    hit = sum(1 for n in nums if n in src_nums)
    words = count_note_words(note_text) or 1
    return hit / (words / 1000.0), hit, len(nums), words


def _arm_notes(round_dir):
    d = os.path.join(round_dir, "notes")
    if not os.path.isdir(d):
        raise SystemExit(f"缺 notes/：{d}")
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(description="事实密度量尺（对照轮判据 3）")
    ap.add_argument("--dir", required=True, help="轮次目录（含 eval_set.json 与 notes/）")
    ap.add_argument("--against", help="另一臂轮次目录（A/B 对打；可省）")
    ap.add_argument("--labels", default="本轮,对照", help="两臂表头标签（逗号分隔）")
    ap.add_argument("--report", help="写出 Markdown 报告")
    ap.add_argument("--json", help="写出明细 JSON")
    args = ap.parse_args(argv)

    rows = load_rows(os.path.join(args.dir, "eval_set.json"))
    notes_a = _arm_notes(args.dir)
    notes_b = _arm_notes(args.against) if args.against else None
    la, lb = (args.labels.split(",") + ["对照"])[:2]

    head = ["# 事实密度", "", CALIBER, ""]
    cols = "| # | 桶 | 源内数值 | 原稿 | " + la + (" | " + lb if notes_b else "") + " |"
    head += [cols, "|---|---|---|---|" + ("---|" if notes_b else "")]

    detail, buckets = [], {}
    for eid, r in sorted(rows.items()):
        src = _read(r.get("source_path") or "")
        n_src = len(source_numbers(src))
        d_old = density(_read(r.get("note_path") or ""), src)[0]
        d_a = density(_read(os.path.join(notes_a, f"{eid}.md")), src)[0]
        d_b = density(_read(os.path.join(notes_b, f"{eid}.md")), src)[0] if notes_b else None
        bk = r.get("bucket") or "?"
        line = f"| {eid} | {bk} | {n_src} | {d_old:.1f} | {d_a:.1f}"
        if notes_b:
            line += f" | {d_b:.1f}"
        head.append(line + " |")
        detail.append({"eval_id": eid, "bucket": bk, "note_type": r.get("note_type"),
                       "source_words": r.get("source_words"), "source_numbers": n_src,
                       "old": round(d_old, 3), "a": round(d_a, 3),
                       "b": None if d_b is None else round(d_b, 3)})
        buckets.setdefault(bk, []).append((d_old, d_a, d_b))

    head += ["", "## 按源长桶", "",
             "| 桶 | 篇数 | 原稿 | " + la + (" | " + lb if notes_b else "") + " |",
             "|---|---|---|---|" + ("---|" if notes_b else "")]
    summary = {}
    for bk in sorted(buckets):
        vals = buckets[bk]
        o = statistics.mean(v[0] for v in vals)
        a = statistics.mean(v[1] for v in vals)
        bmean = statistics.mean(v[2] for v in vals) if notes_b else None
        row = f"| {bk} | {len(vals)} | {o:.2f} | {a:.2f}"
        if notes_b:
            row += f" | {bmean:.2f}"
        head.append(row + " |")
        summary[bk] = {"n": len(vals), "old": round(o, 3), "a": round(a, 3),
                       "b": None if bmean is None else round(bmean, 3)}

    # 源内容里一个数值都没有的篇目：桶均值天然被它们稀释，单列出来免得被误读成「瘦身导致密度下降」。
    zero = [d["eval_id"] for d in detail if d["source_numbers"] == 0]
    head += ["", f"源内容无数值（密度恒 0，桶均值里只是稀释项）：{('、'.join(zero) if zero else '无')}"
             f"（{len(zero)}/{len(detail)} 篇）"]

    text = "\n".join(head) + "\n"
    print(text)
    if args.report:
        open(args.report, "w", encoding="utf-8").write(text)
        print(f"报告 → {args.report}")
    if args.json:
        json.dump({"rows": detail, "by_bucket": summary}, open(args.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"明细 → {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
