# PLAN-20260907 迁移收尾：14 篇补拉与降级总结

> **触发词「迁移收尾」即读本文件并按序执行。**
> 用户拍板（2026-09-07）：飞书→Obsidian 迁移剩余失败篇目继续尝试迁移；迁移仍失败则通过原文重新总结落盘。
> 背景决策：`docs/decisions/DECISION-20260907-migration-tail-and-backup-keep.md`

## 现状快照（2026-09-07）

- `scripts/feishu_to_obsidian_log.json`：终态 909 / 923（written 83 + synced_title 172 + skip_same_title 585 + skip_path 69），**非终态 `fetch_fail:cli_error` = 14 篇**。
- 失败原因：lark-cli(user token) 瞬时 cli_error（频限窗口）。迁移脚本单轮不重试 cli_error，靠「自动多轮」冷却 90s 重跑（`MIGRATE_ROUND` 环境变量传轮次）；每 20 篇 execv 重启刷新 lark-cli 状态（14 篇不会触发）。
- 迁移工具重跑语义（已核实源码 `feishu_to_obsidian.py:387-411`）：`fetch_fail:*` 不在终态 → 重跑自动补拉失败篇；已终态且目标文件存在 → 跳过。

## ⚠️ 严禁不带参数直接正式跑

dry-run 实测（2026-09-07）：`python scripts/feishu_to_obsidian.py --dry` 报「待处理 748」而非 14。原因：skip_same_title(585)/synced_title(172) 条目虽终态，但 `exists_in_dir(*target_path(it)[:2])` 检查的是 tree 目标路径，实际文件是按 URL 主键命中在 vault 别处的 → 不带参数跑会重新 fetch ~734 篇（频限 10s/篇 ≈ 2h+）并可能重复写文件。

**正确做法：14-token 子集补拉**（零代码改动）：

```powershell
# 1. 备份
Copy-Item scripts\_feishu_tree.json scripts\_feishu_tree.json.bak
# 2. 用 python 过滤出仅含下方 14 个 token 的子集，覆盖写回 scripts/_feishu_tree.json
# 3. 前台跑（勿后台；lark-cli 需用户态可用）
python scripts/feishu_to_obsidian.py
# 4. 核对后立即恢复
Move-Item scripts\_feishu_tree.json.bak scripts\_feishu_tree.json -Force
```

## 14 篇清单（token → 标题）

| token | 标题 |
|---|---|
| I27LwVnsrioapbkxNVxcPcGJnXb | 第43集_【深度】比亚迪：460万辆和7000块利润——一家电池公司顺手造了个车，十个抽屉逐个打开 |
| FZgQwjMZFibjTikUIfXcJTginSd | 第44集_【288会员定制节目】阿尔卑斯的小溪怎么成了世界制药的代工厂 |
| QQdzwR1mRioGr3k2LoFcQMEMnwh | 第03集_【小猪仔讲故事】读书会：花荣《操盘手》上——掐断交通线的人 |
| DuTsw9rMEi8GLXkFLFccazuonIf | 第04集_【价投小猪仔】读书会操盘手番外——章沁晖 |
| OEAZw0M4dinAvLkFpsMcPUFmnFp | 第07集_【价投小猪仔】经典电视剧《坐庄》回顾（中）——钱，是害人的祸根 |
| MfTOwa2p5izJYXkCesWc5lEBnoe | 第04集_【价投小小猪】【小小猪讲故事】【睡前故事】药明康德五年一梦——叙事、机构与泡沫 |
| YCj4wuOh8ikrCwkIxMPc9c38nZc | 第19集_【价投小猪仔】我讨厌_老登股_和_小登股_这种说法——标签，让人放弃思考 |
| TwTewmdTNiQYvIkC8SNcG8l7nhK | 全网最全的 Seedance2.0 实测案例库发布 |
| T304wOpxAi4SdzkNr5EcnRxMnPb | AIGCRank：2025年度AI网站总榜发布 |
| TLsRwknBJil8xPkd8svcACxFnvd | 我 Vibe Coding 一周，做了个桌面 Agent（WorkAny 开发复盘） |
| SbScwqggLiqRIskHLoNcPAxAnLc | 外链到底有什么作用？是用来提升权重还是来获取访问量，一定要分清楚 |
| UGpZwwSABiMTabkipxDcthaunte | 加入哥飞社群一年，终于做出一个月近 900K 流量的网站 |
| F0pLw2MUuiUYJ0k3JtVcJwewnsc | 新词新站，快就是优势，快人一步，那就步步都快 |
| VlAzwX3omilj0qkiL8wcJMwLnPd | 一边上班一边上站还能月入万刀？哥飞朋友们的2025年终总结大全 |

## 执行步骤

1. **子集补拉**（上方代码块）：跑完解析 log.json 统计 fetch_fail 剩余数；对 `written` 条目抽验 vault 文件真实存在。
2. **仍失败篇目 → 原文重总结（一律走入口，不手搓脚本）**：
   - 原文获取：`from scripts.feishu_to_obsidian import fetch_markdown` → `content, ok, why, detail = fetch_markdown(token)` 拿 markdown 全文（内部已处理 lark-cli 绝对路径与 429 退避）。
   - 总结落盘：`from articles import skill_main; skill_main({"content": <原文文本>})` → `FORCE_AGENT_MODE` 下返回 `need_continue_summary` + 预计算 prompt → 执行模型按 prompt 总结 → `save_summary_only` 落盘（默认本地 Obsidian，见 RULES.md §3.0）。
   - 落盘路由走 skill_main 默认（`【我的总结】/…`），不复刻迁移目标路径。
3. **收尾**：`python -m pytest tests/ -q` 全绿；向用户汇报迁移成功数 / 降级总结数。

## 护栏（删除类操作前必读）

- `notes/_scraped/_backup_趋势浪子_20260906_034919/` 是 8 个趋势浪子系列课**唯一副本**（vault `【我的总结】\趋势浪子` 为空目录、`notes/` 无系列正本）——**禁止删除**，处置需用户确认。
- `scripts/_feishu_tree.json` 是迁移工具输入，操作完必须恢复原样。
- `migrate_watchdog_status.json` 若仍存在，属看护残留状态文件，可留可删（非数据）。
