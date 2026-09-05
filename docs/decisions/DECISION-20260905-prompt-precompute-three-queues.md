# DECISION-20260905-prompt-precompute-three-queues

## 背景
文档多处声称「子 Agent 消费队列中已算好的 prompt」，但实际只有 `articles/main.py` 降级
返回值带 prompt；monitors 与 scys 批量两处入队写点并未写入 `prompt` 字段，文档-代码漂移。

## 决策
1. **三队列统一 prompt 预计算**：`monitors/run.py:_queue_pending_summary`、
   `scripts/scys_batch_fetch.py:build_pending_entry`、`scripts/fetch_up_range.py` 入队时一律
   写 `"prompt": get_note_prompt(note_type) + QUALITY_GATE_SELFCHECK`；条目缺 `note_type`
   时读 raw 前 4000 字 + 标题自动兜底分类（`classify_note_type`）。
2. 文档统一口径（AGENTS.md / scys-fetch-sop.md / monitors/README.md / DECISION-20260821）：
   预计算在**入队写点**完成（复用 articles 的分类器 + 模板函数），不再归属 `articles/main.py`。
3. 回归测试 `tests/test_pending_prompt_precompute.py`（7 个）钉死三写点行为。

## 影响
- 子 Agent 消费队列时 prompt 恒可用，无需自调任何 CLI；分类与模板选择在入队时锁定。
- 全量测试 255 passed / 0 failed（2026-09-05）。
