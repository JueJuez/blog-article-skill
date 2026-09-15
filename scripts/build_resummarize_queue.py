"""构建「重总结（resummarize）候选队列」——只读，不删不改任何笔记。

背景：DECISION-20260915 §7 之后，门禁判据已换成内容判据，但**生成侧（prompt/模板）
尚未改造**。用户决定不再对历史重名副本做「留哪版」裁决，而是——

    旧版整体作废 + 把最近落盘的笔记按新 prompt 全部重总结一遍。

重跑的前置条件是**源文本可定位**（重跑 = 用已有 raw 源重新生成，**不是**重新抓取）。
本脚本把 946 条（summarized_at >= 2026-09-10）分成三类：

| 分类 | 含义 | 处置 |
|---|---|---|
| `resummarize` | 笔记在库 + 源可定位 | **本阶段直接重跑** |
| `refetch:bilibili` / `refetch:scys` | 笔记在库 + 源缺失，但可重抓 | **列清单，下阶段抓完再跑** |
| `exclude:*` | 公众号（用户决定排除）/ 无 URL / 登记表悬空 | 不动 |

用法：
  python scripts/build_resummarize_queue.py --since 2026-09-10 \
      --json notes/_meta/resummarize_queue.json \
      --refetch notes/_reports/20260915_待重抓清单.md \
      --report notes/_reports/20260915_重跑候选清单.md
"""
from __future__ import annotations

import argparse
import collections
import difflib
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prompts import content_signals as CS  # noqa: E402
from scripts import audit_gate_signals as A  # noqa: E402

REGISTRY = os.path.join(ROOT, "notes", "_meta", "summary_registry.json")
VAULT = os.getenv("OBSIDIAN_VAULT_PATH") or r"D:\Code\Obsidian\obsidian-path\AI 总结笔记"


def normkey(s: str) -> str:
    """标题归一化：去日期数字串、只留中英数字，截断 40 字。"""
    s = re.sub(r"\d{6,}", "", s or "")
    s = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s)
    return s[:40]


def host(url: str) -> str:
    m = re.search(r"https?://([^/]+)", url or "")
    return (m.group(1) if m else "").lower()


def build_source_index() -> tuple[dict, list]:
    """源文本索引：notes/_raw_*.md + notes/_scraped/**/*.md。"""
    idx: dict[str, str] = {}
    files = list(glob.glob(os.path.join(ROOT, "notes", "_raw_*.md")))
    files += glob.glob(os.path.join(ROOT, "notes", "_scraped", "**", "*.md"), recursive=True)
    for p in files:
        k = normkey(os.path.basename(p)[:-3])
        if len(k) >= 6:
            idx.setdefault(k, p)
    return idx, files


def make_resolver(idx: dict, keys: list, bv_map: dict):
    def resolve(v: dict) -> str:
        m = A.BV_RE.search(v.get("source_url") or "")
        if m and bv_map.get(m.group(1).upper()):
            return bv_map[m.group(1).upper()]
        k = normkey(v.get("title") or "")
        if len(k) < 6:
            return ""
        if k in idx:
            return idx[k]
        head = k[:20]
        for c in keys:
            if c.startswith(head) or k.startswith(c[:20]):
                return idx[c]
        close = difflib.get_close_matches(k, keys, n=1, cutoff=0.82)
        return idx[close[0]] if close else ""

    return resolve


def classify(r: dict) -> str:
    """返回 resummarize / refetch:bilibili / refetch:scys / exclude:<reason>。"""
    if not r["note_exists"]:
        return "exclude:note_missing"      # 登记表悬空（多为早期飞书-only）
    if r["source_ok"]:
        return "resummarize"
    h = host(r["url"])
    if not h:
        return "exclude:no_url"
    if "weixin" in h or h.startswith("mp."):
        return "exclude:wechat"            # 用户决定：公众号不重抓
    if "bilibili" in h:
        return "refetch:bilibili"
    if "scys" in h:
        return "refetch:scys"
    return "exclude:unknown_host"


