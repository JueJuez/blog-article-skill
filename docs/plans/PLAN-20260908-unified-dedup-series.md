# PLAN-20260908 · 统一去重登记表（registry）+ 系列课去流程化

> 状态：**阶段 0-4 已完成（含 4.6 文档收口）；阶段 5 消费驱动已落地、队列消费进行中（2026-09-09：93/426 已登记，staging 存 11 条待消费，续跑指南见「阶段 5」节）**
> 背景：全管线去重审计（audit_pipeline_dedup_20260906.md）发现判定分散在 5+ 处状态文件；migrate_gate v3 DRY-RUN 扫出 652 文件中 457 异常、重抓队列 426 条；系列课六件套（贪婪抓取/双索引/队列/manifest/页码防护）被实证为过度设计。用户三项决策已拍板（见 §10）。
> 原则：**vault 成品笔记是唯一真源，登记表只是真源的索引缓存**——登记表丢了可由 rebuild 全量重建，成品笔记永不因登记表缺失而重抓（登记表丢失的最坏代价 = 重抓重总结，不是数据丢失）。

## 1. 问题陈述

1. **判定分散**：`sync_ledger.json`（文章/视频）、`monitors/state.json:seen`（B站监控）、`monitors/series_state.json`（系列 done/fetched 双索引）、`notes/<系列名>/_manifest.json`（系列状态机）、scys `done_ids`/`fetch_results`——同一问题「这篇总结过没」有 5 个答案源，互相不知道对方。
2. **真源悬空**：vault 里明明有 600+ 篇成品笔记，但任何判定入口都不读它；新管线只能依赖各状态文件的累积记忆。
3. **系列课六件套**：整季贪婪抓取、fetched 索引、done 索引、pending_series 队列、manifest 五态状态机、页码冲突防护。实证（千刀千法两个第02集共存、UP 改序第33/34集同标30）表明页码防护无效且不必要——防重真正靠的是「URL 唯一」。

## 2. 目标架构：三个「一」

```
真源层：  Obsidian vault 成品笔记 ──rebuild 扫描──▶ 统一登记表 summary_registry.json
判定层：  四个调用点（入队检查/派单清洗/落盘登记/批量补齐）──▶ 唯一判定入口 is_summarized / batch_is_summarized / register
缓存层：  seen · series done/fetched · scys done_ids ──逐步收编──▶ 判定入口（先降级为缓存，最后删除）
```

- **一个真源**：vault 成品笔记。
- **一张登记表**：`notes/_meta/summary_registry.json`（由 sync_ledger.json 升格重命名）。
- **一个判定入口**：`articles/dedup.py` 的 `is_summarized` / `batch_is_summarized` / `register`——所有调用点只准问它，不准各自读状态文件。

## 3. 登记表设计

### 3.1 文件与迁移（D1）

- 物理文件：`notes/_meta/summary_registry.json`。
- 迁移：`dedup.py` 加载时旧路径 `notes/_meta/sync_ledger.json` 存在且新文件不存在 → 自动改名搬家（同 PLAN-20260906 P0-2 迁移先例）；代码引用方仅 `articles/dedup.py` + `tests/conftest.py` + `tests/test_sync_ledger.py`，改动面可控。
- 并发安全：沿用已落地的 `_index_lock` 文件锁，rebuild 是离线单进程不叠加风险。

### 3.2 schema（薄，向后兼容）

```json
{
  "<key>": {
    "key_type": "url | title_fp",
    "title": "笔记标题",
    "folder": "【我的总结】/作者/xx",
    "note_type": "structured | key_points | ...",
    "source": "article | video | series | scys | wechat",
    "summarized_at": "2026-09-08",
    "feishu_link": "", "obsidian_link": ""
  }
}
```

- `feishu_link`/`obsidian_link` 为 PLAN-20260906 已有字段，原样保留（vault_lifecycle 仍用）。
- 旧记录读时 `.get()` 补空，不迁移、不回填（惰性，被 rebuild/被写时自然补齐）。

### 3.3 键设计（沿用现机制）

- **URL 键**（主）：`_normalize_url(url)` → sha256 前 16 位（`articles/dedup.py:_key_for`）。
- **标题指纹键**（兜底）：无 URL 笔记（如直播回放）用 `normalize_title(title)` 的 hash，`key_type="title_fp"`。
- 归一化缺陷修复（PLAN-20260906 P1-1 顺带落地）：host 白名单剥离可变 query（b23.tv 先展开），否则 rebuild 会因 URL 参数差异产生同内容多键。

