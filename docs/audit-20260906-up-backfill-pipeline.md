# UP 补齐管道排查报告（2026-09-06）

排查对象：`scripts/list_up_videos.py` → `scripts/fetch_up_range.py` → `scripts/filter_pending.py`
→ 子 Agent 消费队列，及系列课侧 `videos/fetch.py` / `videos/main.py` / `shared/series_state.py`。
证据来源：全量代码走读 + `notes/_scraped/趋势浪子_*` 六次真实运行日志 + `monitors/series_state.json` / `pending_series.json` / `pending_summaries.json` 实测。

---

## 一、管道架构现状（哪些是对的）

| 环节 | 机制 | 状态 |
|---|---|---|
| 列表抓取 | `list_up_videos.py` WBI 签名翻页，落 `notes/_scraped/bili_<uid>_videos.json`，按发布时间升序编号 | ✅ 正常，带缓存 |
| 已补过滤 | `fetch_up_range.py` 用 `<作者>_fetch_results.json` 的 `rc=0 且 stdout_tail>100 字` 判完成 | ⚠️ 只认这一个文件（见 V1/V4） |
| 批量执行 | 每 8 条一个 `videos/run.py --batch-file` 子进程；条间 15~30s；100 条/h 滑动窗口 | ✅ 骨架健康（本次全同环境复现成功） |
| 请求级限流 | 170 请求/h 挂在 `_bili_urlopen` 单一咽喉（所有 B站管线共用）；412 后自动 ×0.7 降档 | ✅ 设计正确 |
| 风控熔断 | 连续 3 条 412 → 整批熔断 exit 87；单条 412 记入 `<作者>_risk_skip.json` 不再重抓 | ✅ 正常 |
| 系列课抓取 | 代表集触发整季抓取；`series_state.fetched` 跨进程记录已抓集，重复触发时跳过网络请求 | ✅ 抓取层去重成立 |
| 系列课总结增量 | `series_state.done`（`get_pending`）只列未总结集 | ✅ 总结层去重成立 |

## 二、确认的漏洞（按严重度排序）

### V1（高危·实锤）covered 标记过不了自己定的完成门槛 → 被覆盖集每轮重抓

`fetch_up_range.py` L513 给「被代表集覆盖的同系列集」写的完成标记 `stdout_tail` 实测只有 **54 字符**，
而完成判定 `_is_really_done`（L148）要求 `> 100 字符`。结果：

- 每次重跑，covered 集全部重新进入 todo；
- 重新预扫（每条 1 次 view 请求）→ 重选代表 → 代表触发整季管线；
- 更糟：整季所有集已 `mark_fetched` → `entries=[]` → `fetch_bilibili_series` 返回 None →
  **回退单视频路径，把这一集当独立视频再抓一遍字幕（view+dm_view+subtitle 3 次请求）**；
- dedup 索引里系列集的单集 URL 从未被登记（系列落盘 URL 是系列链接，不是单集链接）→
  `enqueue_pending` 拦不住 → **以单篇身份入队 → 重复总结 + 落错目录**（`【我的总结】/作者/<名>`，
  而不是系列容器）。我在排查中用真实条目复现了这个回退路径（产生了孤儿 raw，已清理）。

### V2（高危·实锤）系列课「抓了但没人总结」：73 集悬挂，无任何队列接管

实测数据：`趋势浪子交易体系篇2025` 在 `series_state.json` 里 **fetched=73、done=0**，
`notes/趋势浪子交易体系篇2025/` 下堆着 73 个 `_raw.md`；而 `monitors/pending_series.json` = **0 条**、
`pending_summaries.json` = **0 条**。

根因：FORCE_AGENT_MODE=1 下系列课总结必然降级（raw 落 `notes/<系列>/`），
`videos/run.py --batch-file` 只把 series 标志记进 rec，**不透传 `degraded_raws`**；
`fetch_up_range.py` 对 series 结果只打日志「不入单篇队列」，也不写 `pending_series.json`。
`monitors/run.py` 的系列降级队列只在监控路径生效。于是补齐路径的系列 raw 变成三不管：
字幕抓了（fetched 记了）→ 永远没人总结（done=0）→ 而且因为 fetched 已标记，
下次想补总结时抓取层还会跳过这些集。这就是「补了一些、还有一些未补」的直接来源。

### V3（中危）系列去重预扫 = 117 连发 view 请求，无视密度与 412

`fetch_up_range.py:_dedupe_series` 对每个 todo 条目无条件打一次 `view` 请求：
- 0903 实测 194 请求/分钟、min_gap 0.04s 的「机器脉冲」就是这类形态（该次 6 条系列入口打了 112 请求）；
- 预扫发生在「已总结过滤」**之前**——已补过的条目也白白预扫；
- 异常被吞（`except: info=None`）→ 412 时整组误判 standalone → 回退单集抓取，请求更碎。

### V4（中危）「已补过滤」只认 fetch_results.json，其他路径补过的全部重抓

todo 过滤 = `fetch_results.json` ∪ `risk_skip`。凡以前通过监控、早期版本、或换了 author 名/
`--refresh` 重建列表后的补齐记录，都不在 fetch_results 里 → 已总结视频仍逐条重抓字幕（3 请求/条）。
`articles/dedup.batch_is_summarized`（批量查 URL 索引）已存在却没用在 todo 计算里——
dedup 检查只发生在抓取**之后**的入队环节，只省落盘、不省请求。
另有隐患：fetch_results 里 118 条旧记录的 stdout_tail 是字幕正文（旧版行为），
它们能过完成门槛，但这些视频是否有对应笔记需以 dedup 索引二次确认。

