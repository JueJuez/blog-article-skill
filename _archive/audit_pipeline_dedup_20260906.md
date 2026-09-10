# 全管线抓取去重审计（2026-09-06）

> 背景：趋势浪子补齐时 `fetch_up_range.py` 把同系列 117 个集 URL 各自喂给 `videos/run.py`，底层 `fetch_bilibili_series` 一拿到单集 URL 就贪婪抓整季 → 请求爆炸 + SIGSEGV。
> 修复（B 方法）：抓取层接 `series_state.is_fetched` 跳过已抓集（所有管线共享 `summarize_video` 入口，一次修好）；补齐层按 `ugc_season` 去重（同系列只喂一个代表 URL）。
> 本审计复核：项目内**所有涉及抓取的管线**是否都有"已抓则跳过"去重，确认无同类事故温床。

## 结论

**7 条管线全部有去重设计。B站系列课"入口单集触发整批抓取"类事故已在【抓取层(is_fetched) + 补齐层(ugc_season 去重)】双重堵死；其余 6 条均为"单 URL → 单内容"，无请求放大风险。**

## 逐管线核对

| # | 管线 | 触发点 | 去重 | 依据 | 放大风险 |
|---|------|--------|------|------|----------|
| 1 | B站补齐 `scripts/fetch_up_range.py` | `main()` → `videos/run.py` 批处理 | ✅ 有 | `done_urls`(`_is_really_done`) + `ugc_season` 代表去重 + `is_fetched` | 关 `--no-dedup-series` 才有 |
| 2 | B站监控 `monitors/run.py` + `bilibili.py` | `discover` → `_handle_bilibili_series` | ✅ 有 | `seen` + 时间窗 + 下游 `is_fetched` + 总结级 `get_pending` | 受控（每轮新集少） |
| 3 | 公众号/微信 `monitors/run.py` | `apply_summaries` → `fetch_web_content` | ✅ 有 | `seen` + 入队前 `is_summarized`/队列内去重 + 跨源 `find_cross_duplicate` | 无 |
| 4 | 生财有术 `scripts/scys_batch_fetch.py` | `_run_once` → `fetch_article` | ✅ 有 | `state.json` `done_ids` + 进程锁 `.lock` | 无（递归站内引用未查 done_ids，极轻量） |
| 5 | 文章总结 `articles/run.py` | `run_batch` / `skill_main` | ✅ 有 | `is_summarized`(dedup.py)，`--force` 强破 | 无 |
| 6 | 开源项目 `tools/project_import/` | `phase1_extract` → `_process_repo` | ✅ 有 | `batch_deduplicate` + `filter_imported`(imported.txt) + 本地 `os.path.exists` | 无 |
| 7 | 单集救回 `videos/rescue_episode.py` | `rescue` → `_step_fetch_subtitle` | ◐ 部分 | 幂等覆盖同集 raw，无"已抓跳过"短路（设计如此） | 无（单集→单集） |

## 残留风险（非紧急，已处理/记录）

1. **`fetch_up_range.py --no-dedup-series` 逃生开关**（L247）：关闭即复现整季重抓。已加⚠️告警，提醒仅调试临时用。
2. **B站监控 `bilibili.py:discover` 不预查 `series_state`**：完全依赖下游 `is_fetched`；若未来 summarize 路径传入 `force=True` 会整季重抓（当前监控默认不带 force，安全，属隐含信任）。
3. **`rescue_episode.py` 无"已抓跳过"短路**：手动救回单集，幂等覆盖可接受，仅作记录。
4. **scys `fetch_article` 站内引用递归(L483-497) 未查 `done_ids`**：极轻量重复，不放大请求。

## 附：本次修复落点

- `shared/series_state.py`：新增 `mark_fetched/is_fetched`（与 `done` 解耦的"已抓取"持久索引）。
- `shared/sanitize.py`：文件名安全化单一真源（去重 base 精确匹配依赖它）。
- `videos/fetch.py`：`_fetch_series_entries` 接 `series_title/force` + 抓取层跳过；`fetch_bilibili_series(force=)` 透传。
- `scripts/fetch_up_range.py`：组批前 `_dedupe_series` 按 `ugc_season` 归并 + 代表成功覆盖集标记完成 + 关开关告警。
- 实测：117 待抓 → 归并后只抓 27 个代表 URL（覆盖 90 个同系列集）。
