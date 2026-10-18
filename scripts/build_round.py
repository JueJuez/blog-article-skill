# -*- coding: utf-8 -*-
"""对照轮工具（PLAN-20261018 §12.3 #14）：为「重新生成 → 同清单复核 → 判分 → 记变长」搭台。

**为什么需要它**：测生成端改动（A4/A6）不能用「对已填表单重跑判分」——那量不出生成差异。
正确流程是同一批源**重新生成**、用**同一份清单**在新目录重新勾选、再判分，并记录笔记变长幅度。
手工搭这个流程每次都要复制/清空/改路径，容易踩防覆盖护栏，故固化成脚本。

**三层循环的落点**（§8）：
- Loop 1 smoke：`--subset long`（A4）；A6 用 `--subset lowretention`（opinion+structured）
- Loop 2 正式轮：`--subset all`（30 篇全量）

产物布局（新目录，**绝不碰原评估目录与 vault**）：

    <round-dir>/
      eval_set.json          # 子集（供 score_eval_set --dir 用）
      forms/Axx.md           # 清单固化、勾选清空（复核者按新笔记重勾）
      notes/Axx.md           # 子 Agent 生成的新笔记（不写 vault）
      prompts/Axx.md         # 该篇的**生产口径 prompt**（get_note_prompt + 自检段）
      _WRITE_INSTRUCTION.md   # 生成者说明书
      _CHECK_INSTRUCTION.md   # 复核者说明书
      _TASKS.md               # A01..A30 的路径对照表

用法：

    python scripts/build_round.py build  --subset long
    # 派子 Agent：按 prompts/Axx.md 生成 notes/Axx.md；再派独立子 Agent 按清单复核 forms/Axx.md
    python scripts/build_round.py measure
    python scripts/score_eval_set.py --dir <round-dir> --report <round-dir>/_score.md

守卫：目标目录已有勾选过的表单时拒绝重建（`--force` 才覆盖）——复核劳动不能静默清空。
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
DEFAULT_ROUND_DIR = os.path.join(DEFAULT_EVAL_DIR, "round_A4_smoke")
_MUST_BEGIN = "<!-- MUST_KEEP -->"
_MUST_END = "<!-- /MUST_KEEP -->"
_ITEM_RE = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s*(?P<text>\S.*?)\s*$")
# A6 低保留类型桶（§12.1 桶形注脚：opinion 63.2% / structured 71.7% 为最低保留）
_LOW_RETENTION_TYPES = ("opinion", "structured")

# A6 模板侧新增的两段散文（2026-10-18）。A4 正式轮要隔离「A4 流程」主效应，故生成时按 A4
# 落地态（commit 4b26a3d：A4 已落地、A6 未落地）的措辞回退这两段 —— 两个生成端改动合进同一
# 对照出「84.2%→X%」无法归因，是 §8 归因纪律明令禁止的。每项 = (A6 加入后的文本, A4 落地态文本)。
_A6_PROSE = (
    ('寒暄、自我介绍、口头语重复、纯过渡句（"接下来我们说""废话不多说"）。\n\n'
     "**明确不算水分、不得删的**（2026-10-18 收紧，见 DECISION-20261018-info-coverage-scope）：\n"
     "- **承托论点的背景故事、案例细节、推导过程**；\n"
     "- **散落信息点**——不在主线、但可能有帮助的独立事实（持仓/个股点评/人物评价/时事/资源入口）。\n"
     "  哪怕只有一句话、与当前论点无关，也**照原意保留**（可放「补充信息」类小节或就近成句）。\n"
     "  删的边界是「重复说法 / 寒暄 / 自我介绍 / 口头语 / 纯过渡」，**不是「与论点无关」**。",
     "寒暄、自我介绍、口头语重复、与论点无关的铺垫。**承托论点的背景故事、案例细节、"
     "推导过程不属于水分，不得删。**"),
    ("- **判定范围按上下文绑定**：\n"
     '  - **孤立**的纯推广/引流（与内容无关、单摆浮搁的一句"加我微信/看我的资料"）→ **删**，不写进笔记。\n'
     "  - 嵌在**流程 / SOP / 操作步骤**里的推广或引流（例：讲\"如何做自媒体\"的第 4 步是引流入口）→ "
     "**保留并就近标注【推广】**：它是步骤的一部分，删了流程就断了。\n",
     ""),
)


def apply_prompt_profile(prompt: str, profile: str) -> str:
    """按 profile 回退/保留 A6 模板条款（`a4only` = 隔离 A4 主效应，见 `_A6_PROSE`）。

    ⚠️ 定位不到 A6 段就**报错**而不是静默返回原文：静默会让这一轮混入 A6 条款、
    破坏归因，且表面上「跑通了」。
    """
    if profile == "current":
        return prompt
    out = prompt
    for a6, a4 in _A6_PROSE:
        if a6 not in out:
            raise SystemExit(f"[ERR] prompt-profile=a4only 定位不到 A6 段（模板已变）：{a6[:40]!r}")
        out = out.replace(a6, a4)
    if out == prompt:
        raise SystemExit("[ERR] prompt-profile=a4only 未改动任何文本，profile 失效")
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s", "", s or "")


def subset_rows(rows: list, subset: str) -> list:
    if subset == "all":
        return list(rows)
    if subset == "long":
        return [r for r in rows if (r.get("source_words") or 0) > 10000]
    if subset == "lowretention":
        picked = [r for r in rows if r.get("note_type") in _LOW_RETENTION_TYPES]
        # 外加 2 篇短源对照（防副作用看得见）
        short = [r for r in rows if (r.get("source_words") or 0) <= 5000][:2]
        return picked + [r for r in short if r not in picked]
    raise SystemExit(f"未知 subset: {subset}")


def _checked_forms(forms_dir: str) -> list:
    """列出**有勾选**（≥1 条 `- [x]`）的表单 —— 轮次目录的防覆盖判据。

    ⚠️ 不能用 `shared.eval_set.touched_forms`：那个按「有条目」算人工劳动，而轮次表单的条目是
    从基线清单**复制**的（生来就有文本），会把「还没复核」误判成「已有人工劳动」而拒绝重建。
    轮次目录里真正该保护的是**复核动作**，所以只看勾选。
    """
    out = []
    if not forms_dir or not os.path.isdir(forms_dir):
        return out
    for name in sorted(os.listdir(forms_dir)):
        if not name.endswith(".md"):
            continue
        try:
            text = open(os.path.join(forms_dir, name), encoding="utf-8").read()
        except (OSError, UnicodeError):
            continue
        body = text.split(_MUST_BEGIN, 1)[1].split(_MUST_END, 1)[0] if _MUST_BEGIN in text else text
        if re.search(r"^\s*[-*]\s+\[[xX]\]", body, re.M):
            out.append(name)
    return out


def _review_progress(forms_dir: str) -> dict:
    """复核完成度：reviewed＝有勾选；unchecked＝一条都没勾（要么笔记为空、要么还没复核）。"""
    reviewed, unchecked = [], []
    if not os.path.isdir(forms_dir):
        return {"reviewed": reviewed, "unchecked": unchecked}
    for name in sorted(os.listdir(forms_dir)):
        if not name.endswith(".md"):
            continue
        text = open(os.path.join(forms_dir, name), encoding="utf-8").read()
        body = text.split(_MUST_BEGIN, 1)[1].split(_MUST_END, 1)[0] if _MUST_BEGIN in text else text
        items, checked = 0, 0
        for line in body.splitlines():
            m = _ITEM_RE.match(line)
            if not m:
                continue
            items += 1
            if re.match(r"^\s*[-*]\s+\[[xX]\]", line):
                checked += 1
        (reviewed if checked else unchecked).append((name[:-3], checked, items))
    return {"reviewed": reviewed, "unchecked": unchecked}


def parse_items(form_text: str) -> list:
    """取原表单 MUST_KEEP 区里的清单文本（判定结果不取，本轮要重新勾）。"""
    body = form_text
    if _MUST_BEGIN in body and _MUST_END in body:
        body = body.split(_MUST_BEGIN, 1)[1].split(_MUST_END, 1)[0]
    out = []
    for line in body.splitlines():
        m = _ITEM_RE.match(line)
        if m:
            out.append(m.group("text").strip())
    return out


def cmd_build(args) -> int:
    eval_dir, round_dir = args.eval_dir, args.round_dir
    from shared.eval_set import load_rows, distortion_block

    rows_all = load_rows(os.path.join(eval_dir, "eval_set.json"))
    rows = subset_rows(list(rows_all.values()), args.subset)
    rows.sort(key=lambda r: r["eval_id"])
    if not rows:
        print("[ERR] 子集为空")
        return 2

    forms_dir = os.path.join(round_dir, "forms")
    if os.path.isdir(forms_dir):
        checked = _checked_forms(forms_dir)
        if checked and not args.force:
            print(f"[ERR] {round_dir} 已有 {len(checked)} 张**已勾选**的表单（复核劳动），拒绝覆盖。"
                  "换 --round-dir，或确认可丢弃后加 --force")
            return 2
    for sub in ("forms", "notes", "prompts"):
        os.makedirs(os.path.join(round_dir, sub), exist_ok=True)

    from prompts.templates import get_note_prompt, QUALITY_GATE_SELFCHECK, source_chars_of

    for r in rows:
        eid = r["eval_id"]
        src_form = os.path.join(eval_dir, "forms", f"{eid}.md")
        items = parse_items(open(src_form, encoding="utf-8").read()) if os.path.exists(src_form) else []
        if not items:
            print(f"  ⚠️ {eid} 原表单无可取清单，跳过")
            continue
        head = (f"# {eid} · {(r.get('title') or '')[:60]}\n\n"
                f"- 轮次：`{os.path.basename(round_dir)}`（清单固化，**勾选按本轮新笔记重判**）\n"
                f"- 桶/源长：**{r.get('bucket')}**（源 {r.get('source_words')} 字）\n"
                f"- 类型/路由：`{r.get('note_type')}` / `{r.get('folder')}`\n"
                f"- 源：`{r.get('source_path')}`\n"
                f"- **本轮新笔记**：`{os.path.join(round_dir, 'notes', eid + '.md')}`\n\n"
                "## 必留清单（清单不变；按新笔记逐条判「实质保留」→ `- [x]`）\n\n")
        body = "\n".join(f"- [ ] {t}" for t in items)
        with open(os.path.join(round_dir, "forms", f"{eid}.md"), "w", encoding="utf-8") as fh:
            fh.write(head + _MUST_BEGIN + "\n" + body + "\n" + _MUST_END + "\n\n"
                     + distortion_block() + "\n")

        source = ""
        try:
            source = open(r["source_path"], encoding="utf-8").read()
        except (OSError, UnicodeError):
            pass
        prompt = get_note_prompt(r.get("note_type") or "structured", source_chars_of(source)) \
            + QUALITY_GATE_SELFCHECK
        prompt = apply_prompt_profile(prompt, args.prompt_profile)
        with open(os.path.join(round_dir, "prompts", f"{eid}.md"), "w", encoding="utf-8") as fh:
            fh.write(f"# {eid} 生产口径 prompt（勿改；profile={args.prompt_profile}）\n\n" + prompt)

    json.dump({"items": rows}, open(os.path.join(round_dir, "eval_set.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    write_instr = (
        "# 轮次生成：写新笔记（A4/A6 对照专用）\n\n"
        "对每篇 `{eid}`：\n"
        "1. Read **源文件**（路径见 `forms/{eid}.md` 的「源」行）——只读源，不要读原笔记（防锚定）。\n"
        "2. Read 生产口径 prompt：`prompts/{eid}.md`（第一行 `# …（勿改…）` **之后**的全部内容）。\n"
        "3. 严格按该 prompt 写稿，用 Write 写到：`notes/{eid}.md`。\n\n"
        "**清单块（源长 >10000 字才要求）**：prompt 若含「长源提取清单（两阶段）」一节，"
        "就必须按它把 `<!-- EXTRACT_CHECKLIST -->` … `<!-- /EXTRACT_CHECKLIST -->` 清单块**原样写在正文最前面**"
        "（系统会机械校验并剥离，不写等于这一步没做）；短源 prompt 若要求 `SOURCE_THIN` 块也照办。\n\n"
        "**硬约束**：不读 vault 里的旧笔记；不读其他轮次目录；不改 prompt；只写 `notes/{eid}.md` 一个文件。\n"
        "完成后回一行：`{eid} ok <笔记字数>`。\n"
    )
    check_instr = (
        "# 轮次复核：勾选 + 失真观察（独立审者，不得看生成过程）\n\n"
        "对每篇 `{eid}`：\n"
        "1. Read `forms/{eid}.md` 里的**源**（先只读源）与**本轮新笔记**（`notes/{eid}.md`）。\n"
        "2. 对 `MUST_KEEP` 区清单每一条判「**实质保留**」：全部关键要素（数字/口径/条件/方向强度）都在 → 改成 `- [x]`，\n"
        "   否则留 `- [ ]`。允许同义改写；缺任一要素即不勾（与 A1 基线同口径）。\n"
        "3. 再填 `DISTORTION` 区**失真观察**（只观察、不计入保留率）：逐项核笔记是否把源写成了**别的意思** ——\n"
        "   命中就改 `- [x]` 并在冒号后写 ≤60 字证据（源位置/原话）；没有就不勾。四类：\n"
        "   范围泛化（限定范围被写宽/写窄）／方向反转（该说 X 说成非 X，或强度被改）／\n"
        "   案例替换（举例换成源里没有的）／源外数字（出现源里不存在的数字或口径）。\n"
        "4. 用 Edit/Write 把 `forms/{eid}.md` **原地更新**（只改勾选状态与失真证据，不改清单条目文字）。\n\n"
        "**硬约束**：不要改清单文字；不要读旧笔记或上一轮结果（防锚定）；不要写别的文件。\n"
        "完成后回一行：`{eid} checked。<勾选数>/<总条数>`；有失真另附 `distortion=<类别>`。\n"
    )
    with open(os.path.join(round_dir, "_WRITE_INSTRUCTION.md"), "w", encoding="utf-8") as fh:
        fh.write(write_instr)
    with open(os.path.join(round_dir, "_CHECK_INSTRUCTION.md"), "w", encoding="utf-8") as fh:
        fh.write(check_instr)
    with open(os.path.join(round_dir, "_TASKS.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# 轮次台：subset=`{args.subset}` · prompt-profile=`{args.prompt_profile}` · "
                 f"{len(rows)} 篇\n\n"
                 "| # | 源 | 生产 prompt | 新笔记 | 表单 |\n|---|---|---|---|---|\n")
        for r in rows:
            eid = r["eval_id"]
            fh.write(f"| {eid} | `{r.get('source_path')}` | `prompts/{eid}.md` | "
                     f"`notes/{eid}.md` | `forms/{eid}.md` |\n")

    print(f"轮次目录 {round_dir}：{len(rows)} 篇（subset={args.subset}）")
    print("下一步：派生成子 Agent → 派复核子 Agent → build_round.py measure → score_eval_set.py")
    return 0


def cmd_normalize(args) -> int:
    """**必做**：把轮次笔记里的 A4 清单块剥掉，镜像生产的落盘处理。

    不剥的后果严重且隐蔽：复核者拿到的稿里**就带着清单**，它照着清单抄就能「找到」全部条目 →
    保留率虚高、对照失效。生产侧（`save_summary_only` / `resum_save_batch`）本来就剥，轮次目录
    必须做同一件事。原稿留 `.raw.md` 备查；清单对账报告落 `_checklist_reports.json`。
    """
    from prompts.extract_checklist import process as cx_process
    from shared.eval_set import load_rows
    rows = load_rows(os.path.join(args.round_dir, "eval_set.json"))
    reports, stripped = {}, 0
    for eid, r in sorted(rows.items()):
        path = os.path.join(args.round_dir, "notes", f"{eid}.md")
        if not os.path.exists(path):
            continue
        text = open(path, encoding="utf-8").read()
        if "EXTRACT_CHECKLIST" not in text:
            continue
        src = ""
        try:
            src = open(r["source_path"], encoding="utf-8").read()
        except (OSError, UnicodeError):
            pass
        out = cx_process(text, src, note_type=r.get("note_type") or "", title=r.get("title") or "",
                         source_path=r.get("source_path") or "",
                         source_chars=r.get("source_words") or 0, persist=False)
        if out.get("has_block"):
            if not os.path.exists(path[:-3] + ".raw.md"):
                os.rename(path, path[:-3] + ".raw.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(out["note"])
            reports[eid] = out.get("report") or {}
            stripped += 1
    with open(os.path.join(args.round_dir, "_checklist_reports.json"), "w", encoding="utf-8") as fh:
        json.dump(reports, fh, ensure_ascii=False, indent=1)
    print(f"剥清单：{stripped} 篇（原稿留 .raw.md）")
    for eid, rep in reports.items():
        print(f"  {eid}: 清单 {rep.get('total', 0)}｜命中 {rep.get('hit', 0)}｜未进 "
              f"{rep.get('miss', 0)}｜待模型判 {rep.get('needs_model', 0)}"
              f"｜校验丢弃 {rep.get('dropped_unverifiable', 0)}")
    return 0


def cmd_measure(args) -> int:
    """记「笔记变长幅度」（§12.2-7：保留更多会让笔记变长，允许但要记录）。"""
    from prompts.verifier import count_note_words
    from shared.eval_set import load_rows
    rows = load_rows(os.path.join(args.round_dir, "eval_set.json"))
    L = ["# 变长幅度（本轮新笔记 vs 评估集原笔记）", "",
         "| # | 桶 | 原稿字数 | 新稿字数 | 变化 |", "|---|---|---|---|---|"]
    deltas = []
    for eid, r in sorted(rows.items()):
        p = os.path.join(args.round_dir, "notes", f"{eid}.md")
        if not os.path.exists(p):
            continue
        new = count_note_words(open(p, encoding="utf-8").read())
        old = r.get("note_words") or 0
        d = (new / old - 1) if old else None
        deltas.append(d)
        L.append(f"| {eid} | {r.get('bucket')} | {old} | {new} | "
                 f"{('+' if d and d > 0 else '')}{d:.0%} |" if d is not None else
                 f"| {eid} | {r.get('bucket')} | {old} | {new} | — |")
    if deltas:
        L += ["", f"- 有效 {len(deltas)} 篇；平均变化 {sum(deltas) / len(deltas):+.0%}；"
                  f"变长 {sum(1 for d in deltas if d > 0)} 篇 / 变短 {sum(1 for d in deltas if d < 0)} 篇"]
    prog = _review_progress(os.path.join(args.round_dir, "forms"))
    L += ["", "## 复核完成度（判分前先看这里）", "",
          f"- 已勾选表单 **{len(prog['reviewed'])}** 篇；**一条都没勾 {len(prog['unchecked'])} 篇**"]
    if prog["unchecked"]:
        L.append("- ⚠️ 「一条都没勾」= 要么新笔记确实是空的，要么**这份还没复核**。"
                 "后者会让判分把 0% 当成真实回归 —— 核对后再判分。清单：")
        L += [f"  - `{eid}`（0/{n} 条）" for eid, _c, n in prog["unchecked"]]
    out = os.path.join(args.round_dir, "_length_delta.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(filter(None, L)) + "\n")
    print(f"变长报告 → {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="对照轮工具（建台 / 记变长）")
    ap.add_argument("mode", choices=["build", "normalize", "measure"])
    ap.add_argument("--eval-dir", default=DEFAULT_EVAL_DIR)
    ap.add_argument("--round-dir", default=DEFAULT_ROUND_DIR)
    ap.add_argument("--subset", default="long", choices=["all", "long", "lowretention"])
    ap.add_argument("--prompt-profile", default="current", choices=["current", "a4only"],
                    help="a4only=回退 A6 模板条款（隔离 A4 主效应，A4 正式轮用）；current=生产态")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    return {"build": cmd_build, "normalize": cmd_normalize,
            "measure": cmd_measure}[args.mode](args)


if __name__ == "__main__":
    raise SystemExit(main())
