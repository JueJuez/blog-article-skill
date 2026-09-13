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
               decision: str, action: str, comment: str = "") -> str:
    """记录一次父 Agent 抽检结果到 _review_log.jsonl，返回写入路径。

    dims: {维度键: "pass"|"warn"|"fail", ...}
    """
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "filename": filename,
        "note_type": note_type,
        "triggers": list(triggers),
        "dims": dims,
        "decision": decision,
        "action": action,
        "comment": comment,
    }
    try:
        os.makedirs(os.path.dirname(REVIEW_LOG), exist_ok=True)
        with open(REVIEW_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return REVIEW_LOG
    except Exception as e:  # 台账失败不影响主流程
        return f"REVIEW_LOG_FAILED:{e}"


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
