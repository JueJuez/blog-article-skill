# DECISION-20260908-series-deflow 系列课去流程化（六件套退役）

- **背景**：整季贪婪抓取曾致 76×117 请求暴增 + 412 风控；六件套（整季抓取 / fetched·done 索引 / pending_series 队列 / manifest 状态机 / 集数一致性校验 / series_state）维护成本高。
- **决策 D6**：单集与散文一样逐 URL 走五步管线 `summarize_series_episode`；防重统一走登记表 URL 键；不主动回溯旧集。
- **D8 账本口径**：点名才补——`scripts/backfill_series.py --series <名>`（登记表自动滤已总结，同集失败 3 次转人工），不做全量回溯账本。
- **件 6 位置修正**：集数一致性校验（`find_page_conflict`/`mark_done`）实际在 series_naming/series_state，非最初判断处；全部随退役删除。
- **连带整删**：`rescue_episode.py` / `dedup_feishu_series.py` / `RUNBOOK-series-rescue.md`（点名补齐已由 backfill_series.py 取代）。
- **教训**：跨文件并行 SearchReplace 有竞态——同文件必须串行；完成声明必须 grep 验证（本计划两次靠 grep 发现真残留）。
