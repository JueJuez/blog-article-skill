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
| 每日跑一遍订阅（B站UP / scys，无 captcha） | `python monitors/run.py --mode auto --apply` | 手改队列 JSON |
| 跑公众号 / 公众号增量 / weread 直连源（**撞码由 agent 自动截图识图过码**） | `python monitors/run.py --mode weread --apply` | `--mode auto` 里跑 weread（撞人机验证无人值守过不了，默认已排除） |
| 新订阅一个 B站UP | `python monitors/run.py --subscribe --uid <id> --name <名> --category <类>` | 手搓 `subscriptions.json`（公众号/scys 才手改） |
| 公众号历史回溯 | `python monitors/run.py --weread-backfill --names <名> --since <日期> --apply`（未翻到再加 `--batch` 续批） | ❌ `--backfill`（旧 wewe-rss 代理，已永久停用） |
| **把库里已有的旧笔记按新版 prompt 重写** | **「3. 存量重做」四步**（`build_resummarize_queue.py` → `build_resum_batches.py` → 派子 Agent → `resum_save_batch.py`） | ❌ `save_summary_only` / `_save_summary_from_file.py`（会重新推导文件名 → `-N` 副本） |
| 某篇笔记的源文本丢了，只想补源 | `scripts/fetch_bili_by_bvids.py` / `scripts/fetch_scys_by_ids.py` | `scys_batch_fetch.py` / `fetch_up_range.py`（会被 dedup 闸门跳过） |
| 补齐某个 UP 主全部历史视频 | `scripts/list_up_videos.py --uid` → `scripts/run_with_env.py -- python scripts/fetch_up_range.py 1 N --uid ... --author ...` | 直接跑 `fetch_up_range.py`（不读 `.env`，cookie 取不到） |
| 补齐生财有术某领域全部帖子 | `python scripts/launch_scys_backfill.py`（数小时任务） | 前台直跑（会话结束被回收） |
| 补齐/重做某系列课整季 | `python scripts/backfill_series.py --series <名>` | ❌ `monitors/apply_pending_series.py`（文件已不存在） |
| 迁移期体检 / 清异常 | `python scripts/migrate_gate.py --vault <path> --scope=` + `--apply` | — |
| 归档开源项目到项目库 | `python tools/project_import/assets/main.py "<repo url>"` | — |
| 把一篇已有笔记出分享版 HTML | `python scripts/export_note_html.py "<md路径>"` | 见「2.1 分享版 HTML 出口」；新笔记分享不用这条（落盘时带 `share_html`） |
| **重做前先判「这篇到底该不该改」** | `python scripts/check_resum_necessity.py --queue notes/_meta/resummarize_queue.json`（见 §3.1） | 直接从全量队列开重做（会把好稿改坏，且无法回退） |
| 建/重算固定评估集（改动的度量基线） | `python scripts/build_eval_set.py` → 填 `forms/`（人工或固定口径 AI 标注） → `python scripts/score_eval_set.py --dir <同目录>`（见 §3.3） | 凭单次生成效果说「变好了」 |
| **落盘后给笔记做独立语义复审**（不阻塞落盘、不占前台） | `python scripts/run_async_review.py --emit` → 子 Agent 审 → `--ingest`（见 §3.2） | 打开 `NOTE_QUALITY_GATE=1` 把审核卡在落盘前 |
| 查「段落悬空指代」到底是不是问题 | `python scripts/scan_dangling_refs.py`（只读，见 §3.4） | 直接建改写轮／凭感觉改 prompt |

## 2. 增量生产管线（抓新内容 → 落新笔记）

**入口**：`python articles/run.py "<url>"`（或 `from articles import skill_main`）/ `python videos/run.py --url`
**特点**：文件名由 `generate_filename(publish_time + title)` 推导，落进 folder 路由或【待归类】。

**零件（非入口）**：`articles.fetch_web_content`（scys 自动分流 CDP）、`summarize_content`（调 AI）、
`save_summary_only`（去重闸门 + 机械门禁 + 路由，是 `skill_main` 的内部分支）、
`save_summarized_article`（最终落盘；2026-09-17 起它同时服务存量重做，见下；2026-10-06 起支持
`share_html=True` 落盘后自动出分享版，见 2.1）、
`articles/manager.py: OutputManager`（飞书/Obsidian/本地三选一）。

**质量零件更新（2026-10-14，入口不变）**：9种模板共享 `prompts/templates.py:SOURCE_FIDELITY_RULES`；硬必备结构以同模块 `SECTION_CONTRACTS` 为真源，由 `prompts/content_signals.py` 消费。无依据的公式、反例、全书地图、问答、排名和CTA允许省略，有依据的重要内容仍须覆盖。会话模型自检与父Agent语义抽检按 RULES §4.6 执行；条件模块是否有依据不能由机械标题检查证明。实施与效果边界见 [模板质量计划](plans/PLAN-20261014-template-quality.md)，不据此批量重写旧库。

**类型与过程追踪（2026-10-15，批次2）**：分类理由和版本由 `prompts/classify.py:classify_note_type_with_evidence` 提供；证据不足保守general。`prompts/provenance.py` 统一派单的实际type、auto/manual/legacy来源、源文本hash和prompt hash。文章/视频降级、监控/scys/UP入队、存量重做派单复用该实现，沿现有保存入口传入登记表；元数据不参与去重键和文件名。prompt_scope区分派单模板、文章最终调用模板和视频各次调用；hash不证明模型实际遵循prompt，审核状态不冒充全文通过。

**证据检查（2026-10-15，批次3）**：沿现有verifier/content_signals与只读审计入口加入明确原话定位、资源名称/入口绑定和常量复算检查。共同证据结构、适用语法与不判定边界见 [证据契约](../references/note-evidence-contract.md)。确定缺陷进issues，不受force豁免；疑点进入现有复核队列，不把机械检查当全文语义验收。