## 4. bootstrap：两步固定顺序

### 步骤 0（人工，一次性）：`python scripts/migrate_gate.py --apply`

生成重抓队列（现 DRY-RUN 426 条落盘到 vault 同级 `_migrate_gate_archive/`）。**必须先跑**——步骤 1 的排除规则依赖队列里的 `old_paths`。

### 步骤 1：`python scripts/rebuild_registry.py --vault <路径>`（新建）

扫描规格：

| 规则 | 内容 |
|---|---|
| 排除 1 | 路径任一段以 `_` 开头的目录（`_meta` / `_scraped` / `_migrate_gate_archive` 等） |
| 排除 2 | 文件命中重抓队列 `old_paths`（426 条 BC 类**完全不登记**——D2，用户拍板；队列本身即待办清单，重抓成功落盘后自然进下次扫描） |
| 排除 3 | 文件名与父目录同名的疑似系列容器页（rebuild 报告单列，供人工核对，支持 `--include/--exclude` 微调） |
| URL 提取 | 三级兜底：frontmatter `source_url` → 正文「来源链接」行（复用 `migrate_gate.py:_main_url`）→ 标题指纹 |
| 幂等 | 重复跑一致；已登记记录 merge 保留人工字段，只补缺 |
| 输出 | 登记表 + 控制台报告（扫描 N / 登记 M / 排除 X₁X₂X₃ / 指纹键 Y / 同内容多键疑似清单） |

## 5. 系列课去流程化

### 5.1 逐 URL 通用管线（五步，与普通视频一致）

1. **查登记表**：`is_summarized(url)` 命中 → 跳过。
2. **1 次 view API**：拿系列名 + 页码，**仅用于落盘路由** `【监控】/B站/<UP>/<系列名>/` 和文件名页码前缀（装饰，不校验冲突）。
3. **单 URL 字幕抓取**：复用 `videos/fetch.py`，无 CC 自动 ASR 兜底（现有机制不动）。
4. **通用总结**：按分类器模板，与普通视频完全同路径。
5. **落盘 + 登记**：`resolve_folder` 算路径落盘 → `register(source="series")`。

### 5.2 两个触发面（本轮新语义，回应 2026-09-08 用户补充需求）

| 触发面 | 行为 |
|---|---|
| **增量**（监控自动） | 发现系列课单集 → **主动 ensure 系列容器配套**（目录/节点）→ 单集走五步管线。**不回溯旧集、不抓整季**。 |
| **点名补齐**（用户发起） | 「补齐 <UP> 的系列课」→ 列全集 URL → 逐条走**同一五步管线**，已登记自动跳过（断点续跑天然成立）。 |
| **首跑自动补齐**（2026-09-08 用户提出） | 新监控对象首跑检测到系列课 → 列全集 URL → 过滤登记表 → 未总结集**分批入队**（每轮 `SERIES_BACKFILL_BATCH` 条，默认 15）→ 后续每轮运行自动续批直到补完。与点名补齐**共用同一函数**，仅触发源不同。 |

**补齐队列护栏（D8，2026-09-08 用户拍板 A）**：点名补齐与首跑自动补齐共用同一队列，队列条目带失败计数——同一集累计失败 3 次后停止自动重试、转人工清单（与健康度报告同路）。背景：总结失败的集不登记（真源无成品），下次触发补齐再次检出属预期行为（该补的就得补），但无上限会天天空转。

### 5.3 首跑自动补齐可行性与请求量论证（D6，2026-09-08 用户追问后定稿）

用户顾虑：旧设计请求量暴增（76×117 量级），担心「自动补齐」重蹈覆辙。

**暴增根源不在"补齐"这个动作，而在旧贪婪设计的乘法结构**：
1. **每集触发整季扫描**：一季 76 集被逐集检测，每集都触发一次全季抓取 → 76 × 全季请求量；
2. **索引失效重抓**：fetched/done 记忆缺口（2026-09-07 前直调路径只 mark_fetched 不 mark_done）→ 按天数再乘；
3. **无节奏无熔断恢复**：一次会话连发全部请求 → 412 限流 → 卡死或从头再来。

**新设计把乘法改回加法**（请求量账本）：

