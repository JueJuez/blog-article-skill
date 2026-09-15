"""父 Agent 抽检标准（Review Rubric）。

设计哲学（2026-09-13 决策）：
  机械层只管「形」（H1 / 来源链接 / 字数触发），语义层只由「人 / LLM」管。
  本模块定义：
    - 触发条件（可机械计算，来自 verifier 的 review_flags + 压缩信号 + 随机抽样）
    - 五维语义判定框架（A–E，仅父 LLM 能逐维判定，本模块不代判，只提供结构）
    - 判定 → 动作映射
    - 留痕（_review_log.jsonl / _review_queue.jsonl）

无人值守降级：纯自动 monitor 跑、没有父 LLM 在环时，机械信号命中只落盘 + 打
`needs_review` 标签进 _review_queue.jsonl，等人工 / 外接 AI 审；绝不因「机械过」就假定语义 OK。
"""
import os
import json
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REVIEW_LOG = os.path.join(ROOT, "notes", "_review_log.jsonl")
REVIEW_QUEUE = os.path.join(ROOT, "notes", "_review_queue.jsonl")
# 根因台账（2026-09-15 新增）：抽检的真正产出不是「修好这一篇」，而是归因到根因。
# 同一根因累计 >= PROMPT_FIX_THRESHOLD → 触发一次 prompt/模板修订任务。
ROOT_CAUSE_LEDGER = os.path.join(ROOT, "notes", "_root_cause_ledger.jsonl")
PROMPT_FIX_THRESHOLD = 3

# 归因取值（父 Agent 抽检时必须选一个；`gate` 表示笔记没问题、是触发条件误报）
ROOT_CAUSES = {
    "prompt": "总结 prompt 的问题（要求缺失、有歧义、字数口径误导、模块过多撑篇幅）",
    "template": "模板结构问题（必备模块不适配该类源、模块顺序不合理）",
    "agent": "子 Agent 执行问题（偷懒、标题级空话、漏模块、目录式复述、自写脚本绕过流程）",
    "source": "源本身问题（ASR 严重错字、丢行、内容太浅撑不起笔记）",
    "gate": "门禁触发条件误报（判据本身错，笔记无问题）",
    "none": "未发现问题",
}

# 随机抽样比例：每批 dispatch 随机抽 N 篇做全量语义复核（防「机械过就放任」）
RANDOM_SAMPLE_RATIO = 0.15
RANDOM_SAMPLE_MIN = 1
RANDOM_SAMPLE_MAX = 3

# 五维语义判定框架（维度键 → 说明）。逐维由父 LLM 判 pass / warn / fail。
DIMENSIONS = {
    "A_synthesis": "合成度：是否按模板结构重组，而非「他说…他又说…」的转录缩写",
    "B_completeness": "完整性：源的核心论点 / 框架 / 结论是否都在；压掉的是水货还是干货",
    "C_fluency": "通顺度：无破碎句 / 无 ASR 错字 / 术语首现即解释",
    "D_fidelity": "忠实度：无编造 / 无加原文没有的例子 / 无改作者立场",
    "E_compliance": "合规度：无 H1 / 无来源链接行 / 主题词在 / 类型结构对",
}

# 判定 → 动作
ACTION_BY_DECISION = {
    "pass": "落盘通过，无需动作",
    "fix_inline": "父 Agent 直接就地修（仅 C/D/E 小问题，如 ASR 错字），不重派",
    "resummarize": "打回子 Agent，附具体指令（太长照搬→压到 X 字只留框架；漏点→补源第三点）",
    "needs_review": "标 needs_review 进人工队列，不阻塞主流程（源本身有问题 / 仍可疑）",
}


def should_review_randomly(batch_size: int, rng=None) -> bool:
    """按批次大小决定是否对本次条目做随机抽样抽检（防机械过就放任）。"""
    import random
    n = max(RANDOM_SAMPLE_MIN, min(RANDOM_SAMPLE_MAX, int(batch_size * RANDOM_SAMPLE_RATIO)))
    r = rng or random
    return r.random() < (n / max(1, batch_size))


def decision_to_action(decision: str) -> str:
    return ACTION_BY_DECISION.get(decision, ACTION_BY_DECISION["needs_review"])


def log_review(filename: str, note_type: str, triggers: list, dims: dict,
               decision: str, action: str, comment: str = "",
               root_cause: str = "none") -> str:
    """记录一次父 Agent 抽检结果到 `_review_log.jsonl`，并把归因写入根因台账。

    dims: {维度键: "pass"|"warn"|"fail", ...}
    root_cause: ROOT_CAUSES 之一。**必须显式给出**——抽检的价值在于归因，
      没有归因的抽检只是「人工兜底」，救不了下一批。
    """
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "filename": filename,
        "note_type": note_type,
        "triggers": list(triggers),
        "dims": dims,
        "decision": decision,
        "action": action,
        "root_cause": root_cause,
        "comment": comment,
    }
    try:
        os.makedirs(os.path.dirname(REVIEW_LOG), exist_ok=True)
        with open(REVIEW_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _append_root_cause(rec)
        return REVIEW_LOG
    except Exception as e:  # 台账失败不影响主流程
        return f"REVIEW_LOG_FAILED:{e}"


def _append_root_cause(rec: dict) -> None:
    """把归因单独落一份台账（便于统计与触发修订任务）。"""
    rc = rec.get("root_cause") or "none"
    if rc == "none":
        return
    row = {
        "ts": rec["ts"], "root_cause": rc, "note_type": rec.get("note_type", ""),
        "filename": rec.get("filename", ""), "decision": rec.get("decision", ""),
        "triggers": rec.get("triggers", []), "comment": rec.get("comment", ""),
    }
    with open(ROOT_CAUSE_LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def root_cause_counts() -> dict:
    """统计各类根因出现次数。"""
    counts: dict = {}
    if not os.path.exists(ROOT_CAUSE_LEDGER):
        return counts
    with open(ROOT_CAUSE_LEDGER, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rc = json.loads(line).get("root_cause", "none")
            except Exception:
                continue
            counts[rc] = counts.get(rc, 0) + 1
    return counts


def suggest_prompt_fix(threshold: int = PROMPT_FIX_THRESHOLD) -> list:
    """同一根因累计 >= threshold → 返回该根因，提示「该去改 prompt/模板，而不是继续抽检」。

    抽检长期高频命中是红灯：说明流程有系统缺陷未修（见 DECISION-20260915 第 3.3 节）。
    """
    return [rc for rc, n in root_cause_counts().items()
            if rc in ("prompt", "template", "agent") and n >= threshold]


def queue_for_review(filename: str, note_type: str, reasons: list, source_url: str = "") -> str:
    """无人值守降级：把需人工/外接 AI 复核的条目写入 _review_queue.jsonl。"""
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "filename": filename,
        "note_type": note_type,
        "source_url": source_url,
        "reasons": list(reasons),
        "status": "needs_review",
    }
    try:
        os.makedirs(os.path.dirname(REVIEW_QUEUE), exist_ok=True)
        with open(REVIEW_QUEUE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return REVIEW_QUEUE
    except Exception as e:
        return f"REVIEW_QUEUE_FAILED:{e}"


def pending_reviews() -> list:
    """读取尚未处理的人工复核队列。"""
    if not os.path.exists(REVIEW_QUEUE):
        return []
    out = []
    with open(REVIEW_QUEUE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    return out
