# -*- coding: utf-8 -*-
"""为「待重抓清单」已补回源的条目构建重总结批次（只读，不改笔记不落盘）。

背景：`build_resummarize_queue.py` 曾把源缺失条目分为 refetch:bilibili / refetch:scys
（见 notes/_reports/20260915_待重抓清单.md）。这些条目的源现已补回
（notes/_scraped/bili/<bvid>.md / notes/_scraped/scys/<topicId>.md），
本脚本把清单里全部条目按 URL 从 summary_registry.json 反查登记记录，
组装成与阶段C 批次同构的批次 JSON（note_path / source_path / prompt 预计算），
供 `_save_resum_batch.py` 落盘复用。

分批规则与 build_resum_batches.py 一致：>10000 字 → 4 条/批；4000~10000 → 8 条/批；<4000 → 16 条/批。

用法：python scripts/build_refetch_resum_batches.py
产物：notes/_meta/resum_refetch_batches/refetch_batch_NN.json
"""
import collections
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from prompts import content_signals as CS  # noqa: E402
from prompts.templates import get_note_prompt, QUALITY_GATE_SELFCHECK  # noqa: E402

REGISTRY = ROOT / "notes" / "_meta" / "summary_registry.json"
LIST_MD = ROOT / "notes" / "_reports" / "20260915_待重抓清单.md"
OUT_DIR = ROOT / "notes" / "_meta" / "resum_refetch_batches"
BILI_BASE = ROOT / "notes" / "_scraped" / "bili"
SCYS_BASE = ROOT / "notes" / "_scraped" / "scys"
VAULT = os.getenv("OBSIDIAN_VAULT_PATH") or r"D:\Code\Obsidian\obsidian-path\AI 总结笔记"

SIZES = {"long": 4, "mid": 8, "short": 16}


def extract_urls(md_path: Path) -> tuple[list[str], list[str]]:
    txt = md_path.read_text(encoding="utf-8")
    bvs = re.findall(r"bilibili\.com/video/(BV\w+)", txt)
    tids = re.findall(r"scys\.com/articleDetail/xq_topic/(\d+)", txt)
    return bvs, tids


def source_path_for(url: str, bv: str = "", tid: str = "") -> Path:
    if bv:
        return BILI_BASE / f"{bv}.md"
    if tid:
        return SCYS_BASE / f"{tid}.md"
    return Path()


def main() -> int:
    bvs, tids = extract_urls(LIST_MD)
    print(f"清单：B站 {len(bvs)} + scys {len(tids)} = {len(bvs) + len(tids)}")

    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    # 目标 URL -> registry 记录（同 URL 多条时全部列出供核对，批次只取最后一条并计数）
    url_set = {f"https://www.bilibili.com/video/{bv}" for bv in bvs} | \
              {f"https://scys.com/articleDetail/xq_topic/{tid}" for tid in tids}
    dup = collections.Counter()
    recs: dict[str, dict] = {}
    for v in reg.values():
        if not isinstance(v, dict):
            continue
        u = (v.get("source_url") or "").rstrip("/")
        if u in url_set:
            dup[u] += 1
            recs[u] = v
    missing = [u for u in url_set if u not in recs]
    if missing:
        print("❌ registry 未命中:", missing)
        return 2
    multi = {u: n for u, n in dup.items() if n > 1}
    if multi:
        print("⚠️ 同 URL 多条 registry 记录（取最后一条）:", multi)

    rows = []
    for bv in bvs:
        url = f"https://www.bilibili.com/video/{bv}"
        rows.append(_build_row(recs[url], url, bv=bv))
    for tid in tids:
        url = f"https://scys.com/articleDetail/xq_topic/{tid}"
        rows.append(_build_row(recs[url], url, tid=tid))
    print(f"组装 {len(rows)} 条")

    # 分批
    buckets = {
        "long": [r for r in rows if r["source_words"] > 10000],
        "mid": [r for r in rows if 4000 <= r["source_words"] <= 10000],
        "short": [r for r in rows if r["source_words"] < 4000],
    }
    print("按源长分布：", {k: len(v) for k, v in buckets.items()})
    batches = []
    for name in ("long", "mid", "short"):
        rs = buckets[name]
        for i in range(0, len(rs), SIZES[name]):
            part = rs[i:i + SIZES[name]]
            for r in part:
                r["prompt"] = get_note_prompt(r["note_type"], r["source_words"]) + QUALITY_GATE_SELFCHECK
            batches.append(part)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for f in OUT_DIR.glob("*.json"):
        f.unlink()
    for i, b in enumerate(batches, 1):
        p = OUT_DIR / f"refetch_batch_{i:02d}.json"
        p.write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"批次 {len(batches)} → {OUT_DIR.relative_to(ROOT)}/refetch_batch_NN.json")
    for i, b in enumerate(batches, 1):
        srcs = [r["source_words"] for r in b]
        print(f"  refetch_batch_{i:02d}: {len(b)} 条 源长 {min(srcs)}~{max(srcs)}")
    return 0


def _build_row(v: dict, url: str, bv: str = "", tid: str = "") -> dict:
    fn = v.get("filename") or ""
    folder = (v.get("folder") or "").replace("/", os.sep)
    note_path = os.path.join(VAULT, fn.replace("/", os.sep))
    src = source_path_for(url, bv=bv, tid=tid)
    source_ok = src.exists()
    note_words = None
    missing_sections = []
    if os.path.exists(note_path):
        note = CS.strip_note(Path(note_path).read_text(encoding="utf-8"))
        note_words = CS.count_words(note)
        missing_sections = CS.structure_missing(note, v.get("note_type") or "").get("missing") or []
    source_words = 0
    if source_ok:
        source_words = CS.count_words(CS.strip_source(src.read_text(encoding="utf-8")))
    return {
        "title": v.get("title") or "",
        "url": url,
        "folder": (v.get("folder") or "").replace(os.sep, "/"),
        "zone": folder.split(os.sep)[0] if folder else "?",
        "author": folder.split(os.sep)[2] if folder.count(os.sep) >= 2 else "",
        "note_type": v.get("note_type") or "",
        "summarized_at": (v.get("summarized_at") or "")[:10],
        "note_path": note_path,
        "note_exists": os.path.exists(note_path),
        "source_path": str(src),
        "source_ok": source_ok,
        "note_words": note_words,
        "source_words": source_words,
        "ratio": round(note_words / max(1, source_words), 3) if note_words and source_words else None,
        "missing_sections": missing_sections,
        "class": "resummarize",
    }


if __name__ == "__main__":
    raise SystemExit(main())
