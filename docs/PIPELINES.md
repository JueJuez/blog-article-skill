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
`scripts/feishu_ext_refetch.py` + `scripts/refetch_feishu_ext_batch.py`（飞书外链全文重抓 / 批量版，
`.bear-web-x-container` 增量滚动；⚠️ URL 取 `<a href>` 或 `_ext` 文件头完整版，正文显示文本带 `...` 会 404）。

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
| `shared.env.load_env` | 加载 `.env`（`force_obsidian` 控制是否强制只写本地） | 所有需要 .env 的 CLI |
| `shared.note_audit.audit_one` | 单篇笔记抽检（四类判据 + A超长句取证） | `resum_audit_batch`、`audit_gate_signals` |
| `shared.cdp_session.SharedCdpSession` | 登录态抓取会话 | scys / 飞书 / YouTube |
| `shared.cdp_session.extract_body` | 从已渲染页面抽正文（选择器优先级，唯一实现；2026-09-18 收敛 articles/fetch.py + scys_batch_fetch.py 两份副本） | `SharedCdpSession._extract_body`、`_extract_body_scys`、`ScysClient._extract_body` |

## 10. 能力索引：想做这件事时，先查有没有现成的

> **动刀前先扫这一节。** 下面每个能力项目里都已经有实现，直接调用即可；
> 只有确认这里没有、或现有实现差一层薄改造时，才允许新写函数。

| 想做什么 | 现成的调用点 | 备注 |
|---|---|---|
| 抓任意文章正文（scys 自动分流 CDP 登录态） | `articles.fetch.fetch_web_content` | 入口：`articles/run.py` |
| 抓 B站字幕 / 转写 | `videos.fetch.fetch_transcript` / `fetch_bilibili_transcript` | 入口：`videos/run.py` |
| 无字幕视频转写（ASR） | `videos.asr.transcribe_video` / `transcribe_audio_chunked` | >30min 自动分片，见 `references/asr-bilibili-sandbox.md` |
| 抓 YouTube 字幕 | `videos.fetch.fetch_youtube_transcript` / `..._cdp` | 登录态走 CDP |
| 字幕清洗（填词/去重/合并） | `shared.subtitle_clean.preprocess_segments` / `preprocess_text` | 已在 fetch 链路自动接入 |
| 长文分块 / 两阶段总结 | `shared.chunking.chunk_text` / `two_stage_summarize` | |
| 判重 / 登记 / 跨源去重 | `articles.dedup.is_summarized` / `mark_summarized` / `find_cross_duplicate` / `normalize_title_for_match` | ⚠️ 判重用的标题归一叫 `normalize_title_for_match`（2026-09-18 改名），与 `shared.title_norm.normalize_title`（落盘标题清洗）**同名不同义，不可互换** |
| 单篇笔记抽检（四类判据） | `shared.note_audit.audit_one` / `evidence` / `score_of` | 2026-09-18 下沉，resum_audit_batch 与 audit_gate_signals 共用 |
| 加载 .env | `shared.env.load_env(force_obsidian=False)` | 2026-09-18 收敛：此前 scripts/ 下 6 份各自实现（`_run_with_env` / `_save_summary_from_file` / `resum_save_batch` / `fetch_bili_by_bvids` / `land_scys_by_key` / `persist_summary`） |
| 文件夹路由 | `shared.routing.resolve_folder` / `category_from_tags` | 落盘自动调用 |
| 语义标签（四维度） | `shared.note_classify.infer_semantic_tags` / `extract_and_strip_topics` | 落盘自动调用 |
| 笔记类型判定 | `prompts.classify.classify_note_type` | |
| 生成总结 prompt / 篇幅目标 | `prompts.templates.get_note_prompt` / `render_coverage_guide` | |
| 笔记格式化（标签行/来源链接） | `prompts.templates.format_note_with_prompt` | |
| 机械门禁（零 AI） | `prompts.verifier.verify_note_mechanical` | |
| 内容判据（锚点/破碎/结构/照搬） | `prompts.content_signals.content_flags` | |
| 抽检留痕 / 根因台账 | `prompts.review_rubric.log_review` / `queue_for_review` | |
| 标题归一 / 文件名净化 | `shared.title_norm.normalize_title`、`shared.sanitize.sanitize_filename` | |
| 调外部 AI | `articles.ai_provider.call_ai_summarize` / `get_ai_provider` | |
| 飞书节点增删改移 | `articles.feishu.FeishuOutput.ensure_folder_path` / `move_node` / `delete_node` | 2026-09-04 起默认不写飞书 |
| 飞书总览索引 | `shared.feishu_overview.ensure_overview` / `add_entry` / `rebuild` | |
| 带登录态抓页面（CDP） | `shared.cdp_session.SharedCdpSession` | 委托用户级 SKILL |
| 从渲染页抽取正文 | `shared.cdp_session.extract_body(page)` | 2026-09-18 收敛：此前 3 份逐字相同实现（`articles.fetch._extract_body_scys` / `scys_batch_fetch.ScysClient._extract_body` / `SharedCdpSession._extract_body`），现统一为模块级单点，改规则只改一处 |
| 飞书外链全文（懒加载） | `scripts/feishu_ext_refetch.py: collect_full_text` | `.bear-web-x-container` 增量滚动 |
| B站 cookie 健康 / 刷新 | `monitors.bilibili.refresh_cookie_if_dead`、`videos.set_cookie.set_bilibili_cookie` | |
| 监控去重状态 | `monitors.state.get_seen` / `mark_seen` | |
| 滚动日志 | `shared.rolling_log.append_rolling` | |
| 登记表全量重建 | `scripts/rebuild_registry.py` | vault 是唯一真源，登记表可重建 |

