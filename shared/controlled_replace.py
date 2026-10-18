"""shared/controlled_replace.py — 受控替换（T8）：旧稿 vs 重做稿的 A/B 判定。

来源：`docs/plans/PLAN-20261018-knowledge-layer.md` §3.3 第 4 步「先比后换」+ §6 A2 · T8。

**要解决什么**：重做入口 `resum_save_batch.py` 原行为是「过门禁即覆盖」——
门禁只拦「不合格」，不衡量「比旧稿更好」。于是一版**比旧稿更差**的重做稿
（修掉旧缺陷又引入新缺陷，或只是换了措辞）也会直接盖掉库里那份。
旧稿没有版本历史（库非 git），覆盖即终身（T13 解决了「该不该改」，本模块解决「改了怎么换」）。

**判据零新增**：比较用的就是 T13 同一套确定性缺陷（`content_signals` 的 hard 集合
= 锚点召回/照搬/结构缺失 + 证据确定性缺陷）——「生产本来就会拦」的那一档。
形态/软信号不参与判定：未标定，用它会拦掉无伤大雅的替换，也与 §6 A2 的纪律一致。

**判定规则（保守：宁可保留）**：

- 新稿缺陷数 < 旧稿 → 替换（有实据的改善）
- 新稿 > 旧稿 → 保留旧稿（回归）
- 相等（含双 0）→ 保留旧稿（没有证据表明更好；Brain「也可以选择不改」）
- 无旧稿（新建落地）→ 直接写
- `force=True` → 无条件替换（逃生舱，与内容门禁的 force 语义一致）

**语义腿不在这里**：保存链路的 `admit_candidate` 照旧生效 —— 带独立审核报告
（`review_required`）时要求独立会话/全文覆盖/passed，否则抛错（→ 稿件暂存留档）。
本模块补的是确定性比较腿。

只读旧稿 + 追加决策台账（JSONL 按天滚动）；不写 vault、不改笔记。
"""
from __future__ import annotations

import json
import os
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER_BASE = os.path.join(_ROOT, "notes", "_meta", "replace_decisions.jsonl")


def assess_text(note_text: str, source_text: str = "", note_type: str = "",
                source_author: str = "") -> dict:
    """按既有判据给一版稿子算「确定性缺陷」，旧稿/候选稿同一口径。

    与 `shared.note_audit.necessity_of` 的硬集合完全一致（T13 先例），
    差异只在本函数吃文本、不读文件，便于对未落盘的候选稿直接评估。

    Returns: {"content_blockers": [...], "evidence_blockers": [...], "defect_count": n}
    """
    from prompts import content_signals as CS

    cf = CS.content_flags(note_text or "", source_text or "", note_type or "",
                          source_author=source_author or "")
    hard = CS.classify_content_flags(cf["flags"])["hard"]
    findings = (cf["details"].get("evidence") or {}).get("findings") or []
    evidence = [f"证据缺陷[{f.get('code')}] 第{(f.get('note') or {}).get('line_start')}行：{f.get('reason')}"
                for f in findings if f.get("decision") == "block"]
    return {
        "content_blockers": list(hard),
        "evidence_blockers": evidence,
        "defect_count": len(hard) + len(evidence),
    }


def metrics_from_gate(gate: dict) -> dict:
    """候选稿指标直接取既有门禁结果（`verify_note_mechanical` 与 assess_text 同判据，
    不重复计算 shingle）。字段名与 `assess_text` 完全一致，可直接进 `decide`。"""
    cb = list(gate.get("content_blockers") or [])
    eb = list(gate.get("evidence_blockers") or [])
    return {"content_blockers": cb, "evidence_blockers": eb, "defect_count": len(cb) + len(eb)}


def decide(old: dict = None, new: dict = None, *, force: bool = False) -> dict:
    """A/B 判定（纯函数）。`old=None` 表示无旧稿（新建落地）。

    Returns: {"replace": bool, "reason": str, "old_defects": int|None, "new_defects": int}
    reason ∈ no_old / improved / regressed / no_gain / force
    """
    new = new or {"content_blockers": [], "evidence_blockers": [], "defect_count": 0}
    d_new = new["defect_count"]
    if old is None:
        return {"replace": True, "reason": "no_old", "old_defects": None, "new_defects": d_new}
    d_old = old["defect_count"]
    if force:
        return {"replace": True, "reason": "force", "old_defects": d_old, "new_defects": d_new}
    if d_new < d_old:
        return {"replace": True, "reason": "improved", "old_defects": d_old, "new_defects": d_new}
    if d_new > d_old:
        return {"replace": False, "reason": "regressed", "old_defects": d_old, "new_defects": d_new}
    return {"replace": False, "reason": "no_gain", "old_defects": d_old, "new_defects": d_new}


def log_decision(rec: dict) -> str:
    """把一次替换决策追加进台账（JSONL，按天滚动）；失败返回 ""，**永不抛**。

    台账是观测：能回答「这批重做里有多少被保留旧稿、为什么」。
    """
    try:
        from shared.rolling_log import append_rolling

        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        row.update(rec or {})
        return append_rolling(LEDGER_BASE, json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        return ""


def read_ledger(base: str = "") -> list:
    """读回台账（含历史滚动文件，按日期排序）。仅供统计/测试，读不到返回 []。"""
    import glob

    base = base or LEDGER_BASE
    out = []
    for path in sorted(glob.glob(base.replace(".jsonl", "") + "*.jsonl")):
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            out.append(json.loads(line))
                        except ValueError:
                            continue
        except OSError:
            continue
    return out
