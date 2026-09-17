# 管线入口总表（唯一真源 · 2026-09-17 建立）

> **找「这事从哪进」只看本文。本文没登记的入口 = 不存在。**
> 新会话 / 新 Agent 开工前先读本文，再决定调什么。

## 0. 三条纪律（防止入口混乱）

1. **入口 = 一条可以直接执行的命令**（`python scripts/xxx.py ...`）。只被别的模块 `import` 的函数不是入口。
2. **零件不是入口**。下表每条管线都列了「零件（非入口）」——它们只被入口调用，直接拿它们当入口会漏掉前置校验
   （典型事故：直接调 `save_summarized_article` 落盘，跳过去重闸门与机械门禁，把坏笔记写进库）。
3. **新增/改造管线必须先更新本文**，否则下一个 Agent 找不到它，就会自己新开一个函数重复实现（本项目已因此
   出现过两份并行落盘实现，见「3. 存量重做」的历史说明）。

## 1. 决策表：我要干什么 → 走哪个入口

| 我要干什么 | 入口 | 别用（常见误用） |
|---|---|---|
| 给我一篇/几条链接，总结归档 | `python articles/run.py "<url>"` | 手搓抓取脚本 |
| 总结一个视频（B站 / YouTube） | `python videos/run.py --url "<url>"` | 直接调 `videos.fetch` |
| 每日跑一遍订阅（B站UP/公众号/scys） | `python monitors/run.py --mode auto --apply` | 手改队列 JSON |
| 新订阅一个 B站UP | `python monitors/run.py --subscribe --uid <id> --name <名> --category <类>` | 手搓 `subscriptions.json`（公众号/scys 才手改） |
| 公众号历史回溯 | `python monitors/run.py --backfill --names <名> --since <日期> --batch 15` | — |
| **把库里已有的旧笔记按新版 prompt 重写** | **「3. 存量重做」四步**（`build_resummarize_queue.py` → `build_resum_batches.py` → 派子 Agent → `resum_save_batch.py`） | ❌ `save_summary_only` / `_save_summary_from_file.py`（会重新推导文件名 → `-N` 副本） |
| 某篇笔记的源文本丢了，只想补源 | `scripts/fetch_bili_by_bvids.py` / `scripts/fetch_scys_by_ids.py` | `scys_batch_fetch.py` / `fetch_up_range.py`（会被 dedup 闸门跳过） |
| 补齐某个 UP 主全部历史视频 | `scripts/list_up_videos.py --uid` → `scripts/_run_with_env.py -- python scripts/fetch_up_range.py 1 N --uid ... --author ...` | 直接跑 `fetch_up_range.py`（不读 `.env`，cookie 取不到） |
| 补齐生财有术某领域全部帖子 | `python scripts/launch_scys_backfill.py`（数小时任务） | 前台直跑（会话结束被回收） |
| 补齐/重做某系列课整季 | `python scripts/backfill_series.py --series <名>` | ❌ `monitors/apply_pending_series.py`（文件已不存在） |
| 迁移期体检 / 清异常 | `python scripts/migrate_gate.py --vault <path> --scope=` + `--apply` | — |
| 归档开源项目到项目库 | `python tools/project_import/assets/main.py "<repo url>"` | — |

## 2. 增量生产管线（抓新内容 → 落新笔记）

**入口**：`python articles/run.py "<url>"`（或 `from articles import skill_main`）/ `python videos/run.py --url`
**特点**：文件名由 `generate_filename(publish_time + title)` 推导，落进 folder 路由或【待归类】。

**零件（非入口）**：`articles.fetch_web_content`（scys 自动分流 CDP）、`summarize_content`（调 AI）、
`save_summary_only`（去重闸门 + 机械门禁 + 路由，是 `skill_main` 的内部分支）、
`save_summarized_article`（最终落盘；2026-09-17 起它同时服务存量重做，见下）、
`articles/manager.py: OutputManager`（飞书/Obsidian/本地三选一）。

## 3. 存量重做管线（旧笔记重写 → in-place 覆盖原路径）

**适用场景**：库里已有笔记，需要按新版 prompt 重写（阶段C 就是这么跑完 690+98+24 条的）。

| 步 | 入口命令 | 说明 |
|---|---|---|
| 1 | `python scripts/build_resummarize_queue.py` | 从 `summary_registry.json` + vault 反查，挑出「笔记在库 + 源可定位」的条目 |
| 2 | `python scripts/build_resum_batches.py` | 按源长切批（>10000→4 条/批，4000~10000→8，<4000→16）+ 逐条预计算 prompt |
| 3 | 派子 Agent（**必读** `notes/_meta/resum_batches/DISPATCH_PROMPT.md`） | 产出 `_tmp/resum_c/out/<batch>_<NN>.md`，并发 2 安全、3–4 必 429 |
| 4 | `python scripts/resum_save_batch.py <batch.json> <out_dir> --force` | **唯一落盘入口**：校验主题词行 + 机械门禁 → 调生产函数 in-place 覆盖 `note_path` |
| 5 | `python scripts/resum_audit_batch.py <batch.json>` | 抽检（只读），命中项填 `root_cause` |
| 附 | `python scripts/archive_old_before_resum.py <batch.json>` | 重做前备份旧版 |

