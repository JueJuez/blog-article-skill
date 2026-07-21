# DECISION-20260720-sub-monitor
## 背景
用户要"持续订阅某账号→自动总结"。本轮先落地 B站UP主监控 + Obsidian 输出，每 12 小时一次。

## 决策
- 范围：先做 B站UP主监控；公众号暂缓（无公开 API，待 RSS/cookie 源）
- AI 引擎：用 WorkBuddy 内置 AI，由 automation 定时驱动；run.py 降级时顶层 AI 兜底总结
- 输出：Obsidian（OBSIDIAN_VAULT_PATH 已配），飞书后续再加
- 首次策略：仅处理最近 5 条，之后增量（monitors/state.json 去重）
- 调度：每 12 小时（automation `FREQ=HOURLY;INTERVAL=12`）
- 去重：本地 `monitors/state.json` 记录已处理 BV/专栏 ID
- 路由：视频→`videos/run.py`，专栏→`articles/run.py`

## 不做什么
- 不做公众号（本轮）
- 不接外部 AI key（本轮用内置 AI）
- 不回溯全部历史（仅最近 5 条）