| 场景 | 旧设计 | 新设计 |
|---|---|---|
| 首跑补齐 1 季 N 集 | 每集触发全季，N×N 级 | 1（列表）+ 2×未登记集，一次线性 |
| 每日增量 | 索引缺口时全季重抓 | 仅新集 ~2-4 请求 |
| 断点/重跑 | 从头再抓 | 已登记 0 请求跳过 |
| 节奏 | 连发 | `BILI_GAP` 串行 + 412 熔断（`fetch_up_range` 已有）+ 每轮 `SERIES_BACKFILL_BATCH`（默认 15）封顶 |

**结论**：不复杂——与点名补齐共用同一函数，增量成本 ≈ 1 个任务 + 3 例单测。采用**渐进式**分批补齐而非一次性全抓，单轮请求量封顶、天然断点续跑。

### 5.4 退役清单（D3，用户已同意）

| # | 退役对象 | 位置 |
|---|---|---|
| 1 | 整季贪婪抓取 | `videos/main.py:_handle_bilibili_series` 列全集逐集抓逻辑 |
| 2 | `fetched` 索引 | `shared/series_state.py` mark_fetched/is_fetched + `monitors/series_state.json` |
| 3 | `done` 索引 | `shared/series_state.py` mark_done/is_done（2026-09-07 刚修的三处调用一并退役） |
| 4 | `pending_series` 队列 | `monitors/pending_series.json` + `monitors/apply_pending_series.py` |
| 5 | manifest 状态机 | `shared/series_manifest.py` + `notes/<系列名>/_manifest.json` |
| 6 | 页码冲突防护 | `shared/sanitize.py` 集数一致性校验（页码降级为命名装饰） |

**职责接住方**：防重 → 登记表 URL 键；续跑 → 点名补齐时登记表自动跳过已总结集；落盘配套 → `ensure_series_node` / `resolve_folder` 保留不动。`series_state.json`/`_manifest.json` 按缓存语义废弃，**不做数据迁移**（丢了可由登记表 + 重抓重建）。

## 6. 判定入口切换：四个调用点

| # | 调用点 | 文件 | 改动 |
|---|---|---|---|
| 1 | 入队时检查 | `monitors/run.py` | discover 后 `batch_is_summarized` 命中不入队 |
| 2 | 派单前清洗 | `scripts/filter_pending.py` | 已走 dedup API，随文件升级自动切换，仅改测试断言 |
| 3 | 落盘时登记 | `articles/main.py:save_summarized_article` | 已有 mark_summarized，扩展 source/note_type/folder 字段 |
| 4 | 批量补齐 | `scripts/scys_batch_fetch.py` / `scripts/fetch_up_range.py` | done_ids 改查登记表；入队前预查 |

## 7. 任务拆分（TDD：RED→GREEN→REFACTOR，全部先写失败测试）

### 阶段 0（人工）

| # | 任务 | 操作 | 验证 |
|---|---|---|---|
| 0.1 | 生成重抓队列 | `python scripts/migrate_gate.py --apply` | `_migrate_gate_archive/` 出现队列 JSON，条数≈426 |

### 阶段 1 · 登记表核心（`articles/dedup.py`）

| # | 任务 | 预期改动 | 验证 |
|---|---|---|---|
| 1.1 | 文件升格+搬家 | `_DEFAULT_INDEX_FILE`→`summary_registry.json`；`_load_index` 前置旧文件改名逻辑；schema `.get()` 兼容 | 新增 `tests/test_registry_upgrade.py`：搬家/共存/空表/损坏表 4 例 |
| 1.2 | register API | `register(url, title, folder, note_type, source)` 薄封装扩展字段 | 同文件：正常/缺参/重复幂等 3 例 |
| 1.3 | title_fp 键支持 | `is_summarized`/`batch_is_summarized` 接受无 URL 标题指纹 | 同文件：指纹命中/未命中 2 例 |
| 1.4 | 归一化修复（P1-1） | `_normalize_url` host 白名单剥 query；b23.tv 展开 | `tests/test_dedup.py` 补 3 例 |

### 阶段 2 · rebuild_registry.py（`scripts/rebuild_registry.py` 新建）

