"""shared/eval_set.py — 固定评估集（T3）与保留度基线（纯股，只读生产数据）

来源：`docs/plans/PLAN-20261018-knowledge-layer.md` §6 A1 / §8。

为什么需要它：我们的唯一保留度量化指标（硬锚点召回）在 1298 对真实源/笔记语料里
**只覆盖 37%**（其余 63% 源的锚点数 < `MIN_ANCHORS`，判据不适用）。没有评估集，
后续任何改动都是「看起来更好」，无法回答「改之前是多少、改之后是多少」。

四件事：

1. `measure`     —— 廉价测量（只数字数），用于分层；不跑四类判据（长源 shingle 很贵）。
2. `stratify_sample` —— 按**源长分桶**为主层、**笔记类型轮转**为副层抽样，默认种子固定 → 可复现。
3. `audit_row`   —— 对**抽中样本**才跑 `shared.note_audit.audit_one`（既有判据），得到「改之前」的基线。
4. `render_form` / `parse_form` / `score_forms` —— 人工必留清单表单与保留率判分。

只读承诺：本模块不写 vault、不改登记表、不改笔记；产物由 CLI 落到 `notes/_meta/eval/`。

为什么不用等比分配（2026-10-18 决策）：全库分桶是 697/355/181/49/16，等比抽样会让最需要的
长源桶（>10000，正是召回衰减段，见 §2.1）只剩 1 条，失去分辨率。故**每桶 6 条**等额分配 ——
评估集要的是每层的分辨率，不是对全库的代表性。

表单状态语义：人工读源 → 逐条写下「丢了不行」的核心事实/论点 → 回查笔记：
`- [ ]` = 未保留（要在 Eva 报告里出现），`- [x]` = 笔记里有。**勾选即判定**，不另设分数字段
（口径沿用 `PLAN-20261014` §10.6：用户只标必留清单，不判类型、不通读、不打分）。
"""
from __future__ import annotations

import collections
import os
import re

# 源长分桶（与 §2.1 的召回衰减统计同口径，便于直接对比）
BUCKETS: tuple = (
    ("0-2k", 0, 2000),
    ("2-5k", 2000, 5000),
    ("5-10k", 5000, 10000),
    ("10-20k", 10000, 20000),
    (">20k", 20000, None),
)
BUCKET_LABELS = tuple(b[0] for b in BUCKETS)

DEFAULT_SEED = 20261018          # 固定种子：评估集必须可复现，换机/换人不得抽样漂移
DEFAULT_PER_BUCKET = 6           # 5 桶 × 6 = 30 篇（计划要求 30）
RETENTION_TARGET = 0.90          # §10.6 目标保留率
MIN_ITEMS_PER_FORM = 3           # 少于该条数的表单视为未填，不参与统计（防「标了 1 条就 100%」）

_MUST_BEGIN = "<!-- MUST_KEEP -->"
_MUST_END = "<!-- /MUST_KEEP -->"
ITEM_RE = re.compile(r"^\s*[-*]\s+\[(?P<state>[ xX])\]\s*(?P<text>\S.*?)\s*$")
_HINT_RE = re.compile(r"^\s*[-*]\s+(?P<text>\S.*?)\s*$")


# ---------------------------------------------------------------- ① 测量

def bucket_of(source_words: int) -> str:
    """源长 → 桶标签；不在任何桶（负数/None）返回 ""。"""
    for label, lo, hi in BUCKETS:
        if source_words >= lo and (hi is None or source_words < hi):
            return label
    return ""


def measure(item: dict) -> dict:
    """给语料条目补 `source_words` / `note_words`（读文件失败时置 None）。

    这里**故意不**跑 `content_signals`：只为分层，1298 条全跑 shingle 太贵。
    """
    from prompts import content_signals as CS

    row = dict(item)
    for key, path_key, stripper in (
        ("source_words", "source_path", CS.strip_source),
        ("note_words", "note_path", CS.strip_note),
    ):
        path = item.get(path_key) or ""
        try:
            with open(path, encoding="utf-8") as fh:
                row[key] = CS.count_words(stripper(fh.read()))
        except (OSError, UnicodeError):
            row[key] = None
    row["bucket"] = bucket_of(row["source_words"]) if row["source_words"] else ""
    row["note_type"] = item.get("note_type") or "legacy"
    return row