**零件（非入口）**：`articles.main.save_summarized_article(note_path=...)` —— in-place 能力 2026-09-17
已从 `resum_save_batch.py` 下沉到这个生产函数，**它只被入口调用，不要直接调**（会跳过门禁）。

**历史教训（别倒退）**：`resum_save_batch.py` 早期复制了生产链路的 4 个变换
（`extract_and_strip_topics` / `infer_semantic_tags` / `format_note_with_prompt` / `verify_note_mechanical`），
导致生产侧改标签规则时重做出的笔记会漂移。现已下沉为单一实现 + 回归测试
（`tests/test_inplace_resum_pipeline.py`）钉死。

**⚠️ 与 `overwrite=True` 的区别**：`overwrite=True` 只覆盖**同名**文件，而文件名仍是推导出来的；
标题与磁盘名错配时扑空 → 照样产 `-N` 副本。in-place 走 `note_path`，压根不推导文件名。

## 4. 订阅监控管线

**入口**：`python monitors/run.py --mode auto --apply`（每日）/ `--mode first --apply`（首跑 30 天）/
`--parallel --mode auto`（三源并行）/ `--subscribe ...`（加订阅）/ `--backfill ...`（公众号历史）
**零件**：`monitors/run_source.py`、`monitors/bilibili.py`、`monitors/wechat.py`、`monitors/state.py`、
`pending_summaries.json` 队列、`scripts/filter_pending.py`（派单前清洗，属于队列维护不是入口）。

## 5. 系列课管线

**入口**：`python scripts/backfill_series.py --series <名>`（清单外整季补齐）；
日常增量**无需手工跑**——单集随单篇队列消费（五步管线 `summarize_series_episode`）。
长任务：`scripts/launch_backfill_series_detached.py`。
**零件**：`summarize_series_episode`、`ensure_series_node`、`convert_series_to_hierarchy.py`。

## 6. UP 主全量补齐管线（B站）

**入口**：`scripts/list_up_videos.py --uid <UID>` → `python scripts/_run_with_env.py -- python scripts/fetch_up_range.py 1 N --uid <UID> --author <UP名>`
→ `python scripts/filter_pending.py` → 派子 Agent 消费队列。
⚠️ `fetch_up_range.py` 自身不读 `.env`，必须经 `_run_with_env.py` 包装。
**零件**：`videos/fetch.py`、`videos/asr.py`（ASR 兜底）、`monitors/asr_pool.py`。

## 7. 生财有术补齐管线

**入口**：`python scripts/launch_scys_backfill.py`（DETACHED 长任务，数小时）；按领域单跑
`python scripts/scys_batch_fetch.py --project <领域>`。落盘另有 `scripts/land_scys_by_key.py`（按 topicId 稳定键）。
**零件**：`scripts/scys_batch_fetch.py: ScysBatchFetcher.fetch_article`、`shared/cdp_session.py`、
`scripts/feishu_ext_refetch.py`（飞书外链全文重抓，`.bear-web-x-container` 增量滚动）。

## 8. 迁移门禁管线

**入口**：`python scripts/migrate_gate.py --vault <path> --scope=`（dry-run）+ `--apply`；
队列消费走 `scripts/consume_migrate_queue.py`（`--plan` → `--fetch --limit 15` → `--clean`）。
**零件**：`scripts/consume_migrate_queue.py` 内部各阶段函数。真源：`docs/decisions/DECISION-20260908-regen-gate-v3.md`。

## 9. 跨管线通用零件（**都不是入口**）

| 零件 | 作用 | 被谁调用 |
|---|---|---|
| `articles.main.save_summarized_article` | 最终落盘（含标签/来源链接/dedup 登记） | `save_summary_only`、`resum_save_batch` |
| `articles.main.save_summary_only` | 去重闸门 + 机械门禁 + 路由 + 落盘 | `skill_main`、队列消费 |
| `shared.routing.resolve_folder` | 文件夹路由 | 落盘链路 |
| `shared.note_classify.infer_semantic_tags` | 四维度语义标签 | `save_summarized_article` |
| `prompts.verifier.verify_note_mechanical` | 机械门禁（零 AI） | `save_summary_only`、`resum_save_batch` |
| `articles.dedup` | 去重登记表 | 落盘链路 |
| `shared.cdp_session.SharedCdpSession` | 登录态抓取会话 | scys / 飞书 / YouTube |

## 10. 变更纪律

- 改任何入口的**命令形态**（改名/换参数）→ 同步更新本文 + `AGENTS.md` 对应能力小节。
- 新增脚本：有长期价值的**不要用 `_` 前缀**（`.gitignore:50` 忽略 `scripts/_*`，会漏入库）。
- 发现本文没覆盖的场景 → **先补本文再动手**，不要自己新开一条并行实现。