| # | 任务 | 预期改动 | 验证 |
|---|---|---|---|
| 2.1 | 扫描骨架 | vault 遍历 + `_` 前缀排除 + 计数报告 | `tests/test_rebuild_registry.py`：tmp vault 正常/排除/空库 3 例 |
| 2.2 | 重抓队列排除 | 读队列 JSON `old_paths` 集合过滤 | 同文件：命中排除/无队列文件 2 例 |
| 2.3 | 三级 URL 提取 | 复用 `migrate_gate._main_url`；失败落标题指纹 | 同文件：fm 命中/正文命中/双失败 3 例 |
| 2.4 | 幂等 merge + 容器页 | 已登记保留人工字段；同名容器页单列 | 同文件：二次跑 diff 为空 1 例 |
| 2.5 | 真库验收 | `--vault "$env:OBSIDIAN_VAULT_PATH"` dry 跑 | 报告数字与 migrate_gate 扫描对账（652 扫描/426 排除量级） |

### 阶段 3 · 判定入口切换

| # | 任务 | 预期改动 | 验证 |
|---|---|---|---|
| 3.1 | 入队检查 | `monitors/run.py` discover 后 batch 查询过滤 | `tests/test_enqueue_gate.py`：命中不入队/未命中入队 2 例 |
| 3.2 | scys 切换 | `scripts/scys_batch_fetch.py` done_ids→is_summarized | 补 2 例单测 |
| 3.3 | UP 补齐切换 | `scripts/fetch_up_range.py` 入队前预查 | 补 2 例单测 |
| 3.4 | 落盘登记扩展 | `articles/main.py` register 传 source/note_type/folder | `tests/test_sync_ledger.py` 断言更新 + 2 新例 |
| 3.5 | 全量回归 | 全仓测试 | 全绿 |

### 阶段 4 · 系列课逐 URL 改造

| # | 任务 | 预期改动 | 验证 |
|---|---|---|---|
| 4.1 | 五步管线函数 | `videos/main.py` 新 `summarize_series_episode(url)` 单集入口 | `tests/test_series_episode.py`：五步各 1 例 + 命中跳过 1 例 |
| 4.2 | 增量面接線 | `monitors/run.py` 系列单集发现 → ensure 容器 → 4.1 入口 | 补 2 例（新建容器/已有容器） |
| 4.3 | 点名补齐命令 | `scripts/fetch_up_range.py` 或新 `scripts/backfill_series.py`：列全集→过滤已登记→逐条入队 | 补 2 例 |
| 4.4 | 退役 1/2/3 | 删 `_handle_bilibili_series` 贪婪逻辑、`shared/series_state.py`、`series_state.json` | 相关测试删除/改写；`py_compile` 全仓 |
| 4.5 | 退役 4/5/6 | 删 `apply_pending_series.py`、`shared/series_manifest.py`、sanitize 集数校验 | 同上 |
| 4.6 | 文档收口 | AGENTS.md/RULES.md/monitors/README.md 系列课章节改写；`DECISION-20260908` 归档 | 人工审阅 |

### 阶段 5 · 消费 426 条重抓队列（必须最后，D5）——✅ 驱动已落地，消费进行中

队列 `kind=bili_video` 走 videos 管线、`article` 走 `articles.skill_main`、`manual_no_url` 人工补链。**排在阶段 4 之后**——否则系列集重抓会触发已退役前的贪婪逻辑。

**实施（2026-09-09 完成）**：`scripts/consume_migrate_queue.py`（TDD，`tests/test_consume_migrate_queue.py` 20 例，全量 720 passed）。四命令：`--plan` / `--fetch --limit N` / `--clean` / `--cleanup-old [--apply]`。跳过序：registry 命中（url 或 old_titles 批查）→ staging 已有 url → manual 已有 url → fails≥3 → limit 截断。B站重传标题匹配 difflib ratio≥0.55，失配/失败 3 次/无 URL → manual。412 熔断立即中止。顺带修复 `dedup.mark_summarized` 漏传 title 缺口（title-only 落键此前被静默丢弃，与查询侧对称，无回归）。

**扩展（2026-09-10）**：新增**无 CC 暂缓机制**（消费队列从 4 命令扩到 6）——`--fetch` 探测到无字幕即标 `deferred`（不重试、不占 `--limit` 配额），`--defer-failed` 批量收敛历史无CC失败，`--classify-deferred` 三分类（误标回队 / `no_cc_confirmed` / `removed_video`）；同日 **v2 修熔断盲点**（`fetch_transcript` 吞异常返 None，412 到不了外层熔断——classify 改 view API 先行：连续 2 次请求层异常=熔断 abort、删除码直接定档、环境健康才探测字幕）。测试 20 → **43 例**。详见 `docs/decisions/DECISION-20260910-nocc-deferred.md`。

