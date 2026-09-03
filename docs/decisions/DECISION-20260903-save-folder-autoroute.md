# 决策记录：save_summary_only 落盘 folder 为空时自动走统一路由器（L8）

> 日期：2026-09-03 | 关联：`articles/main.py` + `shared/routing.py` + DECISION-20260825（机械去重闸门）

## 现象
趋势浪子全量归档时，同为视频总结：一部分落在 `【我的总结】/作者/趋势浪子/`，
另一批 78 篇全部落进 `【待归类】`。内容、作者完全相同，唯一差别是调用方有没有传 `folder`。

## 根因（两扇门只装了一扇闸）
- `skill_main` 有 L7 修复（2026-08-23）：**手贴 URL 去抓取**的路径会自动 `resolve_folder`；
- 但「外层模型自己总结完回存」的路径（`summarized_content` → `save_summary_only`）
  **没有等价逻辑**，`folder` 是可选参数，靠调用方（执行会话）自觉计算传入。
- 监控管线由代码预计算 folder（永远传）；人工/批量会话全凭当次会话记性 → 必然分叉。

## 修复（代码门禁 > AI 记性，与 DECISION-20260825 同哲学）
`save_summary_only` 在 folder 为空时自动：
1. `author` 缺失则 `extract_author(url)` 兜底提取；
2. 走 `shared/routing.resolve_folder` 计算归档路径（作者命中监控账号 → 【监控】节点；
   非监控作者 → 【我的总结】/作者/<名>；scys URL → 生财有术/<领域>；全无信息 → 兜底收件箱，与旧行为一致）；
3. author 补进 tags（与 L7 一致）。
显式传 folder 的调用方（monitors / land_scys_batch / drain_pending / closeout_one）**行为不变**。

## 回归
`tests/test_save_folder_autoroute.py`：无 folder 按作者路由 / 显式 folder 不动 / 无信息落兜底。
