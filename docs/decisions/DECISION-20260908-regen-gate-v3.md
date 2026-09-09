# DECISION-20260908 · 迁移质量门禁转 v3：检测出清单、重抓替换

> 承接：`DECISION-20260907-cleanup-resolutions-and-v2-handoff.md`。用户拍板（2026-09-08）：放弃 v2 正文修复管线（异链接 AI 裁决/作者择优/API 改名全不需要），改为**扫异常→出清单→重抓替换**。

## 拍板

1. **串位=全部重抓**：仅集数串位、正文正常的文件也重抓（realign/H1 改名机制删除——H1 不可靠，重抓产物文件名天然规范）。
2. **替换顺序**：重抓成功落盘→校验→才删旧文；失败旧文原地保留、停留清单持续重试（微信代理不可达则不清理，恢复后再处理）。
3. **B站重传匹配**：原视频删除重传后 URL（BV 号）会变，不能只凭 URL 比较——队列条目带 `old_titles`，供「标题+内容相似度」匹配（匹配逻辑等真实触发再写，YAGNI）。

## 实现（`scripts/migrate_gate.py` + `tests/test_migrate_gate.py` 70 例）

五步：dedup → fm_sync → **scan**（5 类异常 + 链接指纹分组：同 main_url 文件聚组，正文指纹识别不了的盲区）→ **queue**（url/kind/folder_hint/old_paths/old_titles，只生成不执行）→ verify。清单 JSON+MD 写 vault 同级 `_migrate_gate_archive/`。

## 首扫结果（2026-09-08 dry-run，全 vault 652 文件）

异常 457 文件：来源块重复 103 / 残留行 291 / 缺fm链接 175 / 多链接 26 / 集数串位 14；链接重复组 18 组（86 文件，17 组为B站，单链最多 ×16）；重抓队列 426 条（article 258 / bili_video 167 / manual_no_url 1）。

## 消费实现（2026-09-09，PLAN-20260908 阶段 5）

`scripts/consume_migrate_queue.py`（测试 `tests/test_consume_migrate_queue.py` 20 例）：`--plan/--fetch/--clean/--cleanup-old` 四命令。消费循环 = fetch 入队（prompt 预计算、B站标题 difflib ratio≥0.55 匹配、失败 3 次转人工、412 熔断）→ 执行模型派子 Agent 总结落盘（save_summary 自动登记）→ clean 出队 → 全清后 cleanup-old 删旧文（登记命中才删）。风险 4「匹配逻辑等真实触发再写」已兑现：首轮即触发 6 例 `url_changed`（B站重传 URL 指向不同视频），标题匹配拦截按设计工作。顺带修复 `dedup.mark_summarized` 漏传 title 的缺口（title-only 登记此前被静默丢弃）。