**进度（2026-09-09）**：--plan 426 条中 registered 81 → 消费 12 条后 **93**；两轮 fetch 共入队 23 条、转 manual 6 条（全部 `url_changed`：B站重传 URL 指向不同视频，风险 4 按设计拦截）；staging 存 11 条待消费；manual 清单累计 7 条（6 url_changed + 1 manual_no_url）。

**续跑指南（新会话照做即可）**：
1. 派子 Agent 消费 `notes/_scraped/migrate/pending_summaries.json` 存量条目：按条目内预计算 prompt 总结 → `python articles/_save_summary.py <临时md> --url <url> --author <author> --tags <tags> --title <title> --folder <条目folder>` 落盘（自动登记）。
2. `python scripts/consume_migrate_queue.py --clean --archive-dir "<vault同级>\_migrate_gate_archive"` 出队。
3. 循环 `--fetch --limit 15` → 消费 → clean，直至 to_fetch 清零。
4. `--cleanup-old`（先 dry-run 核对，再 `--apply` 删旧文——登记命中才删）。
5. manual 清单交付用户（`notes/_scraped/migrate/manual_queue.json`）。

## 8. 边界与风险

| # | 风险 | 应对 |
|---|---|---|
| 1 | rebuild 后同内容多键（新旧 URL 格式差异） | 1.4 归一化修复前置；rebuild 报告列疑似项人工合并 |
| 2 | title_fp 误伤（不同内容同名） | 指纹键仅 rebuild 兜底产生，报告单列数量（预期极少）供抽查 |
| 3 | 退役期间新旧逻辑共存窗口 | 阶段 4 一次会话内完成 4.4+4.5，不留半退役态 |
| 4 | 426 条重抓 B站 URL 已变 | 沿用 v3 铁律：队列 `old_titles` 标题相似度匹配兜底，不能只凭 URL |
| 5 | 并发写登记表 | 复用 `_index_lock`；rebuild 离线单进程 |
| 6 | 失败集反复入队死循环（失败不登记→下次再检出） | 补齐队列失败上限：同集累计 3 次转人工清单，不自动重试（D8，任务 4.3c） |

## 9. 状态文件去向总表

| 文件 | 去向 |
|---|---|
| `notes/_meta/sync_ledger.json` | 升格重命名为 `summary_registry.json`（自动搬家） |
| `monitors/state.json:seen` | 降级为缓存，保留（监控窗口游标仍有用），判定不再依赖 |
| `monitors/series_state.json` | 删除（缓存语义，不迁移） |
| `notes/<系列名>/_manifest.json` | 删除（随系列扫描清理） |
| scys `done_ids`/`fetch_results` | 降级为缓存保留，判定改查登记表 |
| `pending_summaries` / `pending_refetch` | 保留队列职责，判定走登记表 |
| `monitors/pending_series.json` | 删除 |

## 10. 决策记录

| # | 决策点 | 拍板 | 来源 |
|---|---|---|---|
| D1 | 登记表文件名 | `summary_registry.json` 重命名 + 自动搬家 | 本方案（改动面已核实仅 3 文件） |
| D2 | 426 条待重抓条目 | **完全不登记** | 用户 2026-09-08 拍板 |
| D3 | 系列课六件套 | **全部退役**，职责由登记表+逐 URL 管线接住 | 用户 2026-09-08 拍板 |
| D4 | 页码 | 降级为目录命名装饰，不再校验冲突 | 实证：同集不同页码共存无害 |
| D5 | 426 队列消费 | 排在系列课改造之后 | 防旧贪婪逻辑回归 |
| D6 | 首跑自动补齐系列课 | **采纳渐进式**：与点名补齐同函数，`SERIES_BACKFILL_BATCH`（默认 15）分批 + 登记表跳过，请求量论证见 §5.3 | 用户 2026-09-08 追问后定稿 |
| D7 | 实施节奏 | 先本文档确认，再 TDD 动工 | 用户 2026-09-08 拍板 |
| D8 | 增量补缺 | **维持 D6：增量不自动补缺，点名才补**（A 选项，额外请求 0）；补齐队列加失败上限护栏（同集累计 3 次转人工） | 用户 2026-09-08 拍板 |
