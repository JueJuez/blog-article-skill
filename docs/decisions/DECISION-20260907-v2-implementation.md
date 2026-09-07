# DECISION-20260907 · V2 实施落地记录（任务1-5 + 三生成侧补丁）

> 依据：`DECISION-20260907-cleanup-resolutions-and-v2-handoff.md`（拍板）+ `PLAN-20260906-sync-ledger.md`（任务1-5）。
> 全程 TDD：新增 `tests/test_sync_push.py` + `tests/test_patch_trio.py` 共 46 例；全量回归 549 passed（基线 503 + 新增 46），零回归。

## 落地清单

1. **dedup.py 登记表扩展**：`is_summarized`/`batch_is_summarized` 收录文章+视频+系列三源；P0-2 索引迁出 `.cache`（随 vault 走）；P0-3 跨进程文件锁。
2. **main.py 出口收敛**：正常分支补 mark；YAML frontmatter 停产（不再写入笔记头）。
3. **feishu.py 写入端**：`_strip_frontmatter` 接入 save/save_async（镜像端不再渲染杂物行）。
4. **vault_lifecycle.py gc**：B 方案——只清中间产物（raw/transcripts），成品淘汰交用户手动；`GC_NONREGEN_HOSTS` 白名单域（缺省 mp.weixin.qq.com）永久保留。
5. **audit_sync 对账整合**：reconcile/push 以 Obsidian 真源重建。
6. **三生成侧补丁**（`tests/test_patch_trio.py`）：①视频入口闸门——`_handle_single_video` 开头 `is_summarized(url)`，重复链接直接 skipped（`force=True` 绕过）；②日期前缀强制——`generate_filename` 产出 `YYYYMMDD_【分类】标题[:50].md`；③系列集数一致性——`shared/series_naming.find_page_conflict` 同页码不同标题成稿即跳过（保留原标题，force 绕不过），直连路径与 drain 均回写 `mark_done`。

## 范围外修复

- `articles/obsidian.py`：补 `ensure_folder_path` / `ensure_series_node` + `save(parent_token=)`——修复 `_save_series_note` Obsidian 路由分支 AttributeError（系列容器此前对 Obsidian 不可落）。

## 遗留待办

- scys 20 条 `no_date_found` CDP 登录态重抓补日期前缀（**执行前需用户知情同意**，CDP 会先关用户 Chrome）。
