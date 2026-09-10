# 决策记录：系列课（B站多P）为何没被自动总结

> 日期：2026-08-12 | 关联：`monitors/run.py` + `videos/main.py` + `apply_pending_series.py`
> 现象：订阅的 UP 主发了 75 集《中国好公司》系列课，被抓成 raw 暂存 `notes/中国好公司/`，
> 但**没有进入自动总结闭环**，75 篇一直以 `*_raw.md` 躺平，直到人工介入才发现。

## 结论（一句话）
系列课走的是「降级 raw → 等外层 Agent 总结」的另一条链路，但**降级返回里漏了 `need_continue_summary` 信号**，
导致 monitors 的落盘闭环（`apply_pending_series.py`）完全不知道「还有 75 集待总结」——这是主断点。
叠加两处质量缺陷，使半自动兜底也失效。

## 链路断点拆解（3 个）

### 断点 1（主）：`_handle_bilibili_series` 降级返回缺 `need_continue_summary`
- `videos/main.py` 的系列处理函数，在无外部 AI（FORCE_AGENT_MODE=1）时把每集字幕写成
  `notes/<系列名>/*_raw.md`，并期望由 monitors 的外层 Agent 来总结。
- 但它返回的 dict **没有 `need_continue_summary=True` 字段**，而 `monitors/apply_pending_series.py`
  的闭环逻辑只认单篇 `pending_summaries.json` 里的 `_raw_*.md`（顶层文件），
  **根本不扫 `notes/<系列名>/` 子目录**。
- 结果：75 个 raw 既没进 `pending_summaries.json` 队列，闭合信号也为空 → 静默孤儿化。

### 断点 2（次）：缺 `pending_series.json` 这一「系列级」待办登记
- 单篇有 `pending_summaries.json`，系列课却没有对等结构。run.py 视频分支只调
  `_queue_pending_summary`，系列降级时无人把它登记进一个可被 drain 的队列。
- 修复：新增 `pending_series.json` + `_queue_pending_series()`，并在视频分支判断
  `res.get("degraded_any") and res.get("degraded_raws")` 时改走系列登记。

### 断点 3（质量）：`_save_series_note` 漏加元数据头
- 原 `format_note_with_prompt(..., add_metadata=False)` → 落盘的 75 篇缺 `#标签` /
  `**作者**` / `**来源链接**` 头，不符合模板规范。
- 修复：`add_metadata=True`（已改）。意味着旧节点需重落地才能带头部。

## 为什么半自动兜底也没兜住
- 设计意图：降级时返回 `need_continue_summary` → run.py 末尾打印 `NEED_AGENT_SERIES_SUMMARY`
  → 执行模型读队列、派子 Agent 总结、落盘。
- 因断点 1，信号从未产生，整个「外层总结」步骤被跳过。不是子 Agent 偷懒，是**上游没发令**。

## 修复清单（已落地代码）
1. `videos/main.py`
   - `_save_series_note`：`add_metadata=False` → `True`（补元数据头）。
   - `_handle_bilibili_series` 降级返回补齐 `need_continue_summary / series_title /
     series_dir / url / author / degraded_raws`（让 monitors 感知系列待总结）。
2. `monitors/run.py`
   - 新增 `PENDING_SERIES_PATH = monitors/pending_series.json`。
   - 视频分支：降级且含 raws 时改调 `_queue_pending_series()`（去重同系列）。
   - 末尾新增 `NEED_AGENT_SERIES_SUMMARY` 打印。
3. `monitors/apply_pending_series.py`（新建）
   - 读 `pending_series.json`，串行调 `_save_series_note` 落飞书，重生成总览，保留未处理的 raw 条目。
4. `articles/feishu.py`（**本次补充的根因修复**）
   - `_is_rate_limit` 只认 `99991400`，漏掉飞书批量建/删常见的 `131001`
     （"service temporarily unavailable, please retry"）。该瞬错未被重试，
     导致一次「删旧节点重落地」批量操作半途而废（约 50 个 delete 失败）。
   - 已把 `131001` 及 "service temporarily unavailable" 纳入可重试瞬错，
     配合 `save` / `delete_node` 内置指数退避，批量操作现在能自愈。

## 根因归类（5 Whys 摘要）
- **为什么 75 集没总结？** → 系列降级没发「待总结」信号。
- **为什么没发信号？** → `_handle_bilibili_series` 返回结构漏 `need_continue_summary`。
- **为什么漏字段没被早早发现？** → 单篇链路（`pending_summaries`）和系列链路两套机制，
  系列链路缺对应「待办登记 + 闭合信号」契约，且缺测试覆盖该分支。
- **为什么补修复时又出事？** → 飞书批量删除命中 `131001` 瞬错，重试逻辑未覆盖该码 → 半删半留。
- **结构性根因**：「降级→外层总结」这条异步链路**没有任何常驻的、确定性的信号闸门**——
  依赖函数返回字段是否齐全，而该字段既无 schema 校验也无单测。修复方向应是把
  `need_continue_summary` 提升为落盘闭环的硬契约（缺失即告警/失败），而非靠人肉发现。

## 后续建议（机制化门禁）
- 给 `_handle_bilibili_series` 的返回结构加断言/契约测试：降级时必须含 `need_continue_summary`。
- `apply_pending.py` 与 `apply_pending_series.py` 合并或共享一个「待办扫描器」，
  避免单篇/系列两套逻辑再次分叉。
- 批量飞书操作加「最终一致性校验」：删/建后 `list_children` 复核数量，不符则报警（而非静默 "完成"）。