def collect(since: str) -> list:
    data = json.load(open(REGISTRY, encoding="utf-8"))
    idx, _ = build_source_index()
    keys = list(idx)
    resolve = make_resolver(idx, keys, A._slice_raw_map())

    rows = []
    for v in data.values():
        if not isinstance(v, dict):
            continue
        fn = v.get("filename") or ""
        if not fn:
            continue
        summarized_at = (v.get("summarized_at") or "")[:10]
        if since and summarized_at and summarized_at < since:
            continue
        note_path = os.path.join(VAULT, fn.replace("/", os.sep))
        src_path = resolve(v)
        folder = fn.replace(os.sep, "/")
        row = {
            "title": v.get("title") or "",
            "url": v.get("source_url") or "",
            "folder": folder,
            "zone": folder.split("/")[0] if folder else "?",
            "author": folder.split("/")[2] if folder.count("/") >= 2 else "",
            "note_type": v.get("note_type") or "",
            "summarized_at": summarized_at,
            "note_path": note_path,
            "note_exists": os.path.exists(note_path),
            "source_path": src_path,
            "source_ok": bool(src_path and os.path.exists(src_path)),
        }
        if row["note_exists"]:
            note = CS.strip_note(open(note_path, encoding="utf-8").read())
            row["note_words"] = CS.count_words(note)
            sd = CS.structure_missing(note, row["note_type"])
            row["missing_sections"] = sd.get("missing") or []
        if row["source_ok"]:
            src = CS.strip_source(open(src_path, encoding="utf-8").read())
            row["source_words"] = CS.count_words(src)
            if row.get("note_words"):
                row["ratio"] = round(row["note_words"] / max(1, row["source_words"]), 3)
        for a in ("note_words", "source_words", "ratio"):
            row.setdefault(a, None)
        row["class"] = classify(row)
        rows.append(row)
    return rows


def _prio(r: dict) -> tuple:
    """优先级：结构缺失多 → 压缩比低（长源被砍）→ 源长。越小越该先重跑。"""
    missing = len(r.get("missing_sections") or [])
    ratio = r.get("ratio")
    return (-missing, ratio if ratio is not None else 9.9, -(r.get("source_words") or 0))


def med(xs):
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else None


