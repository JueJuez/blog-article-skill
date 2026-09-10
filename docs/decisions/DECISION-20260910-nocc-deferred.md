# DECISION-20260910-nocc-deferred

> 承接 `DECISION-20260908-regen-gate-v3.md`（消费队列）。2026-09-10 加。

## 背景

重抓队列里 B站「无 CC 字幕且 ASR 兜底失败」的条目被当普通失败反复重抓，白烧限速配额与 AI token；其中不少视频其实已删除（`removed_video`），永远不会成功。

## 决策

- **无 CC 即暂缓不重试**：`--fetch` 探测到无字幕 → state 标 `deferred`，跳过且不占 `--limit` 配额（无 CC 是终态，重试无意义）。
- **加三分类让暂缓可回退**：`--classify-deferred`（默认 dry-run，`--apply` 落盘）重探 deferred 条目——探测到字幕=当初误标 → 清 `deferred`+`fails` 清零回队；探测 None 且 B站 view API 报 `-404`/`62002` → `removed_video`；探测 None 且视频在 → `no_cc_confirmed`（只暂缓不分类会把误标永久钉死）。
- **删除判定自包含**：`_video_removed` 自带 urllib 调 view API，**不复用 `videos.fetch` 私有函数**（asr.py 跨模块引私有名被重构改名断裂的教训，见 RULES.md §4.7）。
- **历史失败批量收敛**：`--defer-failed` 把 state 里已带无CC指纹的旧失败一次性暂缓（幂等，只统计新标记数）。

## 不做什么

- 不自动删除 deferred 条目（视频可能恢复上架，留 `no_cc_confirmed` 标签可复核）。
- 不新增状态文件——复用 `consume_state.json` 的 `deferred` / `classify` 字段。
- 不给 article 类条目做探测（无 CC 只针对视频）。
