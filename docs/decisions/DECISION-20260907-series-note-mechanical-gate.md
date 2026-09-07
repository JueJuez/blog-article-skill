# DECISION-20260907-series-note-mechanical-gate

## 背景
三层保障审计结论：`save_summary_only` 串式管道有机械质量门禁（去重→门禁→autoroute），而系列课单独实现的 `_save_series_note`（系列容器结构平铺模型不支持，故独立实现）输入是纯模型产出，却无任何机械校验——模型产出失控（写 H1 / 写来源链接行 / 篇幅崩塌）会直接落盘。去重有 `series_state.json` 补偿、路由有显式 folder，唯一真缺口是机械门禁。

## 决策
- `videos/main.py:_save_series_note`：函数最顶端（先于 `format_note_with_prompt`）插入 `verify_note_mechanical(content, note_type, source_url=url)`，失败抛 `ValueError("VERIFIER_FAILED: " + issues)`，message 约定与 `save_summary_only` 对齐，不落盘。
- 调用方接管：`monitors/apply_pending_series.py` 与 `scripts/series_maintenance.py` 的既有 try/except 自然接住（记 ❌ 继续、不误标 LANDED）；`videos/main.py` 系列循环调用点补局部 try——拦截时记 error result + continue，**不 mark_done**，下轮重跑自动重试本集。
- 全程 TDD：新增 `tests/test_series_note_gate.py` 5 用例（合规通过+落盘、H1/来源链接行/原文 URL/字数硬下限四类拦截且先于 formatter）；`test_patch_trio.py` 系列集成桩内容从 4 字升到 1600 字（过 general 硬下限）。全量 584 passed。

## 不做什么
- 不给 `_save_series_note` 加去重与 autoroute（`series_state.json` 与显式 folder 已覆盖，避免双重机制）。
- 门禁失败不走 `need_continue_summary` 降级——issues 文案已足够引导下轮模型自修复，raw 暂存机制不变。
