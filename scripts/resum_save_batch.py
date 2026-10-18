# -*- coding: utf-8 -*-
"""存量重做落盘（阶段C 起的批量重做唯一落盘入口）：把子 Agent 生成的临时 md 覆盖写回各自已有的 note_path。

设计（2026-09-17 重构）：
  in-place 覆盖能力已**下沉到生产函数** `articles.main.save_summarized_article(note_path=...)`，
  本脚本退化为薄壳，只做三件只有它知道的事：
    1. 遍历批次 JSON，定位临时稿 `<out_dir>/<batch_name>_<NN>.md`；
    2. 落盘前校验：主题词行存在（MISSING_TOPIC_LINE）+ 机械门禁 `verify_note_mechanical`；
    3. 调生产函数落盘（标签/来源链接/格式化/dedup 登记全部复用生产链路，不再复制实现）。

为什么以前这里复制了 4 个变换（extract_and_strip_topics / infer_semantic_tags /
format_note_with_prompt / verify_note_mechanical）：当初 `save_summarized_article` 只支持
「由 title+publish_time 重新推导文件名」，而重做必须写回既有精确路径。现在生产函数支持
`note_path`，复制实现即可删除——否则生产侧改标签规则时，重做出的笔记会与正常落盘漂移。

⚠️ 与 `overwrite=True` 的区别（易混淆，别再搞错）：
  `overwrite=True` 覆盖的是**同名**文件，而文件名仍是 `generate_filename` 推导出来的；
  标题一旦与磁盘现有名错配（阶段C 690 条里有 18 条）就扑空 → 照样生成 -N 副本。
  本脚本走 `note_path`，压根不推导文件名。

用法：
  python scripts/resum_save_batch.py <batch_NN.json> <out_dir> [--force]
  out_dir 下临时笔记命名约定：<batch_name>_<索引02d>.md（与子 Agent 产出一致）
  --force：豁免内容门禁 + 跳过 A/B 比较无条件替换（逃生舱；机械门禁不豁免）

**T7 稿件不丢（2026-10-18）**：重做是**最需要留档**的场景 —— 拦下不落盘就意味着这版重做稿
直接蒸发，而旧稿还留在库里，「新的比旧的差」永远无从比较。故本脚本在四条**有稿可留**的
失败路径（缺主题词行 / 机械门禁 / 内容硬失败 / 落盘异常）都调
`shared.draft_stage.stage_blocked` 留一份带 hash 的暂存（`notes/_meta/staged_drafts/<日期>/`），
再照原样记为失败。`MISSING_FILE`（子 Agent 从未产出）无稿可暂存，旧稿不受影响；
`NOTE_PATH_EMPTY` 与派单校验失败两处属于批次条目/来源问题，早期中止、不入暂存（草稿仍在 out_dir）。
暂存失败只告警，不改变返回码与判定。

**T8 受控替换（2026-10-18）**：过门禁 ≠ 比旧稿更好 —— 原行为「过门禁即覆盖」会让一版
修掉旧缺陷又引入新缺陷的稿子照样盖掉旧版。现在：候选稿先暂存 → 与旧稿按**同一套确定性
缺陷**（T13 硬集合：锚点召回/照搬/结构缺失 + 证据确定性缺陷）A/B 比较 →
**明确更少缺陷才替换**；相等/更差保留旧稿（Brain「也可以选择不改」，判定 `shared/controlled_replace`）。
`--force` 无条件替换（逃生舱，兼作内容门禁豁免）。决策落 `notes/_meta/replace_decisions.jsonl`
台账并回写暂存 meta；语义腿照旧走保存链路的 `admit_candidate`（带独立审核报告时强制通过）。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TOPIC_LINE_RE = re.compile(r"【核心主题词】\s*[:：]?\s*\S+")


def _stage_failure(it: dict, content: str, source_text: str, reason: str, issues=None) -> None:
    """T7：重做稿被拦时留档（含双 hash），旧稿仍留库里 → 才有「先比后换」的可能。"""
    from shared.draft_stage import stage_blocked

    ctx = it.get("generation_context") or {}
    stage_blocked(
        content, reason=reason, route="resum_save_batch", issues=issues or [],
        url=it.get("url", ""), title=it.get("title", ""), note_type=it.get("note_type", ""),
        source_text=source_text or "", source_path=it.get("source_path", ""),
        prompt_sha256=ctx.get("prompt_sha256") or "unknown")


def _stage_candidate(it: dict, content: str, source_text: str, gate: dict) -> dict:
    """T8：候选稿在替换判定前先暂存（无暂存物就无从比较、也无从回退到旧版）。

    静默（quiet）：每个条目的结果由调用方按决策打印，不再额外刷一行暂存提示。
    """
    from shared.draft_stage import stage_blocked

    ctx = it.get("generation_context") or {}
    return stage_blocked(
        content, quiet=True, reason="redo_candidate", route="resum_save_batch",
        issues=list(gate.get("issues") or []),
        url=it.get("url", ""), title=it.get("title", ""), note_type=it.get("note_type", ""),
        source_text=source_text or "", source_path=it.get("source_path", ""),
        prompt_sha256=ctx.get("prompt_sha256") or "unknown")


def _annotate_staged(staged: dict, extra: dict) -> None:
    """T8：把替换决策/最终去处回写进暂存 meta；拿不到 meta 就静默跳过。"""
    meta = (staged or {}).get("meta") or ""
    if meta:
        from shared.draft_stage import annotate
        annotate(meta, extra)


def main() -> int:
    args = sys.argv[1:]
    force = "--force" in args
    if force:
        args = [a for a in args if a != "--force"]
    if len(args) < 2:
        print("用法: resum_save_batch.py <batch.json> <out_dir> [--force]")
        return 2
    batch_path, out_dir = args[0], args[1]
    batch_name = os.path.splitext(os.path.basename(batch_path))[0]
    items = json.load(open(batch_path, encoding="utf-8"))

    from shared.env import load_env
    load_env(force_obsidian=True)
    from articles.main import save_summarized_article
    from prompts.verifier import verify_note_mechanical, count_note_words

    ok, failed, landed, kept = 0, [], 0, []
    for i, it in enumerate(items):
        md = os.path.join(out_dir, f"{batch_name}_{i:02d}.md")
        title = it.get("title", "")
        if not os.path.exists(md):
            failed.append((i, "MISSING_FILE", title))
            continue
        with open(md, encoding="utf-8") as f:
            content = f.read()
        if not TOPIC_LINE_RE.search(content):
            _stage_failure(it, content, "", "missing_topic_line", ["MISSING_TOPIC_LINE"])
            failed.append((i, "MISSING_TOPIC_LINE", title))
            continue

        note_path = it.get("note_path", "")
        if not note_path:
            failed.append((i, "NOTE_PATH_EMPTY", title))
            continue
        # note_path 不存在 = 该笔记当初从未落盘（登记表悬空 / -1 副本未生成）。
        # 重做目标就是把它们补齐，因此「缺失即落地新建」而非跳过（写入的仍是 note_path，不产副本）。
        landed_new = not os.path.exists(note_path)

        url = it.get("url", "")
        note_type = it.get("note_type", "")
        author = it.get("author", "")

        # 门禁（与生产一致）：H1/来源链接/URL + 极端字数；不过就不覆盖旧版好笔记
        src = it.get("source_path", "")
        source_chars = 0
        source_text = ""
        if src and os.path.exists(src):
            try:
                _src_txt = open(src, encoding="utf-8").read()
                source_chars = count_note_words(_src_txt)
                source_text = _src_txt
            except Exception:
                pass
        from prompts.provenance import validate_dispatch
        try:
            validate_dispatch(it, source_text=source_text)
        except ValueError as exc:
            failed.append((i, str(exc), title))
            continue
        gate = verify_note_mechanical(content, note_type, source_url=url,
                                     source_chars=source_chars, source_text=source_text, source_author=author)
        if not gate["passed"]:
            from shared.gate_blockers import log_gate_block
            log_gate_block('resum', note_type, url, title, gate['issues'], evidence=gate['evidence_report'])
            _stage_failure(it, content, source_text, "mechanical_block", gate["issues"])
            failed.append((i, "GATE:" + ";".join(gate["issues"]), title))
            continue
        # 内容硬失败门禁（2026-09-18 与 save_summary_only 对齐）：锚点召回/照搬/结构缺失
        # 确定性内容缺陷，拦下不覆盖旧版好笔记；--force 为逃生舱。
        if gate.get("content_blockers") and not force:
            _stage_failure(it, content, source_text, "content_block", gate["content_blockers"])
            failed.append((i, "CONTENT_GATE:" + ";".join(gate["content_blockers"]), title))
            continue

        # ---- T8 受控替换：候选稿先暂存，再与旧稿比确定性缺陷，明确更优才覆盖 ----
        from shared import controlled_replace as CR
        staged = _stage_candidate(it, content, source_text, gate)
        new_metrics = CR.metrics_from_gate(gate)
        try:
            from prompts.evidence import resolve_source_author
            author = resolve_source_author(source_text, author)
        except Exception:
            pass
        old_metrics, decision = None, None
        if not landed_new:
            try:
                with open(note_path, encoding="utf-8") as f:
                    old_text = f.read()
            except (OSError, UnicodeError):
                decision = {"replace": True, "reason": "old_unreadable",
                            "old_defects": None, "new_defects": new_metrics["defect_count"]}
            else:
                old_metrics = CR.assess_text(old_text, source_text, note_type, source_author=author)
                decision = CR.decide(old_metrics, new_metrics, force=force)
        if decision is None:                        # 旧稿不存在（重做补落地）
            decision = CR.decide(None, new_metrics, force=force)
        _od = decision.get("old_defects")
        _dec_note = "" if landed_new else (
            f"{decision['reason']}，缺陷 {_od if _od is not None else '?'}→{decision['new_defects']}")

        def _ledger(action: str) -> None:
            CR.log_decision({
                "batch": batch_name, "action": action, "reason": decision["reason"],
                "title": title, "note_path": note_path, "note_type": note_type,
                "old_defects": decision["old_defects"], "new_defects": decision["new_defects"],
                "old_detail": ((old_metrics or {}).get("content_blockers", [])[:4]
                               + (old_metrics or {}).get("evidence_blockers", [])[:4]),
                "new_detail": (new_metrics["content_blockers"][:4] + new_metrics["evidence_blockers"][:4]),
                "staged": (staged or {}).get("draft", ""),
            })

        if not decision["replace"]:
            _ledger("keep")
            _annotate_staged(staged, {"outcome": "kept_old", "replace_decision": decision})
            kept.append((i, _dec_note, title))
            print(f"  ↺ [{i}] 保留旧稿（{_dec_note}）| {title[:30]}")
            continue

        _vault = os.getenv("OBSIDIAN_VAULT_PATH", "")
        rel_dir = ""
        if _vault:
            try:
                rel_dir = os.path.relpath(os.path.dirname(note_path), _vault)
            except Exception:
                rel_dir = ""

        try:
            from prompts.evidence import attach_evidence
            context = attach_evidence(it.get("generation_context"), gate['evidence_report'], note_type)
            review_file = it.get('review_file') or (md + '.review.json')
            if os.path.exists(review_file):
                context['review_required'] = True
                with open(review_file, encoding='utf-8') as f:
                    context['review'] = json.load(f)
            _formed, _fname = save_summarized_article(
                content,
                original_url=url,
                author=author,
                original_title=title,
                tags=[],
                note_type=note_type,
                publish_time=0,
                folder=rel_dir,
                obsidian=True,
                note_path=note_path,
                source_text=source_text,
                generation_context=context,
                review_reasons=gate.get('review_flags') or [],
            )
            if gate.get('review_flags'):
                from prompts.review_rubric import queue_for_review
                queue_for_review(_fname, note_type, gate['review_flags'], url, evidence=gate['evidence_report'])
        except Exception as e:
            if (staged or {}).get("ok"):
                _annotate_staged(staged, {"outcome": "save_failed", "issues": [f"SAVE_FAILED:{e}"]})
            else:
                _stage_failure(it, content, source_text, "save_failed", [f"SAVE_FAILED:{e}"])
            _ledger("save_failed")
            failed.append((i, f"SAVE_FAILED:{e}", title))
            continue

        _annotate_staged(staged, {"outcome": ("created" if landed_new else "replaced"),
                                  "replace_decision": decision})
        _ledger("create" if landed_new else "replace")
        ok += 1
        if landed_new:
            landed += 1
        if (i + 1) % 4 == 0 or i == len(items) - 1:
            _tag = " [NEW]" if landed_new else ""
            _dn = f"  [{_dec_note}]" if _dec_note else ""
            print(f"  ✓ [{i}] {title[:28]} -> {os.path.basename(note_path)} ({len(_formed)}字){_tag}{_dn}")

    print(f"\n{batch_name}: 覆盖落盘 {ok}/{len(items)}（新建落地 {landed}） | "
          f"保留旧稿 {len(kept)} | 失败 {len(failed)}")
    for i, why, t in kept:
        print(f"  ↺ [{i}] 保留旧稿（{why}）| {t[:34]}")
    for i, why, t in failed:
        print(f"  ✗ [{i}] {why} | {t[:34]}")
    return 0 if (ok + len(kept)) > 0 or len(items) == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
