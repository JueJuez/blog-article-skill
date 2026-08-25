# DECISION-20260825-dedup-frontload-and-lock-release
## 背景
多 Agent 接力（非并发）消化待总结队列时：已总结条目会被重新总结（AI token 白花）并可能重复落飞书；scys `.lock` 进程死亡后残留，需手动删除。
## 决策
- 去重判断**尽量前置**，全部代码门禁、AI 只交总结（写不写由代码决定）：
  - ①入队时：`run.py _queue_pending_summary` 查 `dedup` 索引，已总结 URL 不入队；
  - ②派单前：`scripts/filter_pending.py` 清洗两队列（monitors 队列去 dedup 命中项；scys 队列去 `summarized:true` 与 dedup 命中项），编排方只对剩余条目派子 Agent；
  - ③落盘时：`save_summary_only` / `_save_summary.py` 查索引，命中返回 `skipped`（按成功出队），成功后 `mark_summarized` 登记；`force` / `--force` 为强制重写逃生舱。
- scys `.lock` 残留自动释放：锁内已写 PID；撞锁时 psutil 查持有进程存活，死→自动接管；PID 读不出或无 psutil → 锁龄 >6h 判残留接管。
## 不做什么
- 不改系列课落盘路径（飞书 upsert + `series_state` 已幂等）；不动 `summarize_and_save` 既有去重；不做飞书全树巡检脚本（upsert 已防同名重复）。