### 10.1 模块职能地图（找函数先定位模块）

> 函数级不做全量登记（641 个公共函数里绝大多数是模块内部 helper，全列会淹没重点）。
> 规则：**先按职责定位模块，再进模块找函数**。下面是最常被需要的模块。

| 模块 | 职责 | 典型函数前缀 |
|---|---|---|
| `articles/fetch.py` | 抓网页正文（scys 自动分流 CDP / 微信 / 通用） | `fetch_*`、`is_scys_url` |
| `articles/dedup.py` | 去重登记表（判重 / 登记 / 跨源去重 / 标题归一） | `is_summarized`、`mark_*`、`find_cross_*` |
| `articles/main.py` | 落盘主链路（路由 / 门禁 / 保存 / 对外入口） | `save_*`、`skill_*`、`autoroute_*` |
| `articles/ai_provider.py` | AI provider 抽象与各家实现（Trae/OpenAI/Anthropic/Google…） | `call_*_summarize`、`get_*_provider` |
| `articles/feishu.py`、`articles/obsidian.py` | 飞书 / Obsidian 输出端实现 | `ensure_*`、`move_node`、`save` |
| `videos/fetch.py` | 字幕抓取（B站 / YouTube / 412 风控 / cookie 轮换） | `fetch_*_transcript`、`rotate_bili_cookie_*` |
| `videos/asr.py` | 无字幕转写（下载音频 + Whisper，长音频自动分片） | `transcribe_*`、`extract_audio` |
| `shared/routing.py` | 文件夹路由（账号 / 系列 / 分类命中） | `resolve_folder`、`match_series` |
| `shared/note_classify.py` | 语义标签与父子领域判定 | `infer_semantic_tags`、`*_from_lede` |
| `shared/feishu_overview.py` | 飞书总览索引（账号容器内的文章清单） | `ensure_overview`、`add_entry`、`rebuild` |
| `shared/cdp_session.py` | 登录态抓取会话（委托用户级 CDP SKILL） | `SharedCdpSession` |
| `prompts/templates.py` | 模板与 prompt 组装、笔记格式化 | `get_note_prompt`、`format_note_with_prompt` |
| `prompts/verifier.py`、`prompts/content_signals.py` | 机械门禁与四类内容判据 | `verify_*`、`*_flags` |
| `monitors/*` | 订阅监控（B站 / 微信 / scys 三源 + 并行 + 状态 + 回溯） | `run_*`、`cmd_*` |
| `scripts/feishu_to_obsidian.py` 等 `scripts/` 迁移脚本 | 飞书→本地镜像与结构迁移（多已冻结） | — |

## 11. 其余 CLI 清单（不常用，但**已有实现，勿重造**）

> 下面这些没进上面的管线章节，是因为它们属于运维/诊断/历史迁移，不是日常入口。
> 但**它们都还在代码里**——需要同类能力时先跑 `--help` 看看，别新写。
> 定期自检：`python scripts/audit_pipeline_coverage.py`（列出本文没登记的 CLI）。

**对账 / 体检 / 审计（可复用）**
`scripts/rebuild_registry.py`（登记表 bootstrap）、`scripts/audit_sync.py`（vault→飞书对账补传）、
`scripts/audit_fidelity.py`（总结质量抽样审计）、`scripts/audit_overwrite_copies.py`（`-N` 副本审计）、
`scripts/audit_gate_signals.py`（门禁判据离线审计）、`scripts/vault_lifecycle.py`（vault 生命周期对账）、
`scripts/triage_fetch_failures.py`（抓取失败只读分类）、`monitors/status_cli.py`（运行状态查询）、
`scripts/audit_pipeline_coverage.py`（本文覆盖自检）、`scripts/audit_duplicate_funcs.py`（重复实现自检：
**写新函数前跑一次**，看是不是已经有现成的 / 能不能合并）。

