# 并行监控改造 · 过程档案（PLAN-20260828）

> **本文是过程档案（how-we-got-here），不是操作手册。**
> 日常操作看 `AGENTS.md` 能力 2 与 `monitors/README.md`；决策与验证清单看 `docs/plans/PLAN-20260828-parallel-monitor.md`。
> 2026-09-02 从项目 `MEMORY.md` 下沉至此——常驻记忆只留当前可执行事实，过程叙述移出。

## 改造范围（2026-08-28 收官）

`monitors/run.py --mode auto --apply` 串行流水线并行化。

- **P0** 状态 Ledger（新增 `status_store.py` / `status_cli.py`）
- **P1** 三源并行 + ASR 卸载 + 故障隔离 + wechat 误弹码修复
- **P2** draft_only / Landing 解耦 + 飞书节点锁
- **P3** redrive 投递

新增 `status_store.py` / `status_cli.py` / `run_lock.py` / `run_parallel.py`；
改动 `run.py` / `wechat.py` / `asr.py` / `articles/main.py` / `articles/feishu.py` / `articles/local.py`。

## 过程中修掉的 bug（根因记录）

### 1. 本地兜底落盘路径多爬一层（预存在 bug，本次暴露）

`articles/local.py` 的 `_get_base_path()` 旧实现用 `.., .., notes`，
导致本地兜底落盘到**项目外**的 `<祖父>/notes`。改为 `.., notes`，
与 `articles/main.py` 的 `NOTES_DIR` 单一事实源一致。
此前所有本地兜底都落错目录——因为飞书为主路径，一直没暴露。

### 2. 并行 worker 静默覆盖队列（2026-08-29 复审发现）

两个真 bug：

1. `wechat` / `bili` 两 worker 同调 `apply_summaries`，对同一个
   `pending_summaries.json` / `pending_refetch.json` **无锁读-改-写** → 静默覆盖丢失。
2. 各 worker 自建 `SharedCdpSession`，各自 kill + clone，固定 `CLONE_DIR` 并发污染。

修复：

- 每 worker 写独立 staging 文件，父进程 join 后 `_merge_stage_to_json` 合并
  （去重 / 保留未消费项 / refetch 由 staging 重建，与串行语义一致）。
- 父进程建**一次** `SharedCdpSession` 并暴露 `cdp_endpoint`，
  worker 经 `SharedCdpSession.from_endpoint()` `connect_over_cdp` 接管同一浏览器（一次 kill、多 worker 共享）。
- 旧 `pending_refetch` 由父进程先按路由拆分给对应 worker 重抓。
- **串行路径另做惰性会话**：仅当有微信撞墙文或 scys 才 kill Chrome（纯 B站/动态轮次 0 kill）。

覆盖：`tests/test_parallel_merge.py`（86 行，合并无丢失/无覆盖/去重）。
受控单测 `test_parallel.py`（原拟放 monitors/ 下）已按用户要求删除——从未入库，仅会话内临时文件。

## 拍板结论

- **D3**：本机 RTX 4060 + faster-whisper 走 ctranslate2 CUDA →
  `ASR_MAX_CONCURRENCY` 默认 **2**（`detect_asr_max_concurrency()` 用
  `ctranslate2.get_cuda_device_count()`，无卡自动回退 1）。
- **用户明确**：不在意串行还是并行，只在意四件事达成——
  一次 KILL / ASR 不阻塞 / 公众号不阻塞 / 状态机重抓。

## 验证状态

| 路径 | 状态 |
|---|---|
| 串行 `python monitors/run.py --mode auto --apply` | ✅ 2026-08-28 晚真跑通（B站/公众号/scys 真实抓取 + 12 篇落飞书） |
| 并行 `--parallel` | ✅ 2026-09-02 真环境实跑通过（`monitors/run_status/20260902-095504.*`：bili/scys/wechat 三源并发，overall=ok，698s）；代码层 + 单测此前已通过 |

并行真验收入口：`python monitors/run.py --parallel --mode auto`
（不加 `--parallel` 即回退串行；`--asr-max N` 可覆盖并发）。
跑前建议备份 `monitors/state.json`。

## 已失效的设计（留档勿照做）

- **P3 `deliver_redrive`**：原计划把 bili `transcribe_failed` 与 wechat 失败投
  `pending_summaries.json`。随 `failures.jsonl` 子系统整体删除而失效，
  跨轮重抓已统一收敛为 `pending_refetch.json` 单链路。
- **`failures.jsonl` 子系统**（`status_store.add_failure` / `redrive_items` /
  `deliver_redrive` / `status_cli failures|redrive`）：2026-08-29 第一性原理审计
  判定为冗余死代码并整体删除——生产者全仓零调用、文件永为空、与
  `pending_refetch.json` 重复解决同一问题。
