# PLAN-20260828 · 监控流水线并行化 + 状态 Ledger 改造

> 状态：**方案已定稿，新会话按 P0~P3 实施**（已按第一性原理审计修订，消除内部矛盾 + 补代理 500 误弹窗修复）
> 目标消费者：状态 ledger 主要供**前端/模型查询与重抓**，非人肉翻文件 → schema 以"模型可查询、可重驱"为先。

## 1. 要解决的痛点（来自 2026-08-27~28 诊断）

当前 `run.py --mode auto --apply` 是**串行同步**流水线：

```
discover_all()              # B站 + 公众号 一起发现（串行）
  → apply_summaries(all_new)  # B站视频 + 公众号文章 同一 for 循环处理
                              #   └─ B站无字幕视频在此同步调 Whisper ASR（默认 1800s/条，长视频更长）→ 阻塞整条循环
  → run_scys_daily()          # scys 排在 apply_summaries 返回后才启动
```

实测根因（PID 29516 卡死 40+ 分钟）：B站无字幕视频的 ASR 在主循环里**同步阻塞**，导致 (a) 排在其后的公众号文章被饿死、(b) scys 根本没启动。
而用户截图担心的「B站游客」「scys 没用 FDP」均为**误读**（cookie 正常 / scys 一直走 CDP，profile_clone 只是无 debug 端口时的兜底）——无需修。

另外：公众号依赖的 `weread.111965.xyz` 免费代理常返 5xx（瞬时），会使该轮扫码续期失败，但**只影响公众号**，B站/scys 独立。
更严重的是：**token 是否过期是"经代理检测"的**——`is_token_valid`（`monitors/wechat.py:145`）通过打代理接口 `resolve_mp` 的 `401` 判过期，无任何本地过期时间戳。该函数在代理 `5xx`/断网时与 `401` 共用同一套重试，重试耗尽后 `return False` 被当成"token 过期"**误弹二维码**（详见 §7.5 #10，这是审计原稿漏掉的真 bug）。

## 2. 目标架构

```
orchestrator (monitors/run_parallel.py)
 ├─ Worker-A 公众号  (子进程)  discover → fetch → summarize → 入 pending 队列
 ├─ Worker-B B站    (子进程)  discover → 逐视频：
 │      ├ 有 CC 字幕        → 内联 summarize
 │      └ 无字幕            → 投递 ASR 子进程池（有界并发，数见 §3.5 铁律2）→ 转写完入总结队列
 │                           （其余视频不被这一条阻塞）
 ├─ Worker-C scys   (子进程)  scys_batch_fetch → 入 scys pending 队列
 └─ Landing 阶段（串行 + 现有 series drain 锁）：依次消费三源队列 → 落飞书
 └─ Status Ledger：每一条任务 → {source, item_id, stage, status, ts, error, ...}
```

**关键约束（必须守住）**：
- **并行只发生在 抓取/转写/总结**（本地 CPU + 网络，**不碰飞书**）。
- **落飞书保持串行**：沿用 `apply_pending_series.py` 的 series 级 drain 锁；跨系列可并行（锁是 per-series，安全），同系列内部排队 → 不引入重复节点新竞态。
- **ASR 走子进程**（CPU 密集），不走线程（GIL）。

## 3. 并发模型

- 三源各一个 **worker 子进程**（非线程）：隔离故障——公众号 weread 5xx 崩了它的 worker，B站/scys 照跑。
- B站 worker 内：无字幕视频投递到 **ASR 子进程池**（有界并发，数见 §3.5 铁律2，默认按资源推导，防资源打满）；转写完成回调把 transcript 落文件并触发该条 summarize；worker 继续处理其他视频。
- **容错**：每个 worker 只写自己的 queue 文件 + ledger 记录，**父进程不依赖 worker 返回值**，只轮询文件。单源崩不影响其他源、不丢账。

### 3.5 ASR 重抓统一设计（回答用户 Q1：先统计 vs 遇到即起）

无字幕视频的转写统一为「**持久化队列 + 有界子进程池**」，覆盖两种触发时机：
- **运行期内 offload**：本轮 B站 worker 遇到无字幕视频，不阻塞，投递到 ASR 池异步转写（其余视频/源照跑）。
- **跨轮 redrive**：上轮没抓完/崩溃的，下轮或手动 `redrive` 统一补（复用 `pending_refetch.json` 范式）。二者共用队列 + 有界池，区别仅在触发时机。