**落盘辅助（单篇场景）**
`scripts/persist_summary.py --obsidian`（接单持久化，含去重+门禁+标签）、
`articles/_save_summary.py`（外层对话保存总结的专用入口）、
`scripts/_save_one_summary.py`（单条 JSON → `save_summary_only`）、
`scripts/land_migrate_entry.py`（migrate 队列落盘）、
`scripts/land_scys_batch.py`（scys 飞书双写路径，需 `DISABLE_FEISHU_SYNC=0`）。

**凭据 / 登录态**
`scripts/login_cdp_fetch.py`（接管 Chrome 抓需登录页）、`scripts/bili_cookie_refresh.py`、
`videos/set_cookie.py`、`monitors/_auth.py`（weread 扫码辅助）。

**长任务启动器（DETACHED）**
`scripts/launch_fetch_up_detached.py`、`scripts/launch_scys_refetch_detached.py`（⚠️ 内含 `--no-external` 硬编码，
补源场景勿用）、`scripts/launch_backfill_series_detached.py`、`scripts/launch_scys_backfill.py`。

**飞书迁移期脚本（2026-09-04 起默认不写飞书，多数已冻结；保留作参考/特殊镜像）**
⚠️ 下面每个文件头部都加了「已冻结」标记：**勿复用、勿在此基础上继续开发**。
它们内部有大量互相复制的实现（`_find_lark_cli` / `run_cli` / `list_children` / `find_monitor_root` /
`collect_articles` / `fetch_body` / `is_overview`…），是历史堆积**不是范例**。
需同类能力先看 `shared/`、`articles/` 下的现行实现。
`feishu_to_obsidian.py`、`migrate_feishu_structure.py`、`migrate_obsidian_vault.py`、`migrate_watchdog.py`、
`feishu_to_obsidian.py`、`migrate_feishu_structure.py`、`migrate_obsidian_vault.py`、`migrate_watchdog.py`、
`fix_feishu_titles.py`、`probe_feishu_titles.py`、`rename_list.py`、`promote_existing.py`、
`find_duplicates.py`、`delete_duplicates.py`、`scan_feishu_tree.py`、`list_overviews.py`、
`backfill_overviews.py`、`rebuild_overviews.py`、`series_maintenance.py`、`audit_sync_watchdog.py`。

**B站补齐相关**
`scripts/reset_up_backfill.py`（重置补齐状态）、`scripts/reconcile_series_bvid.py`（bvid 对账，只读）、
`scripts/fetch_transcript_only.py`（只抓字幕不总结）、`videos/cdp_capture.py`（CDP 抓 YouTube 字幕）、
`videos/yt_bridge.py`（YouTube 字幕桥接）、`videos/build_bookmarklet_html.py`（小书签安装页生成）。

**其它项目域**
`tools/project_import/*`（开源项目归档，见能力 5；内部 `assets/pipeline.py`、`assets/ingest_repo.py`、
`migrate_feishu_to_local.py` 均为其零件，不是入口）、`scripts/fde_extract_cases.py` +
`scripts/fde_check_card.py`（FDE 案例卡抽取与忠实度门禁，属简历/面试域，非本项目日常管线）。

**自检 / 演示型 `__main__`（非入口，供调试用）**：`shared/note_classify.py`（标签分类器自测）、
`monitors/run_parallel.py`（并行编排器，日常请用 `monitors/run.py --parallel`，不要直调）。

**归档候选（一次性诊断，任务已完成）**：`scripts/gen_pilot_report.py`、`scripts/audit_overwrite_copies.py`、
`scripts/build_refetch_resum_batches.py`（与 `build_resum_batches.py` 同构，待合并）、
`scripts/_force_land_p1.py`（P1 孤儿视频 force 重落盘，任务已完成）。

## 12. 变更纪律

- 改任何入口的**命令形态**（改名/换参数）→ 同步更新本文 + `AGENTS.md` 对应能力小节。
- 新增脚本：有长期价值的**不要用 `_` 前缀**（`.gitignore:50` 忽略 `scripts/_*`，会漏入库）。
- 发现本文没覆盖的场景 → **先补本文再动手**，不要自己新开一条并行实现。
- 新写任何可复用函数前 → 先扫「§10 能力索引」；只有在确认没有、或现有实现需要大改时才新写，
  并把新函数补进 §10。
- 定期自检覆盖度：`python scripts/audit_pipeline_coverage.py`（列出所有 CLI 入口里本文没登记的）。
  它有遗漏不等于文档正确——命中只说明「被提到过」，描述准不准仍要人看。
