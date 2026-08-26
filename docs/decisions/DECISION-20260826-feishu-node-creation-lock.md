# DECISION-20260826-feishu-node-creation-lock
## 背景
多会话/多进程并发收尾不同系列课时（如分别落 价值投资 / 中国好公司 两个同作者系列课），飞书「容器节点」（作者文件夹 `【监控】/B站/<UP>`、系列容器 `<系列名>`）的创建走 `list-then-create` 非原子逻辑（`_ensure_child_node` / `ensure_series_node`）。

两个进程在同一瞬间都执行到「建 `<UP>` 文件夹」这一步、且都在任一方 create 成功之前完成 list → 双方 list 都返回「无」→ 双方都发出 create：
- 若飞书允许同名兄弟节点 → 出现**两个 `土斯` 文件夹**，两个系列各落进一个（内容都正确，但被劈成两堆，需手动合并）；
- 若飞书拒重名 → 后建方 create 报冲突返回空 token → 该系列落盘失败/回退收件箱。

**根因是 TOCTOU（time-of-check-to-time-of-use）竞态**，非设计产物。集节点本身不受影响——`save()` 是 upsert（父容器下已有同名标题先删旧再建新），天然幂等。

## 决策
给容器节点查-建加**跨进程原子锁 + 建失败重查复用兜底**（`articles/feishu.py`）：
- 新增 `_node_creation_lock(space, parent_token, title)`：用原子 `O_EXCL` 锁文件串行化「查→建」，同一 `(space, 父节点, 标题)` 同一时刻仅一个进程可持锁；加锁超时（60s）则放弃加锁、回落到「建失败→重查复用」兜底，不永久阻塞。
- `_ensure_child_node` / `ensure_series_node` 统一改走：
  `先查（无锁命中即返回）→ 持锁二次查（防持锁期间别进程已建）→ 建 → 建失败重查复用`。
  确保仅一个进程真正建节点，另一进程建失败后重查到对方节点直接复用，**绝不重复建、不落空**。
- 重构：抽出 `_find_child_token`（返回 token，含 sanitize 容错，与 `save()` 写入标题一致）；保留原 `_find_child_node`（返回 dict，供 `save()` upsert 删旧节点），并增强其 sanitize 匹配。消除编辑时误插入重名方法导致的「影子方法」bug。

## 不做什么
- 不动 `save()` 的 upsert 逻辑（集节点幂等已成立，无需改）。
- 不依赖「单会话顺序跑 / 错开起步」来规避——那只是运维建议，本门禁从代码层面根治，使多会话并发落不同系列课 = 完全安全。
- 作者文件夹**已存在**时此竞态本就关闭（建步骤退化为查→命中→复用，纯只读）；本门禁进一步消除「首建那一秒」的极小概率故障。

## 验证
- `tests/test_feishu_node_race.py`（6 个）：模拟并发抢建→建失败→重查复用、sanitize 标题匹配、锁获取/释放，全过。
- 既有 `feishu/save/series` 相关测试 25 passed，无回归。
- 提交 `a1f9855` 已 push。