**队列落点（消除断点5 悬空）**：`monitors/asr_queue.jsonl`（append-only），**与 `failures.jsonl` 职责分离**——`asr_queue` 存"待转写/转写中"，`failures.jsonl` 存"跨运行最终失败待重抓"。生命周期：`pending → transcribing → done`；崩溃卡在 `transcribing` 由陈旧看门狗复位 `pending`。

**5 条铁律**：
1. 先写队列、再起子进程——进程崩了内存里待转写列表丢失 → 视频永久漏抓。
2. 有界并发（`ASR_MAX_CONCURRENCY` env，默认按资源推导：无 CUDA=1、有 CUDA=2），**禁止"遇到 N 个起 N 个"**（20 条无字幕=20 个 Whisper→OOM/抢 GPU 雪崩）。
3. 每集清临时音频文件 + 落盘前磁盘预检，防堆积。
4. 落盘（笔记/飞书）前先做 children 查重——`asr_queue` 出的条目同样走单篇幂等（见 §5 落盘改动）。
5. redrive 过滤器：`status in (failed, timeout, pending)` **且 attempts<MAX**；并加**陈旧 `transcribing` 看门狗**（卡超阈值自动复位 `pending`）——修掉"转写中崩溃永久丢失"的数据丢失路径（审计 #4）。

## 4. 状态 Ledger（JSON，模型可查询）

- `monitors/run_status/<run-ts>.tasks.jsonl` —— 本次运行**每一条任务明细**（append-only，并发安全）：
  - 每条任务一行 JSON；多个 ASR 子进程/worker 各自 append，**无 read-modify-write 竞争**（解决边界 #5）。
  - 字段：`run_id`, `source`(bili|wechat|scys), `item_id`, `url`, `title`, `stage`(discover|fetch|transcribe|summarize|land), `status`(ok|skip|error|timeout|retry|**pending|transcribing|transcribe_failed**), `ts`, `error`, `retry_count`, `node_token`(落盘后填)
  - 收尾由聚合器将 jsonl → `latest.json` 汇总（查询时也可直接流式读 jsonl，无需先合并）。
- `monitors/run_status/latest.json` —— 运行级汇总：起止时间、每源 `discovered/fetched/landed/failed/skipped`、整体状态。
- `monitors/run_status/failures.jsonl` —— 跨运行**追加**的失败项（按 `item_id` 去重），供重抓。
- **Schema 原则**：扁平、字段固定、可被模型用脚本或直接读 JSON 过滤（例："取 last run 全部 `status=timeout` 的 bili 项"）。
- **查询/重抓入口** `monitors/status_cli.py`：
  - `summary`：打印 latest 汇总
  - `failures [--source X] [--status timeout] [--since <ts>]`
  - `redrive [--source X] [--status timeout]`：把失败项重新投递到对应 pending 队列，下次运行或立即重跑。过滤器 `status in (failed, timeout, pending)` 且 `attempts<MAX`；并自动复位**陈旧 `transcribing`**（卡超阈值的看门狗）为 `pending` 一并重驱（审计 #4 闭环）。

## 5. 改动文件

| 文件 | 动作 | 说明 |
|------|------|------|
| `monitors/run_parallel.py` | 新增 | 编排器：启 3 个 worker 子进程 + ASR 池 + 串行落盘 + 写 ledger |
| `monitors/status_store.py` | 新增 | ledger 写入 / 查询 / redrive 逻辑 |
| `monitors/status_cli.py` | 新增 | 模型/用户查询入口 |
| `monitors/run.py` | 改 | `apply_summaries` 支持按 source 过滤（拆出 `summarize_item` 单元）；`--parallel` 开关切到 run_parallel（**保留旧串行路径，零风险回退**） |
| `videos/asr.py` / `videos/rescue_episode.py` | 改 | 暴露"投递式"接口（输入 url→输出 transcript 文件，不阻塞调用方） |
| `monitors/wechat.py` | 改 | `is_token_valid` 按 HTTP 状态码**分类**：`401`=真过期→弹窗；`5xx`/网络断=代理异常→标记 `source_error`、跳过公众号、**不弹窗**（修掉"代理抽风误弹二维码"，见 §7.5 #10）；弹窗前先 `WECHAT_PROXY_OK` 探活代理 |
| Feishu 落盘逻辑 | **改** | 单篇落盘也加 `list_children` 查重（series 级 drain 锁 + 单篇幂等），复用已有白名单查重函数；消除重复节点竞态（审计 #1/#2/#6） |