批次4收尾复核U2时，数值及股票代码锚点发现媒体文件名标识噪声；沿共同数字正文范围过滤文件引用，不降低召回阈值，正文真实数字仍保留。见[收尾决策](decisions/DECISION-20261015-batch4-closeout.md)。

**全文语义审核（批次4代码实施，真实效果另验）**：复用`verify_note`和保存前公共准入；开启`NOTE_QUALITY_GATE`时短源全文，长源分块加邻文并汇总，审核不完整阻止写入，force不豁免。关闭时不新增外部调用，状态保留未审。失败候选、源与报告进入现有复核队列及拦截台账；详见[全文审核决策](decisions/DECISION-20261015-fulltext-review.md)。

**提取清单内化（A4，2026-10-18，入口不变）**：`FORCE_AGENT_MODE=1` 下无外部 provider，提取只能由执行模型在**同一次派工**里做，故清单指令按源长条件拼进条目 prompt（`prompts/templates.py:get_note_prompt` → `prompts/extract_checklist.render_extraction_rules`）：源长 >10000 字要求「先列清单、再成文」（清单是自查表，不得当大纲）；<2000 字只给 T0「源撑不起」事前报告钩。落盘前由 `prompts.extract_checklist.process` 把清单块**剥离出正文**、逐条机械校验「原句能在源里命中」（命中不了丢弃）、再对成文做机械对账（数字/专名/专名类中文名），未命中并入异步审核理由。**全部是报告项**：不计分、不拦盘、不触发重交；清单按源 hash 存档于 `notes/_meta/extract_checklists/`。决策与 Loop 0 结果见 [A4 落点决策](decisions/DECISION-20261018-a4-agent-mode-landing.md) 与[知识层计划](plans/PLAN-20261018-knowledge-layer.md) §6 A4。**尚未接线**：`videos` 单集/系列路径（其 prompt 未传源长，故当前不注入，也不需剥离）。

会话独立审核可由`save_pending.py --review-file <报告.json>`随稿提交；存量重做读取稿件旁的`<稿.md>.review.json`（或批次条目的`review_file`）。报告必须绑定完整源、实际清理后的稿件正文及派单prompt，明确不同于作者的审者ID与全文字符范围。协议见[全文审核说明](../references/fulltext-review.md)，未知历史版本不得补造。

已有draft-only/Landing两段出口同样受审核准入约束：新草稿元数据携带源/正文/审核版本和格式化文件hash；显式审核时旧草稿无版本、或草稿文件被改动，都保留草稿并停止Landing，不能绕过新门禁。

## 2.1 分享版 HTML 出口（2026-10-06）

**适用场景**：想把一篇笔记发给别人看（浏览器打开即读，无需 Obsidian / 飞书）。产物是
**单个自包含 .html**（CSS / Chart.js 全内联、零外链资源），默认与笔记同目录同名。

| 路径 | 触发方式 | 说明 |
|---|---|---|
| A · 存量笔记 | `python scripts/export_note_html.py "<md路径>"`（vault 内唯一文件名也行） | 手动按需导出 |
| B · 新笔记分享 | 用户说「**分享 / 出分享版 / HTML 版**」→ 落盘调用带 `share_html:1`（`save_summary_only` 的 input_data 键 / `save_summarized_article` 参数） | 没说就只落盘，不产 HTML |

