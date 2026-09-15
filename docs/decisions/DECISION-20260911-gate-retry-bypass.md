# ⚠️ 本决策已废止（2026-09-15）
>
> 「重试放行逃生舱」随 content-first 门禁改造一并**删除**：它为「源撑不起固定区间」而设，
> 而新门禁的字数硬拦只剩两条确定性缺陷（内容缺失 `<300`、失控保护 `>max(8000,源长×3)`），
> 放行等于把坏笔记写进库。现行规则见 `docs/decisions/DECISION-20260915-content-first-gate.md`。
> 本文件仅作历史记录，**勿据此实现**。

---

# DECISION-20260911 字数门禁重试放行（首次拦 → 重改 → 再犯即放）

**背景**：机械门禁字数硬阈值（<min×0.6 / >max×1.5）会无限拦截「干货密度已到头无法再精简」或「原文本身超长」的总结——重试生成大概率仍越界，卡死落盘浪费 AI token（实测 7 条被拦条目重写后仍越界）。

**决策**（用户定语义：不加豁免开关，而是首犯拦截要求重改；同 URL 重交仍纯字数违规即放行）：
1. `shared/gate_blockers.py`：`log_gate_block` 加 `action` 参数（默认 "blocked"，放行记 "bypassed_retry"）；新增 `count_blocks(url)` 跨滚动台账 `gate_blockers*.jsonl` 统计历史拦截次数（坏行跳过、空 URL 返 0）。
2. `save_summary_only` 门禁段：拦截后 issues 全部为字数类 且 `count_blocks>=1` → 放行落盘并记 bypassed_retry；否则照旧拦截。主标题 / 来源链接卫生问题**永不放行**。
3. 台账随滚动日志保留 7 天（LOG_KEEP_DAYS）= 重试窗口，过期重试语义自然失效。

**后果**：重改一次仍越界视为内容密度 / 原文长度的实情，放行且可观测（台账 bypassed_retry 事件）；卫生底线不让步。测试 `tests/test_note_mechanical_gate.py` 36 例（+TestCountBlocks 4、+TestRetryBypassGate 5），全量 849 passed 零回归。