### V5（低）多 section 系列的页码重置

`fetch_bilibili_series` 对 ugc_season 每个 section 独立 `enumerate(...,1)` 编集号：
多 section 系列会出现两个「第01集」→ base 冲突 → series_state 键互相污染 + 文件名覆盖。
趋势浪子现有系列多为单 section 未引爆，属埋雷。

### V6（观察）子进程秒退不熔断，父进程空转烧完全部批次

012229（15 批）与 024723（4 批）两次运行，子进程**全部秒退**：rc=1、stdout/stderr 全空、0 请求。
当前环境以完全相同的 env+代码复现**成功**（rc=0，正常出 BATCH_RESULTS）——
判定为前会话迭代代码期间的瞬态环境问题，不是现行代码 bug。
但它暴露韧性缺陷：连续多批「未解析到结果且 0 请求」时父进程不熔断，
照常睡 22s 批间延迟把 15 个批次全烧完（323 秒空转）。

## 三、修复建议（最小改动集）

1. **V1**：covered 标记的 `stdout_tail` 补足长度（垫一段 JSON 说明 >100 字符），或让
   `_is_really_done` 对含 `covered_by` 的记录放行；同时系列集入 dedup 索引（落盘时按单集 URL mark）。
2. **V2**：`fetch_up_range.py` 末尾扫描各系列目录的 `*_raw.md`，把新增者写入
   `monitors/pending_series.json`（与监控路径同 schema），复用既有 `apply_pending_series.py` 落地——
   悬挂的 73 集可立即由此接管。
3. **V3**：todo 计算先并上 `dedup.batch_is_summarized` 结果（顺带修 V4），预扫只对剩余条目做；
   预扫循环加 0.5~1s 间隔 + 412 即熔断（复用 risk_412_hit）。
4. **V4**：`done_urls` 扩为 `fetch_results ∪ dedup 索引` 的并集。
5. **V5**：ugc_season 集号改为跨 section 全局递增。
6. **V6**：连续 ≥2 批「未解析到结果且 0 请求」→ 熔断并报「子进程环境异常」，别烧完。

## 四、当前待办（数据层面）

- `趋势浪子交易体系篇2025`：73 集 raw 待总结（修 V2 后走 apply_pending_series 即可）。
- `趋势浪子` 全量 242 条中：fetch_results 实际成功仅 7 条（0903 六个系列 + 012156 一条），
  117 条 todo 因子进程秒退未真正抓取（rc=1 记录会自动重抓，无永久损伤）；
  118 条 rc=0 旧记录（stdout 为字幕正文）建议用 dedup 索引核一遍是否真有笔记。
## 五、本地保留机制决策（2026-09-06，用户委托 AI 定）

- **记录 = 真源，永不清理**：dedup 索引（`.cache/dedup.json`）+ `series_state` + manifest
  负责「是否已总结」的判断。文件删了记录还在，补齐时 dedup 命中即跳过（V4 修复后
  在抓取前过滤），不会重复总结。
- **Obsidian vault = 本地保留层**：vault 本身就是本地 markdown 文件（`OBSIDIAN_VAULT_PATH`），
  "本地保留"天然成立；`audit_sync.py` 做 vault→飞书单向镜像（本地→云）。
- **清理机制**：vault 文件可按 90 天等周期清理，但**只报告不自动删**
  （`scripts/vault_lifecycle.py old-report --days 90`）。
- **对账兜底**：`scripts/vault_lifecycle.py reconcile`——记录在但 vault 文件丢失 →
  清记录 → 下次补齐自动重抓重总结（即用户语义"有就直接上传，没有就重新总结再上传"）。
  ⚠️ `--apply` 必须带 `--scope-substr <关键词>`：索引里混着飞书时代公众号记录
  （filename 本就不在 vault，实测 128 条），无范围清除会误清导致整批重总结。
- **重置工具**：`scripts/reset_up_backfill.py --uid <uid> --author <名> [--apply]`——
  用户清空某 UP 的 Obsidian 内容后，用它成套清掉 fetch_results/dedup/series_state/
  本地系列文件夹（全部迁移到 `notes/_scraped/_backup_<作者>_<时间戳>/`，可回滚）。
  2026-09-06 已对趋势浪子执行：清 156 条 dedup、8 个系列状态与本地文件夹、14 个产物文件。

## 六、本次已实施的修复（对应 §三）

- V1：covered 凭证改结构化 JSON，`_is_really_done` 按 `covered_by` 字段识别（不再靠长度）。
- V2：`videos/run.py` 批量模式透传 `series_title/series_dir/degraded_raws`；
  `fetch_up_range.py` 新增 `enqueue_pending_series`（与 monitors 同 schema 同合并语义），
  系列降级 raw 一律入 `pending_series.json`，由 `apply_pending_series.py` 接管。
- V3：todo 先经 dedup 批量过滤再做系列预扫；预扫加 0.5~1.5s 抖动，412 即熔断（exit 87）。
- V4：`done_urls` = fetch_results ∪ dedup 索引批量命中。
- V5：ugc_season 跨 section 全局集号。
- V6：连续 2 批「子进程无输出秒退且 0 请求」→ 判环境异常熔断（exit 88），不再空转烧批次。
- 测试：`tests/test_fetch_up_range_enqueue.py` + `tests/test_routing_series.py` 12/12 通过；
  V1/V2 新逻辑冒烟测试通过。