## 6. 分阶段实施

- **P0 · 状态 ledger 模块**：`status_store` + `status_cli`，可单测；先接在现有 `run.py` 末尾，记录每源结果（不改动并行行为）。
- **P1 · 三源并行 worker（故障隔离）**：核心痛点。解决"单源故障级联拖垮全链路"。
- **P2 · B站 ASR 子进程池卸载**：无字幕视频不再阻塞其余视频。
- **P3 · redrive 接入 + 模型查询联调**：失败项可一键重抓。

## 7. 风险与缓解

| 风险 | 缓解 |
|------|------|
| worker 崩溃丢账 | 每 worker 只写自己的 queue + ledger，父进程轮询文件不依赖返回值 |
| 飞书重复节点 | 落盘保持串行 + drain 锁（已验证）；并行仅限抓取/转写/总结 |
| weread 5xx 影响扫码 | 仅公众号 relogin 受影响，已优雅跳过；ledger 记 `status=skip(reason=token)`，模型可读后建议重跑 |
| 代理 5xx 误判 token 过期 | `is_token_valid` 把代理 500/断网重试耗尽后 `return False` → 被当 token 过期**误弹二维码**（代码与 docstring 不一致，原写"保守返回 True 放行"实际返 False） | `is_token_valid` 按状态码分类（§5 wechat.py 改动）；弹窗前先确认代理连通，不通则记 `source_error` 不弹窗 |
| 回退 | 保留 `--parallel` 开关，出问题切回旧串行路径 |

## 7.5 边界 / 竞态矩阵（回答用户 Q2：流程运转间的边界问题）

按「是否已有应对」分两类。★ = 已有/可复用机制，缺口在实现时补齐。

### A. 已有机制可复用（不必从零造）
| 边界问题 | 现状 | 处置 |
|---|---|---|
| 公众号限流空正文→重抓 | `pending_refetch.json` 跨轮收集+前置重抓（run.py:671/827） | ASR redrive 复用同范式（§3.5） |
| 飞书重复节点 | series drain 锁 + 落盘前 children 白名单查重 | 落盘阶段串行，沿用即可 |
| 无字幕超时误杀 | `rescue_episode._probe_timeout` 时长×3+600 | ASR 池每集复用 |
| 单源 5xx 不拖垮全链 | 拆子进程后天然隔离 | 架构自带 |
| 重复总结/落盘浪费 | `filter_pending.py` 去重 + manifest `verified` | redrive 接入时复用 |

### B. 需新增应对的边界（实现时必须处理）
| # | 边界问题 | 风险 | 应对 |
|---|---|---|---|
| 1 | **崩溃丢队列** | 进程在「投递未落盘」时崩 → 漏抓 | 投递即写持久队列（§3.5 规则1） |
| 2 | **同文件夹并发落盘竞态** | 两 worker 同时 `ensure_folder_path` 都判「不存在」→ 建出重复文件夹节点 | 全局飞书令牌桶限流 + 落盘前查 children 去重（单条路径也加，不只 series） |
| 3 | **ASR 资源打满/OOM** | 池过大或临时文件堆积 | 有界池(1~2) + 每集清临时音频 + 磁盘预检 |
| 4 | **两次运行重叠** | 自动化重复触发 → 同写 ledger/pending 竞态 | 顶层 `monitors/.run.lock`（PID+心跳），二次调用拒绝/等待 |
| 5 | **ledger 并发写竞争** | 多个 ASR 子进程同时改同一 JSON | 每任务 **append-only JSONL 分片**，聚合器收尾合并（§4 调整） |
| 6 | **落盘后崩溃未记 landed** | 节点已建但 manifest/ledger 仍 pending → redrive 再建重复 | 落盘前「查 children 命中即跳过」兜底（§3.5 规则5） |
| 7 | **孤立子进程** | 主进程 Ctrl-C/OOM → ASR 子进程变孤儿继续烧 GPU | 子进程纳入进程组，atexit/signal 统一 kill |
| 8 | **B站 429** | B站 worker 内并行抓视频触发限流 | B站抓取保持串行+节奏(`BILI_GAP`)，ASR 池与抓取解耦 |
| 9 | **token 过期+代理 5xx 死锁** | 公众号需扫码但 weread 5xx 弹不出码 → 该轮卡等 | `WECHAT_RELOGIN_WAIT=0` + worker 优雅退出记 `auth_failed`，不阻塞；且 token 检测经代理（见 §1），代理不通时不触发弹窗（见 #10） |
| 10 | **代理 5xx 误判 token 过期**（审计原稿漏掉的真 bug） | `is_token_valid` 重试耗尽（全 500/断网）后 `return False` → 被当 token 过期**误弹二维码**；且 docstring 声称"保守返回 True"实际返 False（文档行为不一致） | `is_token_valid` 按 HTTP 状态码**分类**：`401`=真过期→弹窗；`5xx`/网络断=代理异常→标记 `source_error`、跳过公众号、**不弹窗**；弹窗前先 `WECHAT_PROXY_OK` 探活代理 |

