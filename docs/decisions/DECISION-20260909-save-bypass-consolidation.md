# 决策记录：保存入口终局收口 + publish_time 全链路透传（含 12 篇存量回补）

> 日期：2026-09-09 | 关联：DECISION-20260905-bypass-autoroute-guard（守卫方案被取代）| 测试 `tests/test_bypass_consolidation.py`

## 背景
L8/L9 逐个给旁路入口补闸门仍是「打补丁」：`save_summarized_from_file`(+async) / `run.py --summarized` / `_save_summary.py` CLI 依旧缺质量门禁与 publish_time 透传——publish_time=0 时发布时间三处表达（文件名日期前缀 / 正文发布时间行 / 新鲜度标签）全部静默失效。

## 决策
- **收口**：三个旁路入口全部改为一行委托 `save_summary_only`，去重/质量门禁/autoroute/publish_time 四件事只在 `save_summary_only` 一处维护（「代码门禁 > AI 记性」终局形态，取代逐入口补闸门）。
- **12 篇存量回补**：migrate done 队列中发布时间缺失/文件名 `20260909_` 占位的 12 篇，按来源真实发布时间重写三处表达（文件名日期前缀 + 正文「**发布时间**」行 + `#更早` 标签），registry filename 同步；抽查 12/12 通过。

## 结果
全量回归 742 passed。发布时间来源优先级：显式传参 > B站 pubdate / 公众号 feed / 文章 meta 提取，取不到仍用处理时间（惯例不变）。