# ---------------------------------------------------------------- ② 分层抽样

def _round_robin_by_type(rows: list, n: int, rng) -> list:
    """类型轮转抽样：每种类型轮流取一条，直到取满或耗尽。

    目的：30 条里尽量铺开笔记类型，避免某一桶全是同一种模板的产物。
    桶内先按 (title, note_path) 排序，保证同一 seed 下跨机器结果一致。
    """
    by_type = collections.defaultdict(list)
    for r in sorted(rows, key=lambda r: (r.get("title") or "", r.get("note_path") or "")):
        by_type[r["note_type"]].append(r)
    pools = {t: list(v) for t, v in by_type.items()}
    for t in pools:
        rng.shuffle(pools[t])
    order = sorted(pools)
    out = []
    while len(out) < n:
        added = False
        for t in order:
            if pools[t]:
                out.append(pools[t].pop())
                added = True
                if len(out) >= n:
                    break
        if not added:
            break
    return out


def allocate(rows: list, per_bucket: int = DEFAULT_PER_BUCKET,
             total: int = 0) -> tuple:
    """计算每桶应抽条数。Returns: (plan, shortage)。

    ⚠️ **缺额不做「富桶补抽」**（2026-10-18 刻意选择）：某桶样本不足时若把缺额倒给别的桶，
    会出现「全库只有 16 条 >20k，却被要求承担别的桶的缺额」这种把稀缺层抽干的情况——
    既失真也毁掉桶内多样性。宁可总数不足 30 并如实报告缺额，也不要假配额。
    """
    counts = collections.Counter(r["bucket"] for r in rows if r["bucket"])
    target = per_bucket or max(1, total // max(1, len(BUCKETS)))
    plan = {}
    for label in BUCKET_LABELS:
        plan[label] = min(target, counts.get(label, 0))
    shortage = {l: target - plan[l] for l in BUCKET_LABELS if target - plan[l] > 0}
    return ({k: v for k, v in plan.items() if v > 0}, shortage)


def stratify_sample(rows: list, per_bucket: int = DEFAULT_PER_BUCKET,
                    total: int = 0, seed: int = DEFAULT_SEED) -> tuple:
    """按桶分配 → 桶内类型轮转抽样。

    Returns: (picked_rows, plan) —— picked 已按桶序排好，供 CLI 编号稳定。
    缺额经 `allocate` 计算后不补抽，由调用方决定是否告警。
    """
    import random

    rng = random.Random(seed)
    plan, _shortage = allocate(rows, per_bucket=per_bucket, total=total)
    buckets = collections.defaultdict(list)
    for r in rows:
        if r["bucket"]:
            buckets[r["bucket"]].append(r)
    picked = []
    for label in BUCKET_LABELS:
        if not plan.get(label):
            continue
        picked.extend(_round_robin_by_type(buckets[label], plan[label], rng))
    for i, r in enumerate(picked, 1):
        r["eval_id"] = f"A{i:02d}"
    return picked, plan


# ---------------------------------------------------------------- ③ 基线（只对抽中样本跑判据）

def audit_row(row: dict) -> dict:
    """跑既有四类判据 + 证据检查（同一真源 `shared.note_audit`，不另写一份）。"""
    from shared.note_audit import audit_one

    res = audit_one(row, with_evidence=True, evidence_limit=120,
                    passthrough_keys=("title", "folder", "note_type"))
    out = dict(row)
    out.update({
        "anchors": res["anchors"],
        "form": res["form"],
        "structure": res["structure"],
        "verbatim": res["verbatim"],
        "flags": res["flags"],
        "evidence_blockers": res.get("evidence_blockers", []),
        "ratio": res["ratio"],
        "note_sha256": _hash_file(row.get("note_path")),
        "source_sha256": _hash_file(row.get("source_path")),
    })
    # hash 口径与 prompts.provenance 一致（UTF-8 文本），便于日后与派单证据对照
    return out


def _hash_file(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        from prompts.provenance import text_hash
        with open(path, encoding="utf-8") as fh:
            return text_hash(fh.read())
    except (OSError, UnicodeError):
        return ""


def machine_hints(row: dict, limit: int = 12) -> list:
    """机械提取的「可核对起点」清单：源里有、且笔记里**没出现**的锚点。

    ⚠️ 这是**候选**，不是结论：锚点命中不等于语义保留。人必须逐条核对后再勾，
    否则会把 recall 的噪声直接写进人工标准（这正是 §3.3「清单是检查表不是大纲」的纪律）。
    """
    missed = (row.get("anchors") or {}).get("missed") or []
    return [m for m in missed][:limit]


# ---------------------------------------------------------------- ④ 人工必留清单表单

FORM_HEADER = (
    "# {id} · {title}\n\n"
    "- 桶/源长：**{bucket}**（源 {source_words} 字 / 笔记 {note_words} 字，比值 {ratio}）\n"
    "- 类型/路由：`{note_type}` / `{folder}`\n"
    "- 源： `{source_path}`\n"
    "- 笔记：`{note_path}`\n"
    "- 源 sha256：`{source_sha256}`\n"
    "- 笔记 sha256：`{note_sha256}`\n\n"
    "**机械基线（既有判据，不是本次人工结论）**\n\n"
    "- 硬锚点：{matched}/{total}"
    + "{recall}　形态：{forms}　结构缺失：{missing}　照搬：{verbatim}\n"
    "- 缺陷/待复核：{blockers}\n\n"
)

FORM_BODY = (
    "## 必留清单（读源后填，行首 `- [ ]`）\n\n"
    "> 口径：只标「**丢了不行**」的核心事实与论点（含数字口径、前提条件、反例、资源入口）。\n"
    "> 不判类型、不通读全篇、不打分。一条一行；写完回查笔记，**笔记里有的改成 `- [x]`**。\n"
    "> `<!-- MUST_KEEP -->` 与结束标记之间的内容才会被判分脚本读取。\n\n"
    + _MUST_BEGIN + "\n"
    "- [ ] \n"
    "- [ ] \n"
    "- [ ] \n"
    + _MUST_END + "\n\n"
    "## 机械候选（可选起点：源里有、笔记里字面未出现的锚点）\n\n"
    "> 仅供参考，**必须逐条核对后才把结论写进上面清单**，不要把本区直接勾上。\n\n"
)


def render_form(row: dict) -> str:
    a = row.get("anchors") or {}
    recall = (f"（召回 {a['recall']:.0%}）" if a.get("recall") is not None
              else f"（源锚点 {a.get('total', 0)} 个 < 阈值，判据不适用）")
    head = FORM_HEADER.format(
        id=row.get("eval_id", "?"), title=(row.get("title") or "")[:60],
        bucket=row.get("bucket") or "?", source_words=row.get("source_words"),
        note_words=row.get("note_words"), ratio=row.get("ratio"),
        note_type=row.get("note_type") or "legacy", folder=row.get("folder") or "",
        source_path=row.get("source_path") or "", note_path=row.get("note_path") or "",
        source_sha256=row.get("source_sha256") or "", note_sha256=row.get("note_sha256") or "",
        matched=a.get("matched", 0), total=a.get("total", 0), recall=recall,
        forms="、".join((row.get("form") or {}).get("signals", []) or []) or "无",
        missing="、".join((row.get("structure") or {}).get("missing", []) or []) or "无",
        verbatim=row.get("verbatim"),
        blockers="、".join(str(b) for b in (row.get("evidence_blockers") or [])) or "无",
    )
    body = FORM_BODY
    hints = machine_hints(row)
    if hints:
        body += "".join(f"- {h}\n" for h in hints) + "\n"
    else:
        body += "- （源锚点全部字面命中，或该源锚点不足以生成候选）\n"
    return head + body


def parse_form(text: str) -> dict:
    """解析一张表单：只取 MUST_KEEP 标记之间的清单行。

    Returns: {"items": [{"text","kept"}...], "total":n, "kept":k, "retention":float|None,
              "filled": bool}  —— `filled=False` 表示还没标（少于 MIN_ITEMS_PER_FORM 条）。
    """
    if _MUST_BEGIN in text and _MUST_END in text:
        body = text.split(_MUST_BEGIN, 1)[1].split(_MUST_END, 1)[0]
    else:
        body = text
    items = []
    for line in body.splitlines():
        m = ITEM_RE.match(line)
        if not m:
            continue
        t = m.group("text").strip()
        if not t or t.startswith("<!--"):
            continue
        items.append({"text": t, "kept": m.group("state").lower() == "x"})
    total = len(items)
    kept = sum(1 for i in items if i["kept"])
    return {
        "items": items,
        "total": total,
        "kept": kept,
        "retention": (kept / total) if total else None,
        "filled": total >= MIN_ITEMS_PER_FORM,
    }


def touched_forms(form_dir: str) -> list:
    """列出已被人工写过条目的表单文件名（模板里的空 `- [ ] ` 不算）。

    判据与 `parse_form` 同一套（MUST_KEEP 区内、有非空文本的清单行）——
    「会被判分脚本读走的内容」就是「重建时必须保护的内容」。哪怕只写了 1 条
    （未达 `MIN_ITEMS_PER_FORM`、不进统计）也算 touched：那是人工劳动，不是模板。
    用途：`scripts/build_eval_set.py` 重建前的防覆盖护栏 —— 人工清单是数小时的人工
    资产，静默清空是数据事故，不能靠「同种子可复现」假定可以重建。
    """
    out = []
    if not form_dir or not os.path.isdir(form_dir):
        return out
    for name in sorted(os.listdir(form_dir)):
        if not name.endswith(".md"):
            continue
        try:
            with open(os.path.join(form_dir, name), encoding="utf-8") as fh:
                parsed = parse_form(fh.read())
        except (OSError, UnicodeError):
            continue
        if parsed["total"] > 0:
            out.append(name)
    return out


def score_forms(form_dir: str, rows_by_id: dict) -> dict:
    """遍历目录里的表单给分；返回 {items:[...], aggregate:{...}}。

    未填（条目数 < MIN_ITEMS_PER_FORM）的表单单独列出，不混进「已达标」里充数。
    """
    per, pending = [], []
    for name in sorted(os.listdir(form_dir)) if os.path.isdir(form_dir) else []:
        if not name.endswith(".md") or name.startswith("README"):
            continue
        path = os.path.join(form_dir, name)
        try:
            with open(path, encoding="utf-8") as fh:
                parsed = parse_form(fh.read())
        except (OSError, UnicodeError):
            continue
        eid = (rows_by_id.get(name[:-3]) or {}).get("eval_id") or name[:-3]
        meta = rows_by_id.get(name[:-3], {})
        rec = {
            "eval_id": eid, "file": name, "total": parsed["total"], "kept": parsed["kept"],
            "retention": parsed["retention"], "filled": parsed["filled"],
            "bucket": meta.get("bucket") or "", "note_type": meta.get("note_type") or "",
            "source_words": meta.get("source_words"), "note_words": meta.get("note_words"),
            "anchor_recall": (meta.get("anchors") or {}).get("recall"),
            "missed_texts": [i["text"] for i in parsed["items"] if not i["kept"]],
        }
        (per if parsed["filled"] else pending).append(rec)

    by_bucket, by_type = collections.defaultdict(list), collections.defaultdict(list)
    for r in per:
        if r["retention"] is not None:
            by_bucket[r["bucket"]].append(r["retention"])
            by_type[r["note_type"]].append(r["retention"])

    def _agg(d):
        return {k: {"n": len(v), "mean_retention": round(sum(v) / len(v), 3),
                    "min_retention": round(min(v), 3),
                    "below_target": sum(1 for x in v if x < RETENTION_TARGET)}
                for k, v in sorted(d.items())}

    allr = [r["retention"] for r in per if r["retention"] is not None]
    return {
        "items": per,
        "pending": pending,
        "aggregate": {
            "filled_forms": len(per),
            "pending_forms": len(pending),
            "total_items": sum(r["total"] for r in per),
            "kept_items": sum(r["kept"] for r in per),
            "mean_retention": round(sum(allr) / len(allr), 3) if allr else None,
            "min_retention": round(min(allr), 3) if allr else None,
            "forms_below_target": sum(1 for r in per
                                      if r["retention"] is not None and r["retention"] < RETENTION_TARGET),
            "retention_target": RETENTION_TARGET,
            "by_bucket": _agg(by_bucket),
            "by_type": _agg(by_type),
        },
    }


def load_rows(eval_json: str) -> dict:
    """按表单文件名（eval_id）索引 eval_set.json 的条目。"""
    import json
    data = json.load(open(eval_json, encoding="utf-8"))
    return {it["eval_id"]: it for it in data.get("items", [])}