## 8. 验证

1. 注入单源故障（如 weread 返 5xx）验证 B站/scys 仍跑完、ledger 正确记 `skip`。
2. 注入一条无字幕 B站视频，验证其余视频不阻塞、ASR 在子进程完成、ledger 记 `transcribe` 阶段。
3. 跑完查 `status_cli.py summary` / `failures` 可读性；`redrive` 能把失败项重新入队并成功补抓。

## 9. 决策记录（已替你拍板，新会话可直接照此实现）

以下为审计后替你拍板的工程决策，均已写入上文对应章节；仅最后一项（D3 细化）需你按机器环境确认，但已有安全默认值、**不阻塞开工**。

| # | 决策点 | 拍板结论 | 依据 |
|---|---|---|---|
| D1 | 落盘逻辑改不改 | **改**：单篇落盘也加 `list_children` 查重（series 级 drain 锁 + 单篇幂等） | 审计 #1/#2/#6：原 §5"不改"与 §7.5 矛盾，单篇无幂等会引入重复节点 |
| D2 | ASR 队列落点 | `monitors/asr_queue.jsonl`（待转写），与 `failures.jsonl`（跨运行失败）职责分离 | 断点5：避免"进行中"被当失败误重抓 |
| D3 | ASR 池并发数 | `ASR_MAX_CONCURRENCY` env，默认按资源推导（无 CUDA=1、有 CUDA=2） | 审计 #9：原"1~2"是魔数 |
| D4 | redrive 是否含转写中崩溃 | **含**：过滤 `pending/timeout/failed` + 陈旧 `transcribing` 看门狗自动复位 | 审计 #4：否则转写中崩溃永久丢失 |
| D5 | 公众号 auth 跳过是否标 seen | **不标 seen**：token 恢复后下轮自然补回，防永久漏抓 | 审计 #7 |
| D6 | 是否保留旧 `run.py` 串行路径 | **保留** `--parallel` 开关切分，零风险回退 | 用户要求 |
| D7 | ledger 存储 | JSONL append-only（latest 汇总 + failures 跨运行） | 用户：主要给模型查，非人翻文件 |
| D8 | P-fix 是否独立先上线 | **不独立**，折叠进 P1（B站 ASR 卸载 + 公众号 auth 跳过即 P1 交付，天然含最小止血） | P-obs 才解决可观测，但最小止血已是 P1 子集 |
| D9 | 代理 500 误弹窗 | **修**：`is_token_valid` 按状态码分类 + 弹窗前代理探活 | 本次核查真 bug（§7.5 #10） |

**唯一需你确认的输入（D3 细化 · 不影响开工）**：你本机 Whisper 跑在什么资源上？
- 纯 CPU / 显存紧张 → 默认 `ASR_MAX_CONCURRENCY=1` 即可，无需改动。
- 有独显（如 6G+ 显存跑 faster-whisper）→ 可设 `2~3` 提速。
→ 新会话先用默认值 1 实现，你后续按机器调 env 即可。

**结论**：方案现已自洽、无悬空引用、覆盖全部已讨论问题（级联根因 / 误读澄清 / 代理 500 诊断 / 三源并行 / ASR 重抓 / 边界竞态 / 状态 ledger / 决策记录）。新会话按 P0→P3 顺序实施即可。