def write_queue(path: str, rows: list) -> None:
    todo = sorted([r for r in rows if r["class"] == "resummarize"], key=_prio)
    payload = {
        "_note": "重总结队列（build_resummarize_queue.py 生成，只读扫描产物）。"
                 "按 _prio 排序（结构缺失多 → 压缩比低 → 源长）。重跑=用 source_path 重新生成，"
                 "经 scripts/_save_summary_from_file.py --force 覆盖同名笔记。",
        "generated_at": CS.now_str() if hasattr(CS, "now_str") else "",
        "count": len(todo),
        "items": todo,
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    json.dump(payload, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def write_report(path: str, rows: list, since: str) -> None:
    cls = collections.Counter(r["class"] for r in rows)
    ok = [r for r in rows if r["class"] == "resummarize"]
    L = []
    P = L.append
    P("# 重总结候选清单（只读扫描）")
    P("")
    P(f"- 范围：`summarized_at >= {since}`" if since else "- 范围：全库")
    P(f"- 总条目 **{len(rows)}**")
    for k, v in cls.most_common():
        P(f"  - `{k}`：{v}")
    P("- 重跑定义：**用已有 raw 源重新生成**（非重新抓取），旧版归档、新版覆盖同名。")
    P("")

    P("## 一、可直接重跑（源可定位）")
    P("")
    g = collections.defaultdict(list)
    for r in ok:
        g[f'{r["zone"]}/{r["author"]}' if r["author"] else r["zone"]].append(r)
    P("| 分组 | 条目 | 源长中位 | 笔记中位 | 比值中位 | 结构缺失 |")
    P("|---|---|---|---|---|---|")
    for k, v in sorted(g.items(), key=lambda x: -len(x[1])):
        if len(v) < 3:
            continue
        P(f'| `{k}` | {len(v)} | {med([x["source_words"] for x in v])} | '
          f'{med([x["note_words"] for x in v])} | {med([x["ratio"] for x in v])} | '
          f'{sum(1 for x in v if x.get("missing_sections"))} |')
    P("")
    P("### 压缩比分布")
    P("")
    b = collections.Counter()
    for r in ok:
        rt = r.get("ratio")
        if rt is None:
            continue
        b["<0.10 严重压缩" if rt < 0.10 else "0.10~0.25 偏压缩" if rt < 0.25
          else "0.25~0.60 正常" if rt < 0.60 else "0.60~1.5 展开" if rt < 1.5
          else ">1.5 长于源"] += 1
    for k, v in b.most_common():
        P(f"- {k}：{v}")
    P("")
    P("### 优先级前 20（结构缺失多 + 压缩严重）")
    P("")
    P("| # | 标题 | 分组 | 源长 | 笔记 | 比值 | 缺失模块 |")
    P("|---|---|---|---|---|---|---|")
    for i, r in enumerate(sorted(ok, key=_prio)[:20], 1):
        P(f'| {i} | {r["title"][:34]} | `{r["zone"]}` | {r["source_words"]} | '
          f'{r["note_words"]} | {r["ratio"]} | {"、".join(r.get("missing_sections") or []) or "—"} |')
    P("")

    P("## 二、待重抓（下阶段抓完再总结）")
    P("")
    for c in ("refetch:bilibili", "refetch:scys"):
        sub = [r for r in rows if r["class"] == c]
        P(f"### {c} —— {len(sub)} 条（详见 `notes/_reports/20260915_待重抓清单.md`）")
        P("")
    P("## 三、排除")
    P("")
    P(f'- `exclude:wechat` {cls["exclude:wechat"]} 条（用户决定：公众号不重抓）')
    P(f'- `exclude:no_url` {cls["exclude:no_url"]} 条（无 URL，无法重抓）')
    P(f'- `exclude:note_missing` {cls["exclude:note_missing"]} 条（登记表悬空，库里无此文件）')
    P("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def write_refetch(path: str, rows: list) -> None:
    L = []
    P = L.append
    P("# 待重抓清单（源缺失，下阶段抓完再总结）")
    P("")
    P("> 这些条目**笔记已在库**，但源文本（转录稿/原文）已不在本地，无法重跑。")
    P("> 抓取完成后按下阶段流程重新总结。**本文件仅为清单，不做任何抓取。**")
    P("")
    for c, label, hint in (
        ("refetch:bilibili", "B站（视频）", "可用 `scripts/fetch_up_range.py` / `videos/` 单条抓取补源"),
        ("refetch:scys", "scys 生财有术", "可用 `scripts/land_scys_by_key.py` 或按月补齐补源"),
    ):
        sub = [r for r in rows if r["class"] == c]
        P(f"## {label} —— {len(sub)} 条")
        P("")
        P(f"> {hint}")
        P("")
        P("| # | 标题 | 分组 | 笔记字数 | 缺失模块 | URL |")
        P("|---|---|---|---|---|---|")
        for i, r in enumerate(sorted(sub, key=lambda x: -(x.get("note_words") or 0)), 1):
            P(f'| {i} | {r["title"][:30]} | `{r["zone"]}` | {r.get("note_words") or "-"} | '
              f'{"、".join(r.get("missing_sections") or []) or "—"} | {r["url"]} |')
        P("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--report", default="")
    ap.add_argument("--refetch", default="")
    args = ap.parse_args()

    rows = collect(args.since)
    cls = collections.Counter(r["class"] for r in rows)
    print(f"条目 {len(rows)}：", dict(cls.most_common()))
    if args.json:
        write_queue(args.json, rows)
        print("队列 →", args.json)
    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)) or ".", exist_ok=True)
        write_report(args.report, rows, args.since)
        print("报告 →", args.report)
    if args.refetch:
        os.makedirs(os.path.dirname(os.path.abspath(args.refetch)) or ".", exist_ok=True)
        write_refetch(args.refetch, rows)
        print("待重抓 →", args.refetch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