**转换规则（确定性代码，零 AI）**：Obsidian callout → 样式块；`chart` 数据块 → Chart.js 图
（Chart.js 缺失时降级数据表格）；元数据行 → 页眉；`##` 标题自动生成目录。
**chart 数据块是单一格式真源**（```chart：`type: bar|line|pie` + `title` + `labels` + `series[{title, data}]`，
与 Obsidian Charts 插件**原生 schema 一致**，见 `prompts/templates.py` §质量纪律 4），
Obsidian Charts 插件与 HTML 导出器是它的两个消费者，互不另外定义格式。

**零件（非入口）**：`articles/html_export.py: render_note_html` / `export_note_html`（含自包含校验）；
`articles/main.py: _export_html_safe`（落盘钩子，失败只告警不拦落盘）。

## 3. 存量重做管线（旧笔记重写 → in-place 覆盖原路径）

**适用场景**：库里已有笔记，需要按新版 prompt 重写（阶段C 就是这么跑完 690+98+24 条的）。

| 步 | 入口命令 | 说明 |
|---|---|---|
| 1 | `python scripts/build_resummarize_queue.py` | 从 `summary_registry.json` + vault 反查，挑出「笔记在库 + 源可定位」的条目 |
| 1.5 | `python scripts/check_resum_necessity.py --queue <上一步队列>` | **新增（T13）**：只判「该不该改」，产 `<queue>.necessity.json`；`keep` 的别再进重做批次（见 §3.1） |
| 2 | `python scripts/build_resum_batches.py [--queue <necessity.json>]` | 按源长切批（>10000→4 条/批，4000~10000→8，<4000→16）+ 逐条预计算 prompt；传 `--queue` 只把「建议重做」的做成批 |
| 3 | 派子 Agent（**必读** `notes/_meta/resum_batches/DISPATCH_PROMPT.md`） | 产出 `_tmp/resum_c/out/<batch>_<NN>.md`，原阶段并发观察不作为当前平台保证；并发量按本次配额与返回结果调整 |
| 4 | `python scripts/resum_save_batch.py <batch.json> <out_dir> [--force]` | **唯一落盘入口**：校验主题词行 + 机械门禁 → 候选先暂存 + 与旧稿 A/B（明确更优才替换，平手保留旧稿；`--force` 无条件替换）→ 调生产函数 in-place 覆盖 `note_path`；被拦的稿也会暂存留档（见 §3.1） |
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

### 3.1 重做保护：先判该不该改，再谈怎么换（T13 / T8，2026-10-18）

**问题**：以前是「从登记表演算一批 → 无差别重做 → in-place 直接覆盖」。旧稿是好的也会被重写，
重做稿更差就没有退路（Brain 的 Update 阶段有「**也可以选择不改**」，我们缺这一层）。

**现状能力（已完成部分）**：

- `scripts/check_resum_necessity.py --queue <队列>`：用**既有判据**判 `keep` / `redo`，零新增口径
  （零件 `shared.note_audit.necessity_of`）。判据只取「生产本来就会拦」的那一档
  （`content_signals` hard 集合 + 证据确定性缺陷），**形态信号不构成重做理由**。
  2026-10-18 全量跑：1396 条候选 → 建议重做 **391** / 建议跳过 **1005**（报告
  `notes/_meta/eval/necessity_all_report.md`）。**判定不等于执行，取舍仍由人定。**
- `scripts/build_resum_batches.py --queue <necessity.json>`：只把 `redo` 那批做成子句，
  避免「为了改而改」。
- `resum_save_batch.py` 四条**有稿可留**的失败路径（缺主题词行 / 机械门禁 / 内容硬失败 / 落盘异常）都会
  `shared.draft_stage.stage_blocked` 留档到 `notes/_meta/staged_drafts/<日期>/`（含
  `note_sha256` / `source_sha256` / `prompt_sha256`），旧稿原样留库 → 才有「先比后换」。
  `缺文件` 一类无稿可暂存（子 Agent 从未产出，旧稿不受影响）；`NOTE_PATH_EMPTY` / 派单校验失败
  属批次条目问题，早期中止不入暂存。

**受控替换（T8，2026-10-18）**：`resum_save_batch.py` 不再是「过门禁即覆盖」—— 候选稿先暂存
（`redo_candidate`，含三 hash）→ 与旧稿按**同一套确定性缺陷**（T13 硬集合：锚点召回 / 照搬 /
结构缺失 + 证据确定性缺陷）A/B 比较 → **明确更少缺陷才替换**；平手/更差保留旧稿
（Brain「也可以选择不改」；判定零件 `shared.controlled_replace`，判据零新增）。
`--force` 跳过比较无条件替换（逃生舱，兼作内容门禁豁免；机械门禁不豁免）。每次决策追加
`notes/_meta/replace_decisions.jsonl` 台账并回写暂存 meta（`kept_old` / `replaced` / `save_failed`）。
语义腿沿用保存链路 `admit_candidate`：带独立审核报告（`review_required`）时强制独立会话 /
全文覆盖 / passed，不过即抛错（稿进暂存）。决策记录见
[DECISION-20261018-controlled-replace](../decisions/DECISION-20261018-controlled-replace.md)。

### 3.2 落盘后异步审核（T7 ②，2026-10-18）

**问题**：全套独立语义审核（`prompts/semantic_review.review_fulltext`：分块全文、独立会话审者、
`coverage=full` 才认）**早就建好了，但只能卡在落盘前**，开关 `NOTE_QUALITY_GATE` 默认 `0`
→ 不开就永远不审，开了就拖慢生产。

**改法**：落盘（照常）→ 入队（一行 JSONL，零 AI 成本）→ 有空/有人时消费。

| 步 | 入口 | 说明 |
|---|---|---|
| 入队 | 自动（`save_summarized_article` 两处本地落盘点） | 开关 `NOTE_ASYNC_REVIEW`：`0` 关 / `1` 触发式（默认：`review_flags` 非空**或**按 `NOTE_ASYNC_REVIEW_RATE`=0.15 抽样）/ `all` 全量 |
| 看队列 | `python scripts/run_async_review.py --stats` | 状态分布 |
| 派单 | `python scripts/run_async_review.py --emit --limit 5` | 出包到 `_tmp/async_review/in/<id>.md`（口径 = `QUALITY_GATE_PROMPT` 原文） |
| 回收 | `python scripts/run_async_review.py --ingest` | 读 `_tmp/async_review/out/<id>.json`，按 `parse_quality_gate` 收口 |
| 直调 | `python scripts/run_async_review.py --run --limit 5` | 有外部 provider（`FORCE_AGENT_MODE=0`）时走真 `review_fulltext` 全流程 |

**硬约束（改前必读）**：

- **hash 是命门**：回收时重算笔记 hash，对不上 → 标 `stale` 丢结论（后台审时稿可能已被重做/手改）。
- **源快照**：入队时把当时的源文本快照到 `notes/_meta/async_reviews/sources/<id>.txt`
  （只有被抽中的那部分）。`_raw_` 会被清理、重抓会覆盖，不留证就没法复核。
- **判据只有一份**：回收走同一个 `parse_quality_gate`，不因「异步」放松（`coverage=full`、
  P1 一票否决、`reviewer_role` 不得是 author/self_check）。
- **不写 vault**：结论落在队列状态 + `notes/_meta/async_reviews/` + `notes/_reports/async_review_<日期>.md`。
  笔记正文/frontmatter 一个字段都不加（下游 KB 按段落检索，塞非生产字段是污染）。
- 队列与旧 `notes/_review_queue.jsonl` **是两回事**：后者是机械 `review_flags` 的无人值守降级，
  历史上被假信号淹过（300+ 条后清空），不要混用。

### 3.3 固定评估集（T3，2026-10-18）

**为什么要**：唯一量化的保留度指标（硬锚点召回）在 1298 对语料里**只覆盖 37%**（其余 63% 源锚点
数 < `MIN_ANCHORS`，判据不适用）。没有评估集，后续任何改动都只能「看起来更好」。

```bash
python scripts/build_eval_set.py                    # 30 篇：源长 5 桶 × 6，桶内按类型轮转，种子固定
# 填 forms/Axx.md 的 MUST_KEEP 区（人工或固定口径 AI 标注）→ 笔记里有的勾 [x]
python scripts/score_eval_set.py --dir notes/_meta/eval/20261018-knowledge-layer
```

- 产物全在 `notes/_meta/eval/`（**不入库、不写 vault**），零件 `shared.eval_set`。
- 只对抽中样本跑既有四类判据（`shared.note_audit.audit_one`），回填 `note_sha256`/`source_sha256`。
- **缺额不补抽**：某桶样本不足就如实少抽，不把缺额倒给富桶（会把 >20k 这种稀缺层抽干）。
- **防覆盖护栏**：`build_eval_set.py` 重跑时检测到已填写的表单会拒绝覆盖（`--out` 换目录或
  显式 `--force` 才重建）—— 抽样可复现 ≠ 填写可以重建，人工/标注清单是资产。
- **当前状态（2026-10-18）：30 篇已按固定口径由独立子 Agent 标注完毕（AI 标注基线）**：
  整体保留率 **84.2%**（249/299；当日经口径修订剔除 2 条不绑流程的纯推广条目，原 83.6%），
  按桶 0-2k 89.2% / 2-5k 90.7% / 5-10k 86.7% / 10-20k **72.0%** / >20k 82.2%，最低 A23 8%
  （严格口径下的粒度敏感篇目）。方法、边界与抽查记录见
  `notes/_meta/eval/20261018-knowledge-layer/ANNOTATION.md`；评分明细 `score_report.md`。
  改前基线就此可用：改动后**不要重建表单**，直接重跑判分即得同口径对照。

### 3.4 段落悬空指代扫描（T4，2026-10-18）

```bash
python scripts/scan_dangling_refs.py                # 全库只读；--limit 200 --sample 15 先看样本
```

下游按**段落**检索，段首「这个方法 / 如上 / 后者」会让孤立片段变成残缺事实 —— 所以先问「是不是问题」再
决定要不要动 prompt。2026-10-18 全库（2333 篇 / 30905 段）：block 首命中 **283 处 = 0.92%**（监控 0.96% /
我的总结 0.82% / 生财有术 1.12%）→ **不成系统性问题，不建改写轮、不改 prompt**。
零件 `shared.dangling_ref`；复扫随时可跑，判据是「命中率 <2% 即不立项」。

### 3.5 Loop 0 提取覆盖率（A4 调参环，2026-10-18）

```bash
python scripts/loop0_extract_coverage.py --emit-prompts   # 写版本化提取指令 + 30 篇任务书
# 派子 Agent 按 <eval-dir>/loop0_checklists/_INSTRUCTION.md 产出 A01..A30 的清单（只提取、不成文）
python scripts/loop0_extract_coverage.py                 # 机械粗筛 → loop0_report.md + loop0_suspects.json
```

**改提取 prompt 就转这个环**（PLAN-20261018 §8 三层循环）：30 篇评估源的「标准答案」就是 A1 必留清单，
把 T15 提取输出逐条对清单算覆盖率 —— 零成文成本。判据分两层：机械粗筛（bigram 覆盖率 ≥0.45 **或**
与清单共享 ≥6 字连续片段）＋ **模型裁可疑项**（机械会假阴性：A1 条目是「陈述＋依据」打包句，清单是
原子条目）。产出的 `extract_miss` 就是提取 prompt 的靶。只读生产数据；报告落在评估目录（`notes/`，不入库）。

### 3.6 对照轮：重新生成 → 同清单复核 → 判分（§12.3 #14）

```bash
python scripts/build_round.py build --subset all \
    --prompt-profile a4only --round-dir <eval-dir>/round_A4_formal
