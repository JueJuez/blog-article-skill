# DECISION-20260907 迁移收尾交接 + 趋势浪子备份保留

**背景**：日志膨胀审计收尾。审计报告「待迁移 7」为过期快照——实测 `scripts/feishu_to_obsidian_log.json` 非终态 = **14 篇** `fetch_fail:cli_error`（价投小猪仔读书会 7 篇 + 生财/自媒体 7 篇）；终态 909/923。

**拍板（2026-09-07）**：
1. 34 项纯垃圾已删（scripts 迁移残留 26 / monitors 旧轮询文件 2 / notes/_scraped 测试日志 4 / .workbuddy 1 / C 盘 CDP 旧启动日志 1）；`_feishu_tree.json`（迁移输入）与 `__pycache__` 保留。
2. 14 篇继续尝试迁移；仍失败的走 `skill_main` 原文重总结落盘。

**⚠️ 护栏（防后续会话踩坑）**：
1. `notes/_scraped/_backup_趋势浪子_20260906_034919/` 是 8 个趋势浪子系列课**唯一副本**（vault `【我的总结】\趋势浪子` 为空目录、`notes/` 无系列正本）——**禁止当垃圾删除**，处置需用户确认。
2. `feishu_to_obsidian.py` 不带参数裸跑会扫出「待处理 748」（skip_same_title/synced_title 条目的 `exists_in_dir(target_path)` 检查与文件实际位置错位），触发 ~734 篇重抓——补拉必须用 14-token 子集 TREE_JSON。

**交接**：`docs/plans/PLAN-20260907-migration-finish.md`（触发词「迁移收尾」）。
