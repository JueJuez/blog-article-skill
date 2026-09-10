# 决策记录：旁路保存入口收编 autoroute + 测试隔离真实 dedup 索引（守卫补全）

> ⚠️ 2026-09-09 终局收口：四个旁路入口已改为一行委托 `save_summary_only`（不再逐入口补闸门），本篇守卫方案被取代，见 `DECISION-20260909-save-bypass-consolidation.md`；autoroute_folder() 辅助与 conftest 去重隔离仍有效。

> 日期：2026-09-05 | 关联：`articles/main.py` + `articles/_save_summary.py` + `tests/conftest.py` + DECISION-20260903（L8 autoroute）

## 现象（两个独立泄漏口）
- L8 修复后仍有旁路：`save_summarized_from_file` / `skill_continue_summary` / `async_save_summarized_from_file` /
  `articles/_save_summary.py` CLI 四个入口 folder 为空时直落【待归类】，只有 `save_summary_only` 有自动路由。
- `test_a4_frontmatter_tokens` 有 stub_output（拦截落盘）但无 tmp_dedup → `mark_summarized` 把模板内容哈希
  写进真实 `.cache/dedup.json`（机械证实：sha256[:16]=c2f5e077a0bae5ee，289 条中混入 1 条假去重键）。

## 根因
闸门装在单一 chokepoint（save_summary_only），旁路调用方漏装 → 靠调用方记性；
测试写真实缓存 → 靠每个测试记得挂 fixture。两者都违反「代码门禁 > AI 记性」。

## 修复
- 抽 `autoroute_folder()` 辅助（folder 空 → resolve_folder + author 补 tags），四个旁路入口统一调用；
  async 版改 `asyncio.to_thread` 委托同步版；CLI 加 `--folder` 显式出口；显式传 folder 的调用方行为不变。
- `tests/conftest.py` autouse `_isolate_dedup`：全测试库强制重定向 `dedup._INDEX_FILE/_CACHE_DIR` 到 tmp_path，
  「测试污染真实去重状态」结构上不可能。污染条目已清理（289→288）。

## 回归
`tests/test_save_folder_autoroute.py` 17 passed；实证 test_a4 前后真实索引 sha256 不变。
