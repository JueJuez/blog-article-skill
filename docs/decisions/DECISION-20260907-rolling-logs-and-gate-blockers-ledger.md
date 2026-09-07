# DECISION-20260907 滚动日志统一 + 门禁拦截台账

**背景**：门禁拦截事件仅 apply 路径持久化（直连/队列路径无记录，无法区分「未消费」与「被拦」）；全项目 9 处 append 日志（系列进度/扫码轮询/CDP 启动/看护心跳/迁移日志等）单文件无限膨胀。

**决策**：
1. 新增 `shared/rolling_log.py`：按天切文件（`foo.YYYYMMDD.log`）+ 写入时惰性清理过期文件，保留天数 `LOG_KEEP_DAYS`（默认 7）；仅删文件名内嵌合法 8 位日期的文件，防误删。
2. 新增 `shared/gate_blockers.py`：拦截事件 JSONL 台账 `monitors/gate_blockers.YYYYMMDD.jsonl`，接线于全路径汇合处 2 点（`save_summary_only` source=queue、`_save_series_note` source=series），记录失败静默不抛。
3. 9 处膨胀点改走滚动写入（daemon/Chrome fd 固定启动当天文件，重启自然切天）；`status_store` 旧 run 分片按 mtime GC（每小时限速），与 `LOG_KEEP_DAYS` 同 env 同默认、不 import shared 保持可独立引用。
4. 数据类文件不动（tracker 去重索引 / vault journal / state.json 已有自修剪 / fetch_up_range 已有 TTL GC）。

**后果**：排障入口统一为「当日日志」；拦截可观测缺口补齐；回归 613 passed（584+21+8）。旧日志文件无需迁移，过期自然清理。

## 增补（2026-09-07）：接线测试污染真实台账，已修
使用状态审计发现：`test_note_mechanical_gate` / `test_series_note_gate` 走真实 `log_gate_block`，把 7 条假拦截记录（g.com / example.com）写进了真实 `monitors/gate_blockers.20260907.jsonl`。修复照 `conftest._isolate_dedup`（2026-09-05）守卫先例：新增 autouse fixture `_isolate_gate_blockers` 重定向 `GATE_BLOCKERS_BASE` 到 tmp_path，删除被污染的当日文件；回归 613 passed 后确认 monitors/ 下不再生成台账文件——「测试污染真实台账」结构上不可能。