#   派生成子 Agent（按 prompts/Axx.md 写 notes/Axx.md）→ 派独立复核子 Agent（按清单勾 forms/Axx.md）
python scripts/build_round.py normalize --round-dir <round-dir>   # **必跑**：剥清单块
python scripts/build_round.py measure --round-dir <round-dir>     # 变长幅度 + 复核完成度
python scripts/score_eval_set.py --dir <round-dir> --report <round-dir>/_score.md
```

**测生成端改动（A4/A6）不能用「对已填表单重跑判分」**——那量不出生成差异。正确流程是同一批源
**重新生成**、用**同一份清单**在新目录重新勾选、再判分，并记录笔记变长幅度。本工具把这条流程固化，
且**绝不碰原评估目录与 vault**（新笔记写 `<round-dir>/notes/`）。防覆盖判据按「**已勾选**」算复核劳动
（轮次表单的条目是复制来的，不能用 `touched_forms` 判）。判分前先看 `measure` 报的**复核完成度**：
一条都没勾的表单要么笔记为空、要么**还没复核**，后者会把 0% 当真实回归。

两个关键开关（2026-10-18 正式轮新增）：

- **`normalize` 必跑**：轮次稿带 A4 清单块，不剥则复核者照抄清单 → 保留率虚高、对照失效。
  原稿留 `.raw.md`；**复核子 Agent 只能读 `notes/Axx.md`，不得读 `.raw.md`**（派工提示词里要写明）。
- **`--prompt-profile a4only`**：按 A4 落地态（commit `4b26a3d`）回退 A6 的两段模板条款，用来**隔离
  A4 主效应**（§8 归因纪律：两个生成端改动不得合进同一对照）。定位不到 A6 段即报错，不静默通过。
  跑 A6 自己的核对用默认 `current`。
- 表单带 `<!-- DISTORTION -->` 区（§12.3 #15 失真伴随观察项）：**只观察、不计入保留率**，
  四类＝范围泛化/方向反转/案例替换/源外数字；判分输出 `distortion_observed_forms` /
  `distortion_forms_hit` / `distortion_counts`，口径见评估目录 `ANNOTATION.md` §七。

### 3.7 A6 条件必备信号离线测量

```bash
python scripts/audit_conditional_signals.py --corpus all
```

T9 的源侧机械信号（反例/公式/章节/对比/数值密集）升级为硬拦前，先在存量语料上看分布与误报率。
2026-10-18 实测 1298 篇：信号总触发 35 例（最高 `formula` 24 例）、**触发但缺该节仅 1 例**（疑别名过字面）
→ 误报率极低，可作硬拦。判据实现与阈值在 `prompts/content_signals.py`（③之二）。


## 4. 订阅监控管线

**入口**：`python monitors/run.py --mode auto --apply`（每日，仅 B站+scys）/
`--mode weread --apply`（公众号/weread 直连源，**撞人机验证需人在场过码**，与 auto 分开跑）/
`--mode first --apply`（首跑 30 天）/
`--parallel --mode auto`（并行 worker，默认仅 B站+scys）/
`--subscribe ...`（加订阅）/
`--weread-backfill --names X --since 日期 --apply`（weread 历史补全，未翻到再跑即续批）/
`--with-weread`（可选：在 auto/first 里强制带上 weread 源，仅当人在场准备过码时用）
⚠️ **2026-09-26 起 `--mode auto` 默认不含 weread**：公众号撞人机验证无法在无人值守时自动过码，
故抽出独立 `--mode weread` 单独跑（人在场时撞码→`scripts/weread_captcha.py --shot` 识图→`--grid --confirm` 过码→重跑）。
**零件**：`monitors/run_source.py`、`monitors/bilibili.py`、`monitors/weread.py`、`monitors/wechat.py`（旧代理，永久停用）、
`monitors/state.py`、`pending_summaries.json` 队列、`scripts/filter_pending.py`（派单前清洗，属于队列维护不是入口；2026-10-06 起清洗时**默认按当前模板重算队列内预计算 prompt**——长跑补齐进程加载的模板可能落后于仓库，老条目消费前统一刷新）。

**派单刷新边界（2026-10-15取代旧的无差别刷新）**：仅已知auto且源可读的条目重算type与prompt，manual保留type；legacy只报建议差异，原type/prompt/路径不改，只有chars而无全文的条目跳过。`python scripts/filter_pending.py --dry-run` 仅预览分类/刷新，不执行去重或写入（包含已总结条目）；默认命令继续去重并保存允许的刷新。报告含分类变更理由。本次开发仅运行dry-run，不改生产队列。`save_pending.py` 与通用保存/原路径重做入口校验已记录版本，失配报 `PROVENANCE_MISMATCH`，force不能豁免；新schema不为旧稿猜历史版本。

**队列消费落盘 runner（2026-09-24 新增 · 子 Agent 派单的唯一落盘出口）**：
`python scripts/save_pending.py --queue monitors|scys --key <url|topicId> --file <总结稿.md> [--force]`
统一处理 `.env` 前置 + `sys.path`（`python scripts/x.py` 时 sys.path[0] 是 scripts/）+ 两队列字段差异
（monitors 键 `url`、原文 `raw_file`、目录取条目 `folder`；scys 键 `topicId`、原文 `output`、目录
`生财有术/<project>`），避免子 Agent 手搓 `save_summary_only` 出错。
⚠️ 成功落盘**不改队列**：多子 Agent 并发 read-modify-write 会互相覆盖，收工统一跑 `filter_pending.py`
按 dedup 索引出队（2026-09-24 实测 22 条一次清空）。
✅ `--force` 修订**已会就地覆盖旧版**（2026-09-24 修）：`save_summary_only` 在 force + 该 URL 已登记时
自动定位旧版路径 in-place 覆盖（拿不到旧路径则退化为覆盖同名），**不再产 `-N` 副本**；
回归测试 `tests/test_overwrite_semantics.py::TestForceRevisionNoCopy`。
⚠️ force 只用于「该 URL 已落过盘」的修订场景，新条目别传（无旧版可覆盖）。
派单 prompt 里写死两个拦点可显著提高一次通过率：**原话必须改写**（20 字片段重合 >20% 硬拦）、
**硬锚点入正文**（发布日期 / BV 号或帖号 / 作者 / 关键数字，召回 <60% 硬拦）。历史一次并发 4 个子 Agent 未触发 429，不保证其他平台或当前配额同样可用。

**weread 直连公众号源（2026-09-19 起现役，PLAN-20260919）**：旧 wewe-rss 代理已死，
接替方案 `monitors/weread.py`。⚠️ **2026-09-26 起不再随 `--mode auto` 自动参与**：因撞人机验证
无法在无人值守时自动过码，默认 auto 已排除 weread（省 weread 日配额、免白开 CDP）。
需公众号时单独 `python monitors/run.py --mode weread --apply`（人在场过码）；
`--with-weread` 可在 auto 里强制带上（仅当人准备过码时）。`WEREAD_SOURCE_ENABLED=1` 仍须开启。
增量按时间窗（`WEREAD_WINDOW_DAYS=2` 基础、断跑补齐封顶 30 天，按 createTime 过滤、
与更新频率无关；首跑只建基线），正文走 mp 原文直链与普通公众号同管线。
异常自动处置：cookie 失效 → 自动截二维码等扫码（扫到即续抓）；验证码 → 会话内模型过码
（`scripts/weread_captcha.py`）；双层配额熔断（日 `WEREAD_DAILY_QUOTA=25` / 小时
`WEREAD_HOURLY_QUOTA=6`，到线跳过公众号源或停止补续批）。
**登录判定修正（2026-09-24）**：「是否过期 / 能否自愈」改以长期刷新令牌 `wr_rt` 为准，不再以短期 `wr_skey` 缺失误判未登录——`wr_rt` 在即先导航书架页触发服务端静默续期 `wr_skey` 再重试，续期失败（`wr_rt` 也被吊销）或无 `wr_rt` 才弹码。避免 `wr_skey` 短暂过期就白等扫码。
历史补全：`--weread-backfill --names X --since 日期 --apply`（未翻到再跑即续批）。
机制/防封纪律见 `references/weread-direct-source.md`。
**weread 探针工具（2026-09-18/19 探索用，非生产入口，勿接入管线）**：
`scripts/weread_api_probe.py`（登录态页内 fetch 探测）、`scripts/weread_cookie_export.py`
（cookie 导出/自测）、`scripts/weread_state.py`（登录态体检）、`scripts/weread_probe.py` +
`scripts/launch_weread_probe.py`（CDP 被动抓包 + DETACHED 启动器）、`scripts/weread_sig_probe.py`
（签名复用验证）、`scripts/weread_hook_sr.py`（hook __WRPA__.sr 记录真实调用）。

公众号登录恢复：只读本地cookie变化，不按5秒发探活；恢复后的当前账号业务请求只试一次并消费结果。请求统计与异常边界见 `references/weread-direct-source.md` §5 第4项（2026-10-10修订），入口命令不变。

## 5. 系列课管线

**入口**：`python scripts/backfill_series.py --series <名>`（清单外整季补齐）；
日常增量**无需手工跑**——单集随单篇队列消费（五步管线 `summarize_series_episode`）。
长任务：`scripts/launch_backfill_series_detached.py`。
**零件**：`summarize_series_episode`、`ensure_series_node`、`convert_series_to_hierarchy.py`。

## 6. UP 主全量补齐管线（B站）

**入口**：`scripts/list_up_videos.py --uid <UID>` → `python scripts/run_with_env.py -- python scripts/fetch_up_range.py 1 N --uid <UID> --author <UP名>`
→ `python scripts/filter_pending.py` → 派子 Agent 消费队列。
⚠️ `fetch_up_range.py` 自身不读 `.env`，必须经 `run_with_env.py` 包装（逻辑在 `shared.env.run_with_env`；
前身 `scripts/_run_with_env.py` 因 `_` 前缀被 `.gitignore` 忽略、从未入库，2026-09-20 改名入库，旧名仅留转发）。
**零件**：`videos/fetch.py`、`videos/asr.py`（ASR 兜底）、`monitors/asr_pool.py`、
`scripts/up_seasons.py`（官方合集→BV 映射，带缓存）。
**合集识别（2026-10-06）**：入队时按官方合集（ugc_season）显式传 series →
【监控】/<平台>/<账号>/<合集名>；命中账号 `series_exclude` 的合集跳过不入队；
映射拉取失败降级全部落【日更】。存量已落盘笔记分拣：`scripts/split_notes_by_season.py
--uid <UID> --author <名> [--apply]`（登记表 filename 精确反查优先 + difflib 兜底，
dry-run 默认，--apply 迁移并回写 summary_registry）。

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
| `prompts.verifier.verify_note_mechanical` | 机械门禁（零 AI）+ 内容硬失败分级（`content_blockers`：结构缺失/锚点召回不足/照搬未合成 → 拦 + retry；形态信号只软抽检）| `save_summary_only`、`resum_save_batch`、`videos.main._summarize_and_save`（实时落盘，source_text 取字幕原文，2026-09-18 补）、`videos.main._save_series_note`（reland 维护路径）|
| `articles.dedup` | 去重登记表 | 落盘链路 |
| `shared.env.load_env` | 加载 `.env`（`force_obsidian` 控制是否强制只写本地） | 所有需要 .env 的 CLI |
| `shared.note_audit.audit_one` | 单篇笔记抽检（四类判据 + A超长句取证） | `resum_audit_batch`、`audit_gate_signals` |
| `shared.cdp_session.SharedCdpSession` | 登录态抓取会话 | scys / 飞书 / YouTube |
| `shared.cdp_session.extract_body` | 从已渲染页面抽正文（选择器优先级，唯一实现；2026-09-18 收敛 articles/fetch.py + scys_batch_fetch.py 两份副本） | `SharedCdpSession._extract_body`、`_extract_body_scys`、`ScysBatchFetcher._extract_body` |
| `shared.note_audit.necessity_of` | 重做必要性判定（`keep` / `redo`，只用既有判据） | `scripts/check_resum_necessity.py` |
| `shared.draft_stage.stage_blocked` | 被拦稿件暂存（三 hash + 元信息，**永不抛异常**） | `save_summary_only`、`videos._summarize_and_save`、`videos._save_series_note`、`resum_save_batch` |
| `shared.controlled_replace` | 受控替换 A/B 判定（T13 硬集合，零新增判据；平手保留旧稿）+ 决策台账 | `scripts/resum_save_batch.py` |
| `shared.async_review.maybe_enqueue` | 落盘后入异步审核队列（按 env 抽样，零 AI 成本） | `save_summarized_article` |
| `prompts.extract_checklist` | A4 长源提取清单：按源长条件注入两阶段指令（>10000）／短源 T0 报告块（<2000）；剥离清单→机械校验（原句须命中源）→对账→按源 hash 存档，**全部报告项、永不抛异常** | `prompts.templates.get_note_prompt`（注入）、`save_summary_only`、`resum_save_batch` |
| `prompts.claim_coverage` | A5 论点级判据（**报告项**）：源侧断言候选＋疑似无落点＋失真嫌疑（源外数字/方向反转）；机械覆盖率**禁当指标** | `scripts/run_async_review.py --emit`（注入派单） |
| `prompts.content_signals.CONDITIONAL_SIGNALS` | A6 条件必备结构的源侧信号（反例/公式/章节/对比/数值密集） | `content_signals.structure_obligations` → 机械门禁硬拦 |
| `shared.eval_set` | 评估集分层抽样 / 基线测量 / 人工表单判分 | `scripts/build_eval_set.py`、`scripts/score_eval_set.py` |
| `shared.dangling_ref` | 段落悬空指代识别与统计 | `scripts/scan_dangling_refs.py` |

## 10. 能力索引：想做这件事时，先查有没有现成的

> **动刀前先扫这一节。** 下面每个能力项目里都已经有实现，直接调用即可；
> 只有确认这里没有、或现有实现差一层薄改造时，才允许新写函数。

| 想做什么 | 现成的调用点 | 备注 |
|---|---|---|
| 抓任意文章正文（scys 自动分流 CDP 登录态） | `articles.fetch.fetch_web_content` | 入口：`articles/run.py` |
| 抓 B站字幕 / 转写 | `videos.fetch.fetch_transcript` / `fetch_bilibili_transcript` | 入口：`videos/run.py`。**充电专属不阻断**（有字幕就用字幕）；源不完整由 ASR 层的覆盖率闸门兜住 |
| 无字幕视频转写（ASR） | `videos.asr.transcribe_video` / `transcribe_audio_chunked` | >30min 自动分片；**唯一源质量硬闸门**：本地音频 < 视频时长×90% 即拒绝转写（充电试看/下载截断）；⚠️ **依赖版本有红线**：`ctranslate2==4.5.0`、`onnxruntime==1.19.2`、`nvidia-cublas-cu12` + `nvidia-cudnn-cu12`(cuDNN 9) —— 见 `references/asr-bilibili-sandbox.md`「本机运行环境版本要求」；FunASR 中文特化后端（独立 venv，见同文件「坑 7」）支持 `ASR_BACKEND=auto` 按语言自动路由（中文→FunASR、其余→Whisper），默认仍 whisper |
| 抓 YouTube 字幕 | `videos.fetch.fetch_youtube_transcript` / `..._cdp` | 登录态走 CDP |
| 字幕清洗（填词/去重/合并） | `shared.subtitle_clean.preprocess_segments` / `preprocess_text` | 已在 fetch 链路自动接入 |
| 长文分块 / 两阶段总结 | `shared.chunking.chunk_text` / `two_stage_summarize` | |
| 判重 / 登记 / 跨源去重 | `articles.dedup.is_summarized` / `mark_summarized` / `find_cross_duplicate` / `normalize_title_for_match` | ⚠️ 判重用的标题归一叫 `normalize_title_for_match`（2026-09-18 改名），与 `shared.title_norm.normalize_title`（落盘标题清洗）**同名不同义，不可互换** |
| 单篇笔记抽检（四类判据） | `shared.note_audit.audit_one` / `evidence` / `score_of` | 2026-09-18 下沉，resum_audit_batch 与 audit_gate_signals 共用 |
| 加载 .env / 包装子命令 | `shared.env.load_env(force_obsidian=False)` / `shared.env.run_with_env(cmd)`（CLI 壳 `scripts/run_with_env.py`） | 2026-09-18 收敛 load_env：此前 scripts/ 下 6 份各自实现（`_run_with_env` / `_save_summary_from_file` / `resum_save_batch` / `fetch_bili_by_bvids` / `land_scys_by_key` / `persist_summary`）。2026-09-20 再把「加载 .env 后 subprocess 执行」下沉为 `run_with_env()`——前身 `scripts/_run_with_env.py` 被 `.gitignore`（`scripts/_*`）忽略却未入库，是 `launch_backfill_series_detached` / `launch_fetch_up_detached` 的必需环节，换机即断 |
| 文件夹路由 | `shared.routing.resolve_folder` / `category_from_tags` | 落盘自动调用 |
| 语义标签（四维度） | `shared.note_classify.infer_semantic_tags` / `extract_and_strip_topics` | 落盘自动调用 |
| 笔记类型判定 | `prompts.classify.classify_note_type` | |
| 生成总结 prompt / 篇幅目标 | `prompts.templates.get_note_prompt` / `render_coverage_guide` | |
| 笔记格式化（标签行/来源链接） | `prompts.templates.format_note_with_prompt` | |
| 笔记 → 自包含分享版 HTML | `articles.html_export.render_note_html` / `export_note_html` | 入口见 §2.1（CLI 或落盘 `share_html`）；chart 块/callout/目录全机械转换 |
| 机械门禁（零 AI） | `prompts.verifier.verify_note_mechanical` | |
| 内容判据（锚点/破碎/结构/照搬） | `prompts.content_signals.content_flags` | |
| 抽检留痕 / 根因台账 | `prompts.review_rubric.log_review` / `queue_for_review` | |
| 标题归一 / 文件名净化 | `shared.title_norm.normalize_title`、`shared.sanitize.sanitize_filename` | |
| 调外部 AI | `articles.ai_provider.call_ai_summarize` / `get_ai_provider` | |
| 飞书节点增删改移 | `articles.feishu.FeishuOutput.ensure_folder_path` / `move_node` / `delete_node` | 2026-09-04 起默认不写飞书 |
| 飞书总览索引 | `shared.feishu_overview.ensure_overview` / `add_entry` / `rebuild` | |
| 带登录态抓页面（CDP） | `shared.cdp_session.SharedCdpSession` | 委托用户级 SKILL |
| 从渲染页抽取正文 | `shared.cdp_session.extract_body(page)` | 2026-09-18 收敛：此前 3 份逐字相同实现（`articles.fetch._extract_body_scys` / `scys_batch_fetch.ScysBatchFetcher._extract_body` / `SharedCdpSession._extract_body`），现统一为模块级单点，改规则只改一处 |
| 飞书外链全文（懒加载） | `scripts/feishu_ext_refetch.py: collect_full_text` | `.bear-web-x-container` 增量滚动 |
| B站 cookie 健康 / 刷新 | `monitors.bilibili.refresh_cookie_if_dead`、`videos.set_cookie.set_bilibili_cookie` | **克隆死即重克隆闭环（2026-09-24）**：CDP 活会话提取拿不到有效 cookie（克隆会话已被服务端作废）→ 立即 `shared.cdp_session.force_refresh_clone()` 强制全量重克隆默认 profile 取活会话、重试一次。用户拍板：克隆死就立即触发，不加任何守卫（自动监控轮次亦然） |
| 监控去重状态 | `monitors.state.get_seen` / `mark_seen` | |
| 滚动日志 | `shared.rolling_log.append_rolling` | |
| 登记表全量重建 | `scripts/rebuild_registry.py` | vault 是唯一真源，登记表可重建 |
| 判「旧笔记值不值得重做」 | `shared.note_audit.necessity_of` → `scripts/check_resum_necessity.py` | 见 §3.1；only-hard 判据，形态信号不算理由 |
| 被拦的候选稿不要丢 | `shared.draft_stage.stage_blocked`（=`stage_rejected` + 打印） | 见 §3.1；**永不抛异常**，写不进只告警 |
| 重做稿与旧稿谁更好 | `shared.controlled_replace`（`assess_text` / `decide` / 台账） | 见 §3.1；T8 受控替换，**平手保留旧稿**，`--force` 才是无条件替换 |
| 落盘后做独立语义复审 | `scripts/run_async_review.py --emit/--ingest`、`shared.async_review.maybe_enqueue` | 见 §3.2；别用 `NOTE_QUALITY_GATE` 卡落盘 |
| 建/判分固定评估集 | `scripts/build_eval_set.py` / `scripts/score_eval_set.py`、`shared.eval_set` | 见 §3.3；产物只在 `notes/_meta/eval/` |
| 扫段落悬空指代 | `scripts/scan_dangling_refs.py`、`shared.dangling_ref` | 见 §3.4；报告项，不拦不改 |
| 调提取 prompt（长源清单） | `scripts/loop0_extract_coverage.py` | 见 §3.5；零成文成本，机械粗筛＋模型裁可疑项 |
| 测生成端改动（A4/A6） | `scripts/build_round.py` + `scripts/score_eval_set.py` | 见 §3.6；重新生成 → 同清单复核 → 判分 |
| 给条件必备结构定阈值 | `scripts/audit_conditional_signals.py` | 见 §3.7；硬拦前先看误报率 |

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
| `articles/html_export.py` | 笔记 → 自包含分享版 HTML（callout / chart 块 / 目录，机械转换零 AI） | `render_note_html`、`export_note_html`、`self_contained_violations` |
| `videos/fetch.py` | 字幕抓取（B站 / YouTube / 412 风控 / cookie 轮换） | `fetch_*_transcript`、`rotate_bili_cookie_*` |
| `videos/asr.py` | 无字幕转写（下载音频 + Whisper/FunASR 双后端 + 语言感知路由 `auto`，长音频自动分片） | `transcribe_*`、`extract_audio`、`detect_language`、`auto_route_backend`、`unload_whisper_model` |
| `videos/asr_funasr.py` | FunASR 中文特化后端（独立 venv 常驻 worker，零污染 whisper 进程） | `transcribe_audio_funasr`、`close_funasr_worker` |
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
`scripts/pipeline_gate.py`（管线门禁真源：未登记 CLI + 新增完全重复 → 拦截；由
`tests/test_pipeline_gate.py` 断言强制生效，见 §12）。

**落盘辅助（单篇场景）**
`scripts/persist_summary.py --obsidian`（接单持久化，含去重+门禁+标签）、
`articles/_save_summary.py`（外层对话保存总结的专用入口）、
`scripts/_save_one_summary.py`（单条 JSON → `save_summary_only`）、
`scripts/land_migrate_entry.py`（migrate 队列落盘）、
`scripts/land_scys_batch.py`（scys 飞书双写路径，需 `DISABLE_FEISHU_SYNC=0`）、
`scripts/export_note_html.py`（已有笔记 → 自包含分享版 HTML，见 §2.1 路径 A）。

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
`fix_feishu_titles.py`、`probe_feishu_titles.py`、`rename_list.py`、`promote_existing.py`、
`find_duplicates.py`、`delete_duplicates.py`、`scan_feishu_tree.py`、`list_overviews.py`、
`backfill_overviews.py`、`rebuild_overviews.py`、`series_maintenance.py`、`audit_sync_watchdog.py`。

**B站补齐相关**
`scripts/reset_up_backfill.py`（重置补齐状态）、`scripts/reconcile_series_bvid.py`（bvid 对账，只读）、
`scripts/fetch_transcript_only.py`（只抓字幕不总结）、`videos/cdp_capture.py`（CDP 抓 YouTube 字幕）、
`videos/yt_bridge.py`（YouTube 字幕桥接）、`videos/build_bookmarklet_html.py`（小书签安装页生成）。

**ASR 运维（2026-10-06 补登记，FunASR 接入时漏）**
`scripts/setup_funasr.py`（装/修 FunASR 独立 venv：Paraformer-zh + fsmn-vad + ct-punc + 版本红线）、
`scripts/asr_backend_compare.py`（同段音频 whisper vs funasr 对照报告，诊断路由质量用）。

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

- 改任何入口的**命令形态**（改名/换参数）→ 同步更新本文 + `AGENTS.md` 对应任务路由。
- 新增脚本：有长期价值的**不要用 `_` 前缀**（`.gitignore:50` 忽略 `scripts/_*`，会漏入库）。
- 发现本文没覆盖的场景 → **先补本文再动手**，不要自己新开一条并行实现。
- 新写任何可复用函数前 → 先扫「§10 能力索引」；只有在确认没有、或现有实现需要大改时才新写，
  并把新函数补进 §10。
- 定期自检覆盖度：`python scripts/audit_pipeline_coverage.py`（列出所有 CLI 入口里本文没登记的）。
  它有遗漏不等于文档正确——命中只说明「被提到过」，描述准不准仍要人看。
- **管线门禁（2026-09-18）**：新建「未登记到本文的 CLI 入口」或「基线 `ALLOWED_DUP` 之外的完全
  重复函数」会被拦截。这意味着：**新增任何 CLI / 复用任何现成函数，都要先在本文（登记入口）
  或 `scripts/pipeline_gate.py`（基线）落地，否则 `tests/test_pipeline_gate.py` 跑红。**
  门禁逻辑单点维护在 `scripts/pipeline_gate.py`（`check()` 返回问题清单），由
  `tests/test_pipeline_gate.py` 断言强制生效，配合纪律「改动后必跑全量测试」。
  **纯项目内机制，不依赖 git hook / 任何 git 配置，换机克隆零接线。**
