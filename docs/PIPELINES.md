# 管线入口总表（唯一真源 · 2026-09-17 建立）

**当前开发任务**：用户已确认一轮生成端有限修正与 P4 定向复验，交新会话执行；范围、三调用预算与停止条件以[意图树计划 §9](plans/PLAN-20261030-intent-tree-summary.md#9-已确认一轮生成端有限修正与复验f3r新会话执行)为准。保留切块与提炼 v6，不引入向量检索，不启用多 Agent 分段写作或生产开关，不扩批。下方历史试点“过验证”不等于本轮已完成。

**流程简化（2026-10-28 立项，2026-10-29/30 F1–F5c 完成：裸 pack_only 判不启用，组合流程过验证待用户裁决启用）**：普通源单轮；复杂长源按收益保留选材。已接线：提炼契约 v5 + pack schema v3（模型只报 `source_spans`，素材正文由程序从同版源逐字提取）；`prepare_entry` 改用 `assemble_pack_only_prompt`（包块 + 未选区间附录；provenance 记 `distill_input_mode=pack_only`；旧输入模式块判 `input_mode_changed` 重装）；`--ingest --prepare` 组合只装配本批回收成功条目。**F5b/F5c 两源真实对照结论**：裸「成稿只吃包」会丢内容（D1 整章丢失）判不启用；组合流程「**po 写作 → 机械覆盖自查（零模型，高召回候选器）→ 定向补稿（先核对缺才补）**」两源通过 §5.7 保留条件——D1 产物 P1=0/失真=0、审计排名三稿第一（≳ ex > cur），成本在登记预算 ex×1.10 内（对单轮仍约 2.2× 输入）。**`NOTE_DISTILL_DISPATCH` 保持 `0`**；用户追加方向（2026-10-30）：试点 4–10k 段组合流程（计划 §4 F7），若证实中短段丢失严重则向全长度启用（可关闭，数字密集两类熔断暂保留）——试点未出结论前仍只在显式评估中使用。两阶段入口仍为 `python scripts/distill_prepare.py --emit --out <DIR>` → 子 Agent 选材 → `--ingest --dir <DIR> --prepare`（默认关闭，仅显式使用）；`--plan` 是可选只读预览。证据与遗留：`notes/_meta/eval/20261029-pack-only-wiring/VERDICT.md`（§六/§七）、[总结流程简化计划](plans/PLAN-20261028-summary-flow-simplification.md)（§4/§8–§10），旧长源计划与低成本计划均为历史。

**原文素材前轮记录（N6，2026-10-28，已实测、未启用）**：提炼契约 v5 / pack schema v3——模型只交原文位置（`source_spans`）、简短主题、必要关联与疑点，素材正文由程序从同版源逐字提取，成稿时按需整理。两源实测（S3 字幕 / D1 长文）：逐字保真通过，有限复核显示成稿失真减少；成本复核发现原表漏算提炼输入，统一含空白字符后总输入相对单轮约 +200%～+236%。实际 token/耗时与可用预算尚待验收，保持不启用。证据见[旧长源计划 §4 N6](plans/PLAN-20261028-long-source-summary.md) 与本机 `notes/_meta/eval/20261028-n6/VERDICT.md`；理由见[原文素材决策](decisions/DECISION-20261028-source-excerpts.md)。该轮写作仍重读全文，不能作为新完整包消费方式的验收；下文 N1–N5 同为前轮记录。

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
| **落盘后给笔记做独立语义复审**（不阻塞落盘、不占前台） | `python scripts/run_async_review.py --plan` → `--emit --batch <id>` → 子 Agent 审 → `--ingest --batch <id>` → `--verdict` 入账（见 §3.2 / §3.2.1） | 打开 `NOTE_QUALITY_GATE=1` 把审核卡在落盘前 |
| 查「段落悬空指代」到底是不是问题 | `python scripts/scan_dangling_refs.py`（只读，见 §3.4） | 直接建改写轮／凭感觉改 prompt |
| **查分析层数字是否可溯源、表格数字是否挂错讲者** | `python scripts/audit_note_grounding.py --dir <round-dir>`（只读，见 §3.9） | 把它当落盘门禁（它只报告，不拦盘） |

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

**提取清单内化（A4，2026-10-18，入口不变）**：`FORCE_AGENT_MODE=1` 下无外部 provider，提取只能由执行模型在**同一次派工**里做，故清单指令按源长条件拼进条目 prompt（`prompts/templates.py:get_note_prompt` → `prompts/extract_checklist.render_extraction_rules`）：源长 >10000 字要求「先列清单、再成文」（清单是自查表，不得当大纲）；<2000 字只给 T0「源撑不起」事前报告钩。落盘前由 `prompts.extract_checklist.process` 把清单块**剥离出正文**、逐条机械校验「原句能在源里命中」（命中不了丢弃）、再对成文做机械对账（数字/专名/专名类中文名），未命中并入异步审核理由。**全部是报告项**：不计分、不拦盘、不触发重交；清单按源 hash 存档于 `notes/_meta/extract_checklists/`。**2k–10k 档已试过薄清单并否决**（S2，5 篇配对：保留率 96.6% vs 98.3%、失真 4 vs 2、成稿 −13.9%；该档一次能写完，加清单只诱导压缩），开关 `NOTE_THIN_CHECKLIST` 默认 `0`，证据 `notes/_meta/eval/20261026-thin-checklist/REPORT.md`。决策与 Loop 0 结果见 [A4 落点决策](decisions/DECISION-20261018-a4-agent-mode-landing.md) 与[知识层计划](plans/PLAN-20261018-knowledge-layer.md) §6 A4。**视频侧接线现状（2026-10-18 实测，非文档推断）**：视频侧**全部总结路径都含 A4**，与文章侧一致——单篇视频、系列课单集、监控 ASR 批量三条入口最终都汇到 `videos/main.py:_finalize_single`，其降级分支用 `prompts/provenance.dispatch_fields` 生成条目 prompt；该函数自 2026-10-15 起即传 `source_chars_of`（原为篇幅目标），本批 A4 落地后**顺带生效**；落盘走 `articles/_save_summary.py` → `save_summary_only`，剥离同样生效。仅两处例外：① 外部 provider 直调路径（`videos/main.py:_summarize_segments` → `get_note_prompt(note_type)`，仅 `FORCE_AGENT_MODE=0` 时走，默认关）不注入；② 系列维护脚本 `scripts/series_maintenance.py` → `_save_series_note`（接收已写好的正文，不经 `_finalize_single`）不注入，也不触发落盘后异步审核。⚠️ **历史误述更正**：原「videos 单集/系列路径尚未接线（其 prompt 未传源长）」系据 `videos/main.py:_build_subagent_prompt` 与外部 provider 分支写成，而该函数**自 2026-07-21 起无任何调用点（死代码）**，与实测不符，已按实测更正。干净 A/B（12 视频源）测得视频侧 A4 保留率增量未检出，**决定保留现状**；依据见 [A4 视频侧保留决策](decisions/DECISION-20261018-a4-video-side.md)。

**质量优化前轮记录（N1–N5，2026-10-28）**：v4 论证转述方案已实测并决定不启用，N5 机械门槛 9/9 通过；原始证据及边界保留在[旧长源计划](plans/PLAN-20261028-long-source-summary.md) §4。原“5 对 4”属于模型审计计数，其中两项有争议，不能当严格优劣证据；字符增长只表示登记口径下的近似输入规模。用户随后确认 N6 原文素材方向，其原则继续有效；后续任务以本文开头的流程简化计划为准，下列 N1–N3/S5–S9 段是前轮实现历史。

**输出契约统一（S5，2026-10-27 实施）**：生产 prompt **关闭额外延伸**——思维模型透镜与「笔记者分析」区、延伸思考留白、自测三问、费曼「讲出来」、类比迁移、无源行动项、认知误区与「我的立场」退出九类模板；**源内已有的分析、反驳、类比、方法、练习和迁移内容照常保留并归属作者**，不按栏目关键词整段删除（PLAN-20261026 §9.7，入口与命令不变）。九类「固定结构（顺序不可颠倒）」改为「模块与组织（按源准入；顺序可受约束重组）」，开篇系统契约（作者行/禁H1/标签行/主题词区块）不动；`SECTION_CONTRACTS` 同步去掉已关闭小节（structured/general 去「延伸思考」，opinion 去「认知误区/笔记者分析」）；`QUALITY_GATE_PROMPT` 以「无源延伸」维度取代「思维模型落地」，`QUALITY_GATE_SELFCHECK` 与 `prompts/review_rubric.py` D/E 维度同步。`SECTION_ALIAS` 与 `shared/note_grounding.py` 分析层正则保留（旧库笔记兼容）；S4 vizpurpose profile 的替换目标句未动。测试 `tests/test_s5_output_contract.py`（40 条）+ 更新后的模板/学习包/门禁自检回归。

**材料层索引（S6 首轮，2026-10-27，抓取行为不变）**：`shared/source_material.py` 对**已落盘源文本**构建同版结构索引——标题/表格块/代码块/段落范围按 1-based 行号可回查，绑定与 provenance 同口径 `source_sha256`（`verify()` 对账，失配不当有效交接），时间位置与图片状态**如实声明**（absent / 引用 `image_inventory` 同款 url_key，不伪造；文本通道限制固定声明 `html_table_rows_flattened` + `no_visual_evidence`）。落盘 `notes/_meta/source_materials/<url_key>.json`，与图片台账互查。接入点两处（转写成功即写，失败不阻塞主流程）：`videos/asr.py` ASR 成功分支、`videos/main.py:_summarize_and_save` CC/实时路径（有 segments 才写）；transcript 缓存保持纯文本不动。**存量与量化**：存量 transcript 无时间戳 → 索引如实 absent；2370 个 `_raw_` 文章源 **0 个**含管道表格——HTML 表格行列在文章通道系统性打平（`_extract_bs4_content` get_text），已登记缺口未改抓取。测试 `tests/test_source_material.py` 18 条。见 [质量改进计划](plans/PLAN-20261026-low-cost-quality.md) §9.3–§9.6。

**独立提炼候选（S7，2026-10-27，未接生产派单）**：`prompts/distill.py`（提炼契约：主线地图/信息条目/关系说明三类产物、原话与摘意区分、自包含任务 + JSON 输出，`DISTILL_MARKER` 隔离断言保证不拼接进九类写作模板）+ `shared/distill_pack.py`（pack 装壳/机械校验/落盘）。校验是**报告项**：同版 hash 对账（provenance 同口径，失配即 ok=False）、行号 sanity（越界/空行）、关系引用不悬空、枚举合法、`stance=quote` 原话机检（去空白口径同 extract_checklist）——**字段齐全 ≠ 语义完整**，语义核对属 S8 独立复核。落盘 `notes/_meta/distill_packs/<url_key>.json`（与材料索引/图片台账同款 key；同版重写幂等，失败可续跑；`store_dir` 可指定评估目录隔离试验）。真实验证：scys B 页源（19.7k 字）提炼 36 条目/15 关系/5 quote 一次通过校验，行号抽查 3/3 正确，pack 落 `notes/_meta/eval/20261027-distill/packs/`（本机证据，换机须重验）。测试 `tests/test_distill_pack.py` 16 条。

**提炼配对评价（S8 首轮，2026-10-27，仍未接生产）**：5 对冻结源（长文章多案例/数字密集/多对象地图/长视频 ASR/短源负样本，`notes/_meta/eval/20261027-distill-round/MANIFEST.json`，本机证据）两臂对照——cur=生产单轮；distill=提炼子 Agent → pack 校验 → 写作（同 prompt 主体逐字节 + 材料包块）。盲评（每臂一人互不见）：distill P1 遗漏 0 vs cur 6、确定性失真 0 vs cur 3、取材 15/15 vs cur 13+2 无法回答 → **达保留门槛，适用范围=长源（约 ≥10k）复杂论证/多案例**；短源不适用（无增益、pack 相对成本高）；数值密集表格源为 distill 弱项（P2 丢失增多）。S9 接线见下一条。顺手修 `distill_pack.load_pack`/`source_material._path` 伪协议（`eval://`）读不回 bug + 2 条回归测试（36 passed）。裁决与实录见计划 §9.11-S8 与评估目录 VERDICT.md。

**长源两阶段派发（S9 有限接线，2026-10-27，默认不改生产行为）**：`shared/distill_dispatch.py` + `scripts/distill_prepare.py`。**路由从严**——只对 `source_chars_of` ≥ 10000、数字密度 < 0.08、**且源内不同数字串 < 130** 的源走「提炼 → 按 pack 写作」；短源（<5000）、10000 以下、密度密集（`numeric_dense`）与低密度高数字量（`numeric_heavy`，长直播复盘型）一律回单轮（S8 已证短源无增益、数字密集源 P2 丢失增多；S9 抽样补录同一弱项的另一张脸——美债源密度仅 0.0373 却有 191 个不同数字串，且千字归一化在这个区间分不开），判不准都落 `single`。数字串口径下沉 `shared/source_metrics.py`（与 `scripts/measure_fact_density.source_numbers` 同一真源，后者改为引用）。**装配不改主体**——`assemble_write_prompt` = 生产 prompt 主体逐字节 + 材料包块（标记/文案与 S8 distill 臂逐字节同款，实测 24278 字节比对一致），提炼契约 `DISTILL_MARKER` 不进写作 prompt。**版本绑定**——源 hash / 提炼 prompt hash 失配或 `validate_pack` 不过 → `load_valid_pack_for_source` 返回 None，该篇降级单轮、不阻塞批次、不修补旧 pack。**四步链**：`python scripts/distill_prepare.py --plan`（只读路由报告，不写文件；>40k 的源会显示 `chunks` 块数）→ `--emit --out DIR`（单块 `DIR/<url_key>_distill.txt`；超长源每块 `DIR/<url_key>_c<k>of<N>_distill.txt`，任务只含该块片段但行号是 `NNN| ` 全局行号）→ 子 Agent 提炼（每块一份回包）→ `--ingest --dir DIR`（**缺任一块或任一块校验不过 → 整篇不落盘**；多块由 `distill_pack.merge_chunk_packs` 合并：id 加块前缀、关系重映射、重叠区去重、跨块关系不臆造并写进 notes）→ `--prepare`（写队列条目 prompt + `generation_context.distill_pack_key`/`distill_pack_source_sha256`，`prompt_sha256` 同步为装配后 hash，`validate_dispatch` 仍须通过；留痕 `notes/_meta/distill_dispatch/*.jsonl`，`--dry-run` 不写）。**幂等判据是两把锁**：`block_is_current` 同时比对 `source_sha256` 与 `distill_prompt_sha256`——只比源 hash 会让契约升级后队列里的旧包块永远停留（2026-10-27 实抓）。**开关**：默认不启用——`monitors/run.py` 不调用本入口；`scripts/filter_pending.py` 仅在 `NOTE_DISTILL_DISPATCH=1` 时对刷新后的条目装配（顺序必须 refresh → prepare，反了包块会被重建的主体抹掉）。测试 `tests/test_distill_dispatch.py` 31 条 + `test_filter_pending_refresh.py` 11 条 + 分块 `tests/test_distill_chunking.py` 19 条 + 缺陷回归 `tests/test_s9_defect_fixes.py` 19 条。**历史证据**：两轮同组 n=2 隔离抽样、v2 pack 数字修正、规模更正与验证限制见[旧计划](plans/PLAN-20261026-low-cost-quality.md) §2/§9.11-S9 第 10–11 条；**后续由[新计划](plans/PLAN-20261028-long-source-summary.md) N1–N5 承接，已于 2026-10-28 收口（N4 判不保留，见下段）**。B 原 210,427 字符含新旧双包，单包重装为 141,543；A 为 106,229。**回退也会改写条目**（§8.5 第 1 条，2026-10-27 已修）：`prepare_entry` 先判路由源可读性再拆包——源不可读 / 路由回 `single` / 无有效 pack 时把残留包块剥掉并清 `distill_*`（`PrepareIneligible.stripped=True` 表示条目已变，队列须写回；`filter_pending` 计 `stripped` 且纳入写回条件，不能直接依赖 `refresh_prompt` 先行重建来掩盖）；`--plan` 先行给出 `pending_strip`，`--prepare` 报告分列 `stale_stripped`/`fallback_stripped`。重复装配归一为**恰好一个**包块（循环剥离 + 日志 `blocks_after` 单包断言）。`--emit` 的 `url_key` 未定义 NameError（分块重构引入，自提交 `a84138e` 起派单跑不通）已修。提炼契约的 quote/paraphrase 口径（「带模型注解的 content 不得标 quote，只能 paraphrase」、示例 i4 改 paraphrase、`example_issues()` 自检）**在提交历史里没有单独的 v3**——`a84138e` 提交的是 v2，该口径来自当时会话内未提交的 v3；N1 直接落地为 **v4**（见下一条），所以 v3 不可复现，旧 pack 一律按版本门失效、提炼需重跑。**后续任务已收口（2026-10-28）**：输入成本、新分块稳定性、失败定位、独立语义复验与有限启用由新计划 N1–N5 承接并已完成结论——N4 实测判「不保留」（两源无可定位改善、端到端输入 +165%/+196%），N5 机械门槛 9/9 实测通过；旧固定字数档实验不再作为执行清单，`NOTE_DISTILL_DISPATCH` 保持默认 `0`。

**长源总结 N1：粒度与交接协议（2026-10-28，离线实现，默认仍关）**：契约 **v4**（`prompts/distill.py`，`DISTILL_MARKER` 同步）+ pack **schema v2**（`shared/distill_pack.py`）。① **段级论证单元**：条目首选 `type=argument`，一个完整观点/论证连同依据、案例、步骤、条件、例外、数字归属写成一条；**不再「数字逐行成条」**，也不为每个数字单列。② **数字同段不混口径**：单元内多读数走 `readings` 数组，逐项给 `value` + 对象/单位/期间/基准/确定性；两个读数没有任何区分字段即「未声明口径」，`validate_pack` 拦下（`prompts.distill.readings_issues` 为单一真源，示例自检与真实回包同口径）。③ **必要跨段关系**：段内关系写进单元正文，只有跨单元的依赖/限制/冲突才进 `relations`；分块任务把跨块才看得清的指代/条件/相反读数写进 `deferred`，**局部任务不得替全篇下结论**。④ **实际处理范围**：`coverage.processed_ranges` 记 1-based 行区间（来自派单块边界，不采信模型自报）；`complete=True` 必须真覆盖全文，有洞即报「空洞」；`complete=False` 如实声明部分读取。⑤ **版本绑定**：pack 与包块 meta 同时记 `contract_version`/`schema_version`；`load_valid_pack_for_source` 对旧契约返回 `contract_version_mismatch`，`block_is_current` 判 `contract_upgraded`，`--emit` 任务书与 manifest 记契约版本、`--ingest` 拒收旧契约回包。⑥ **成本可观测**：`measure_assembly` 增报 `schema_version`/`contract_version`/`arguments`/`readings`/`deferred`/`source_lines_total`/`processed_ranges`/`chunk_count`；token 与耗时取不到仍写 `unknown`。行式序列化同步支持 `@q`（读数）与 `@f`（deferred），`lines_roundtrip_issues` 逐条比对——压缩不得丢口径或跨段交接。测试 `tests/test_distill_contract_v4.py` 31 条；契约升级令旧 pack 与旧任务书全部失效，需重跑提炼（版本绑定的预期行为）。

**长源总结 N2：结构感知切分（2026-10-28，离线实现）**：`shared/source_material.scan_structure` 成为材料索引与切块的**单一结构真源**（标题/表格/代码块/段落，`build_index` 改为引用）。`plan_chunks` 从「每 40k 字切一刀 + 回退 40 行」改为：先按标题、段落、表格、代码块边界切**结构单元**（表格/代码块内部不许下刀），再按容量贪心装块；无结构文本（字幕/口播，换行不算语义边界）按**句子结束符**切，无标点才按容量硬切。每块给出两套行区间：`start_line–end_line` 是**下发范围**（含前一块尾部 `context_lines` 行重叠，字数为 `delivered_chars`），`core_start_line–core_end_line` 是**核心区间**（本块负责提炼，字数为 `chars`）；核心区间首尾相接、恰好覆盖 1..总行数，重叠只出现在下发范围。超过容量的单元切分时带 `continued`/`continues`/`continuation`（表格/代码/长段的后半），派单文案明确「接续/尚未结束/不要重复提炼」；`chunk_scope_issues` 在 `--ingest` 拒收 `source_lines` 越出核心区间的回包（重叠上下文不得重复建条目）。`merge_chunk_packs` 的 `processed_ranges` 改用核心区间（不再把重叠计入「读过的行」）。全程纯机械，**不增加 LLM 分段调用**。边界用例：标题边界、表格整块、代码整块、超长代码块续接、字幕句群、无标点兜底、超长单行、末块；测试 `tests/test_distill_structural_chunking.py` 23 条。全篇统稿与装配消费见下一条 N3。

**长源总结 N3：全块回收门与全篇统稿（2026-10-28，离线实现，默认仍关）**：写作侧消费契约与回收门槛，不新增模型调用。① **全块完整回收才形成可统稿材料**（`shared/distill_dispatch.py`）：`--ingest` 先按每块自报的 `total` 校验清单块序必须 `1..total`（`--limit` 截断 manifest 时回包数照样凑得上，但源没处理完），合并后再用 `distill_pack.coverage_issues` 校验处理范围真覆盖全文，任一不满足整篇不落盘；消费侧 `load_valid_pack_for_source` 对 `complete≠True` 或块序不全返回 `incomplete_coverage`，`--prepare` 据此剥掉旧包块回单轮（`filter_pending` 单列 `incomplete` 计数）。② **装配块自带覆盖声明**：`pack_meta_json.coverage_complete` 与行式 `@meta complete=`，`block_is_current` 增 `coverage_incomplete`——只比源/契约 hash 时，不完整 pack 装配出的块会永远停在队列里被当成「已就绪」。③ **全篇统稿契约**（新模块 `prompts/synthesis.py`）：多块或有 `deferred` 的包在块头附「不做二次压缩 / 跨块对账（deferred 逐条 + 块边界 + 指代条件冲突）/ 块级主线不等于全篇结论」，并下发**回源核对**信息（源文件路径、总行数、`source_lines` 为 1-based 行号）；源路径缺失时明说「回源不可用，缺口如实保留」，与 `PACK_BLOCK_HEADER` 的单块简版并存。④ **装配契约版本** `HANDOFF_MARKER`：N3 之前装配的块判 `handoff_upgraded`，只重装 prompt 不重跑提炼（与「提炼契约升级要重跑 pack」区分开）。⑤ **合并完整性**：跨块去重键改为（type, stance, content, readings）——只看 content 会把「同一句、不同读数口径」的另一块条目静默丢掉；`coverage` 记 `chunk_indices`/`chunk_total_declared`。⑥ **接线**：`--plan` 增 `pack_ok`/`pack_chunks`；`prepare` 的 provenance 增 `distill_chunks`/`distill_source_path`/`distill_source_lines`/`distill_synthesis`，剥包时一并清掉。测试 `tests/test_distill_synthesis.py` 17 条。**边界**：统稿是否真的接住跨块指代、条件与冲突属语义问题，由 N4 独立复核，机械门只保证「缺块不进统稿、deferred 与块边界一定送到写作端」。`NOTE_DISTILL_DISPATCH` 仍默认 `0`。

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

**⚠️ 不要手搓队列条目**：落盘会用条目里的 `url` / `author` 写信息块。手工造条目漏 `url` → 链接变「【无来源链接】」，author 猜错 → 覆盖正确作者；而 T8 只比**内容**缺陷，看不出元数据退化（2026-10-26 实测把一份更差的稿判成 improved，已回滚）。`build_resum_batches` 现有元数据闸：旧稿有真实链接、条目没 `url` → 与 `match=suspect` 同级单列待人工核对。正规做法只用 `build_resummarize_queue` 产出的队列。

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
[DECISION-20261018-controlled-replace](decisions/DECISION-20261018-controlled-replace.md)。

### 3.2 落盘后异步审核（T7 ②，2026-10-18）

**问题**：全套独立语义审核（`prompts/semantic_review.review_fulltext`：分块全文、独立会话审者、
`coverage=full` 才认）**早就建好了，但只能卡在落盘前**，开关 `NOTE_QUALITY_GATE` 默认 `0`
→ 不开就永远不审，开了就拖慢生产。

**改法**：落盘（照常）→ 入队（一行 JSONL，零 AI 成本）→ 有空/有人时消费。

| 步 | 入口 | 说明 |
|---|---|---|
| 入队 | 自动（`save_summarized_article` 两处本地落盘点） | 开关 `NOTE_ASYNC_REVIEW`：`0` 关 / `1` 触发式（默认：`review_flags` 非空**或**按 `NOTE_ASYNC_REVIEW_RATE`=0.15 抽样）/ `all` 全量 |
| 看队列 | `python scripts/run_async_review.py --stats` | 状态分布（`--batch <id>` 附带该批进度） |
| 选单 | `python scripts/run_async_review.py --plan --rounds 5` | 有界批次：风险 3 + 随机 1 + 预留重审 1（**只读队列，不清积压**） |
| 派单 | `python scripts/run_async_review.py --emit --batch <id>` | 按选单出包到 `_tmp/async_review/in/<id>.md` + `HANDOFF-<id>.md`（口径 = `QUALITY_GATE_PROMPT` 原文） |
| 回收 | `python scripts/run_async_review.py --ingest --batch <id>` | 读 `_tmp/async_review/out/<id>.json`，按 `parse_quality_gate` 收口 → 批次报告 |
| 重解析 | `python scripts/run_async_review.py --ingest --retry-errors` | 回包已在但判 error 的**只重解析**，不产生新审稿轮次 |
| 裁定 | `python scripts/run_async_review.py --verdict true_error / false_positive / unconfirmed --batch <id> --id <rec>` | 写既有 `notes/_meta/grounding/ledger.jsonl`，不另起质量分 |
| 重出报告 | `python scripts/run_async_review.py --report-only --batch <id>` | 补完裁定后刷新报告，不动队列 |
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

### 3.2.1 有界批次与最小统计（S1，2026-10-26）

**为什么**：3.2 的 `--emit --limit 5` 只是「按文件顺序取前 5 条」——而队列里 48/119 条是同源的
`author_unverified` 系统性噪音，会把这 5 个名额吃满，真正带具体行号的疑点永远轮不到；
同时「发了包」和「审过了」在账面上分不开（D4），也说不清真错/误报（D5）。

**执行链**：`--plan` → `--emit --batch` → 子 Agent（独立会话）→ `--ingest --batch` → 报告 → `--verdict` 入账。

| 机制 | 口径 |
|---|---|
| 预算 | 默认 `--rounds 5` = 风险 3 + 随机 1 + **预留重审 1**；`--random-slots` 不足时如实记 `shortfall` |
| 选单排序 | 非系统性噪音优先 → 带「第 N 行」具体定位优先 → 旧账（`ts` 早）先审 → id 稳定兜底 |
| 去重 | 同一稿只派一次（`duplicate_note:<id>`）；同类风险 `--per-tag-cap 2`（`tag_cap:<tag>`） |
| 未选中 | 每条都写原因：`over_budget` / `tag_cap` / `duplicate_note` / `note_missing` / `source_missing` / `valid_conclusion_exists` |
| 不重复花钱 | `shared.async_review.VALID_CONCLUSIONS`＝`passed`/`failed`：同稿同 hash 已有有效结论就不再派单；`update()` 默认**不把有效结论回退**成 error/stale/skipped（`force=True` 才允许） |
| 回包不合格 | 状态 `error` 而非通过；原因码由 `_classify_reject` 给出：`coverage_partial` / `status_partial` / `reviewer_self_check` / `invalid_json` / `empty_output` / `bad_score` …（**判定仍由 `parse_quality_gate` 做，原因码只记账**） |
| 未回收 | 保持 `pending`，可续跑；**未审 ≠ 通过** |
| 统计 | 处理量（选中/发包/有效回收/未回收/error/stale/skipped/未选中/全库 pending）、问题（机械疑点/独立 findings/真错/误报/未确认）、修订、消耗（轮次/预估调用/token=unknown/耗时/人工分钟） |
| 误报率分母 | **只算已裁定 = 真错 + 误报**；未确认不进分母，也不算真错；无样本时写「尚无已裁定」而非 0% |

产物（都在 gitignore 内）：批次账本 `notes/_meta/async_review_batches/<batch_id>.json`
（含选中/未选中理由、预算、发包、回收、统计）、报告 `notes/_reports/async_review_<batch_id>.md`。

**边界**：发包 ≠ 已审；报告生成成功 ≠ 质量通过；风险优先样本**不能估算全库错误率**。
选单/派单/回收只动队列与 `_tmp`，**不写 vault**（笔记正文与 frontmatter 一个字段都不加）。

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

`--prompt-profile` 可用臂：`current`（生产态）/ `a4only`（回退 A6 模板散文）/ `baseline`（剥短源块，配
`--subset short`）/ `noblocks`（＋禁止并列铺开，配 `--subset short`）/ `vizpurpose`（＋按表达用途选图表
条款，PLAN-20261026 §6 的图表 A/B 臂；**2026-10-27 首轮 5 对验证未达保留门槛，未接生产**，profile 留档
见该计划 §6.7）。护栏测试 `tests/test_build_round_profile.py`：每对 profile 改动必须能在生产模板里定位到、
试验臂与生产态逐字节 diff 只差目标块，定位不到即报错不静默。

**测生成端改动（A4/A6）不能用「对已填表单重跑判分」**——那量不出生成差异。正确流程是同一批源
**重新生成**、用**同一份清单**在新目录重新勾选、再判分，并记录笔记变长幅度。本工具把这条流程固化，
且**绝不碰原评估目录与 vault**（新笔记写 `<round-dir>/notes/`）。防覆盖判据按「**已勾选**」算复核劳动
（轮次表单的条目是复制来的，不能用 `touched_forms` 判）。判分前先看 `measure` 报的**复核完成度**：
一条都没勾的表单要么笔记为空、要么**还没复核**，后者会把 0% 当真实回归。

两个关键开关（2026-10-18 正式轮新增）：

- **`normalize` 必跑**：轮次稿带 A4 清单块，不剥则复核者照抄清单 → 保留率虚高、对照失效。
  原稿留 `.raw.md`；**复核子 Agent 只能读 `notes/Axx.md`，不得读 `.raw.md`**（派工提示词里要写明）。
- **`--prompt-profile`**（`apply_prompt_profile`）：一圈只改一个变量，定位不到目标段即报错、不静默通过。
  | 值 | 含义 | 用途 |
  |---|---|---|
  | `current` | 生产态，原样 | 测生产态 / 新块本身（如 `round_layer_admit`） |
  | `a4only` | 按 A4 落地态（commit `4b26a3d`）回退 A6 两段模板条款 | 隔离 A4 主效应（§8 归因纪律） |
  | `baseline` | 剥掉整个「短源篇幅取向」块（块头起至末尾） | 短源 A/B 的**对照臂**，只配 `--subset short` |
  | `noblocks` | 生产态 + 「禁止并列铺开」条款（渲染前置 `NOTE_SHORT_GRANULARITY=1`） | §4.7 禁铺实验；**已否决**，仅留档复跑 |
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

### 3.8 短源篇幅取向（去分析层与元层，2026-10-19）

实现：`prompts/section_budget.py`（阈值常量、黑名单、块渲染与 `--check` 自报）。

```bash
python -m prompts.section_budget --check                      # 阈值 / 各类型关停清单 / 边界自检
python scripts/build_round.py build --subset short            # 验收台（0-2k + 2-5k 共 12 篇）
python scripts/build_round.py build --subset short --prompt-profile baseline   # A/B 对照臂
python scripts/build_round.py build --subset short --prompt-profile noblocks   # +禁止并列铺开（§4.7 已跑）
```

**机制**：源长 ≤ `section_budget.SHORT_SOURCE_MAX_CHARS`（5000）时，`get_note_prompt` 追加
「短源篇幅取向」块，**关停分析层与元层**（延伸思考 / 自测三问 / 讲出来 / 笔记者分析 / 小结闭环，
只列该笔记类型模板里**确实存在**的小节），只保留「重组 / 条件与前提 / 有信号反例 / 作者私房术语」；
**并用「凭据表」约束内容小节的层**（背景 / 因果 / 证据 / 结论 / 原则-启示 各要一个能在源里指认的凭据，
没有就不立层；必留四类优先于「少写一层」——2026-10-19 定案，见下）；
**长源一行不动**（实测长源新增 90% 是正当的源内容展开）。**不改 `SECTION_CONTRACTS`、不改门禁签名**
（`分层速览` 是 `required`，短源按裁决**保留**它，故门禁无需感知源长）。

**为什么不是「加一句写精简些」**：A4 正式轮已实测提示词修订**压不住变长**（压得住超长句）——
本机制靠**关停具体小节**与**层的准入凭据**，都有清单、可机械自报（`--check`）。

**判据（2026-10-19 修订）**：保留率不降 / **2-5k** 稿长÷源长 ≤1.5 / **0-2k** 稿长绝对值 ≤2,800 字 /
事实密度（源内可溯数值÷千字）不低于对照臂。原「两桶都用 ≤1.5」是从长源数据推的，而 0-2k 有结构下限
（required 小节 + 必留四类），按比例量会逼着砍内容——实测代价见计划 §4.7。口径与全部数字见
[短源瘦身计划](plans/PLAN-20261019-short-source-trim.md) §1、§4.9。

> ⚠️ **这些是「验收尺」，不是门禁**：在对照轮跑完后由人量，**不喂生成端、不写进 prompt、不做落盘拦截**。
> 门禁侧仍只认 RULES §3 的 `<300` 与 `>max(8000, 源长×3)` 两档——把上面任何一条做成生成端上限
> 就等于复活「字数上限压制」（RULES 明令禁止，见 `DECISION-20260915-content-first-gate`）。

**验收结果（2026-10-19 实跑，12 篇 A/B，按修订前的旧线判）**：对照臂 94.3% / 2.36·1.60 / 1.51·6.97，
生产臂 **96.4% / 1.89·1.13 / 1.54·8.71**（保留率 / 稿长比 0-2k·2-5k / 密度 同序，密度用
`measure_fact_density.py` 量）——保留率与 2-5k 长度线达标；0-2k 长度线（1.89 对 1.5）与两桶密度线
按**旧线**未达标，其中 0-2k 的线已在下面定案里撤销。机制生效可机械核验（生产臂关停小节 0 篇违规、
对照臂同类小节 8,721 字），但杠杆只覆盖「分析/元层」，**0-2k 的膨胀主体在 required 内容小节内部的铺开粒度**。

**定案（同日，`PLAN-20261019` §4.8–§4.9）**：**不禁分层**，改「按凭据准入」——短源块里给了
「层 → 必须先能在源里指认」的对照表（背景/因果/证据/结论/原则-启示），并写明
**必留四类（数字 / 条件 / 反例 / 原话）优先于「少写一层」**。准入臂实测：层数 53→38、
0-2k 密度 1.54→**2.65**、整体保留 **97.2%**、失真 0；0-2k 稿长比 1.80。
**判据随之修订**：0-2k 撤销「稿长÷源长 ≤1.5」，改 **绝对值 ≤2,800 字**（下游每篇取 2 段 ≈3.2k 为据）；
2-5k 仍看 ≤1.5（现 1.13）。禁铺条款 `NOTE_SHORT_GRANULARITY` **默认关**、仅留档。

**接线覆盖**（实测调用点）：`articles/main.py`、`prompts/provenance.py`（条目 prompt 出口）、
`scripts/backfill_series.py`、`build_refetch_resum_batches.py`、`consume_migrate_queue.py` 均传源长 → 生效；
例外两处与 A4 相同——`videos/main.py` 的外部 provider 直调分支（`FORCE_AGENT_MODE=0`，默认关）
与死代码函数 `_build_subagent_prompt`。

### 3.9 接地审计：源 payload 清洗 + 两类只读检查（2026-10-19）

留出轮 5 篇暴露出三类「机械检查看不到」的问题（证据与复跑见
`notes/_meta/eval/20261019-holdout-5/ROOT_CAUSE_REGRESSIONS.md`）：

1. **源 payload 抬高锚点分母**（P0，已修）：scys 源里嵌入 Coze JSON，图片尺寸 / logid / note_id
   被当成内容锚点，一篇 15/15 的稿被 58% 召回误拦。`prompts/content_signals.py:strip_source_payload`
   在构造锚点前剥掉「带 payload 字段的行」与超长单行 JSON，**不盲删所有 JSON**（教程里的配置示例是正文）。
   2026-10-25 补第二类：页面 chrome「评论区输入框占位符 `0 / 1200 发布`」被拆行抓进正文，
   纯数字 1200 进锚点分母，一批 4 篇同被误拦——现已剥「数字/数字/发布」紧邻序列（回归测试
   `test_comment_box_chrome_stripped_but_real_number_kept`）。
   2026-10-19 全量 1298 篇对比：仅 5 个源受影响、锚点总数 9150→9136（−0.15%）、**1 篇新通过、0 篇新失败**。
2. **分析层数字不可溯源**（P1，只读）：`笔记者分析 / 延伸思考 / 自测 / 讲出来` 里的中文数字（「三年」）
   不进锚点集合；`shared.note_grounding.analysis_number_flags` 扫这些小节，源里找不到且未标估算/推断即报。
3. **表格数字挂错讲者**（P1，只读）：多人源的「结果与数据」表把 A 的量化结果填进 B 行；
   证据契约明说不识别多说话者归属。`table_attribution_flags` 只对「首列在源里有独立 `名字：` 小节」的行
   做 4-gram 归属检查，≥3 个讲者段落命中的视为通用词跳过。

入口（报告项，不拦盘、不改稿）：

```bash
python scripts/audit_note_grounding.py --dir notes/_meta/eval/<round-dir> \
  --report <round-dir>/_grounding_audit.md --json <round-dir>/_grounding_audit.json
python scripts/audit_note_grounding.py --note <稿.md> --source <源.md>   # 单篇
python scripts/audit_note_grounding.py --manifest <list.json>            # 生产批次：[{"id","note","source"}]
python scripts/audit_note_grounding.py --recent-hours 6                  # 跑完监控后扫最近落盘的库内笔记
```

**源解析四级收口（2026-10-25 修盲区）**：`--recent-hours` 曾对两类条目**静默漏扫**（配不上源就跳过、
输出空表假绿）——① B站长标题：raw 文件名 key 截 30 字符（`articles/main.py:save_raw_content_to_file`），
全标题匹配不上；② scys 源：`notes/_scraped/scys/` 不在 `_raw_` 索引里。现由
`audit_gate_signals.resolve_source` 统一解析：slices bvid 映射 → scys topicId 确定性回查
（source_url 含 `xq_topic/<id>` → `<id>.md`）→ raw 全名 → raw 截断前缀 `key[:RAW_KEY_MAXLEN]` →
difflib 0.9 兜底。回归 `tests/test_audit_source_resolution.py`；修复后语料覆盖 1316→1678（+362）。

**P2 复核清单**（零生产风险）：`build_round.py` 的 `_CHECK_INSTRUCTION.md` 与
`QUALITY_GATE_PROMPT` 各加两条观察项——**机制链完整性**（资金/流程/因果是否有一句完整文字链路，
不只端点、不只图）与**数字归属**（表格数字是否挂在源里对应讲者名下，挂错按 P1）。
`QUALITY_GATE_PROMPT` 变更会改变 prompt hash，旧的显式审核报告需重出（默认 `NOTE_QUALITY_GATE=0` 不受影响）。

> 边界：P1 两项都是**报告项**，误报由人工判；不要把它们直接改成落盘硬拦。P0 只改锚点构造的输入清洗，
> 不改阈值（`MIN_ANCHORS` / `RECALL_THRESHOLD` 未动）。

**下一批真实生产的有限验证协议**（2026-10-19 定）：对本批稿件跑 P1 + 消费异步复审队列 →
只对确认问题做定向修订 → 复审 → 在 `notes/_meta/grounding/ledger.jsonl` 记「真错/误报/修订次数/人工分钟」，
再按成本决定是否自动接线或加 prompt 补丁。完整步骤、账本字段与门槛见
[`notes/_meta/eval/20261019-holdout-5/NEXT_PRODUCTION_PROTOCOL.md`](../notes/_meta/eval/20261019-holdout-5/NEXT_PRODUCTION_PROTOCOL.md)。


### 3.10 图片缺口台账（D2，2026-10-26）

**问题**：抓取只抽文本，`<img>` 全丢，连「源里有几张图」都没记录 —— XMind / 鱼骨图 / 数据截图
里的知识静默消失，稿子却毫无破绽。抽样 4000 篇源文件，图片链接命中 **0**：不是"识别不了"，是"不知道有没有"。

**做法**（`shared/image_inventory.py`，报告项）：

```bash
# 抓取时自动记账（无需手动跑）；查台账 / 回填识图结果用代码 API：
python -c "from shared import image_inventory as I; print(I.summary_line(I.load_for_url('<url>')))"
```

| 状态 | 判据 |
|---|---|
| `unknown_no_html` | 取不到 HTML（CDP/字幕通道）—— **不许报「无图」** |
| `no_images` | HTML 解析完确实 0 张 `img` |
| `decorative_only` | 全是图标/logo/小图（启发式，仍计入 `total`） |
| `images_not_read` / `partial_read` / `read` | 有内容图，按 `reads` 算读了几张 |

**四条硬约束**（都有测试）：
1. `alt` 有字**不算**已读；`images[].read` 由 `reads` 反推（单一真源）。
2. 图链接缺失算缺口，不算干净。
3. 台账只落 `notes/_meta/image_inventory/<url 前16位>.json`，**不写笔记正文/frontmatter**。
4. 不下载、不 OCR、不批量请求；识图由调用方按需做，结果用 `mark_read()` 回填（方法/范围/失败原因）。

**接线**：抓取五条通道记账（普通 HTTP / 微信直连有 HTML；微信 CDP / scys / 飞书 记 `unknown_no_html`）；
`articles/main.py:summarize_content` 只在**有缺口**时往 prompt 追加一行提示（纯文本源字节不变）；
`_enqueue_async_review` 放 `extra`。🔴 **图片状态不许进 `reasons`**——`reasons` 非空会触发入队，等于把 15% 抽样放大成 100%。

**现状边界**：~~主源 scys 走 CDP 文本通道，仍然数不出图~~ → **2026-10-27 起已接 DOM 快照**（下述）。"重要图"靠人挑，不做批量识图。
小样本实测见 `notes/_meta/eval/20261026-image-gaps/REPORT.md`（2 张流程图恢复结构、1 项误读被独立核对抓出）；
2026-10-27 真实主源 3 页小样本（A/B/D，35 图，重要图 2/5）见 `notes/_meta/eval/20261027-real-source-images/candidates/VERIFICATION.md`。

**CDP DOM 快照记账（2026-10-27，`shared/cdp_session.page_html`）**：scys / 飞书 CDP 抓取时另取一次渲染后 DOM 快照
（`page_html(page)`，只抽 `<img>` 元数据用于数图，**不落盘、不参与正文抽取**），`articles/fetch.py` 的
`_feishu_cdp_fetch` / `_scys_cdp_fetch` 与 `scripts/scys_batch_fetch.py` 的对应路径用 `_record_images(url, html=..., kind="scys_cdp_html")` 记账。
CDP 通道从 `unknown_no_html` 变为可数图（拿到 DOM 时按 `images_not_read`/`partial_read`/`read` 记；DOM 取不到时仍兜底 `unknown_no_html`，不冒充"无图"）。
原 `fetch_web_content` 中两处 `kind="..._text_only"` 记账已注释（避免与 CDP fetch 内重复记）。测试：`tests/test_image_inventory.py`（16 条）。

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

**材料包消费模式（流程简化 F2–F4，2026-10-29 接线）**：`generation_context.distill_input_mode == "pack_only"`
的条目，prompt 自带完整材料包（已选原文素材 + 未选区间附录 + 版本绑定与回源路径）——
**派工时不再附原文全文、不指示子 Agent 整篇读 `raw_file`**；子 Agent 只消费条目 prompt，
需要核对某行上下文时按 `source_lines`/行段局部读源并在稿内登记回查范围。
条目 prompt 无包块（`distill_input_mode` 缺失）的条目维持现役单轮派工（prompt + 原文）。
落盘侧不变：`save_pending.py` 仍按 `raw_file` 磁盘原文做 `source_sha256` 对账，
公共门禁的比对依据是完整同版源，不以包替换；保存成功才登记/清队列，失败保留候选按原上限修订。

**写作端两环（流程简化 F7 接线，2026-10-30）**：pack_only 条目写稿后、`save_pending.py` 落盘前
走组合流程收尾，全部由 `scripts/check_note_coverage.py` 承载（零模型，阈值冻结于
`check_calibration.json`，label 3-gram < 0.2）：
1. **机械覆盖自查**：`python scripts/check_note_coverage.py --queue <q> --key <键> --file <稿>`——
   exit 0 无可疑直接落盘；exit 1 有可疑块走第 2 步；exit 4 源/包失效（按派工回退处理，不硬闯）。
2. **定向补稿**：`--emit-patch <任务文件>` 生成补稿材料（成稿全文 + 可疑条目素材正文，**不含源全文**），
   派补稿子 Agent，**三出口协议**必守（意图树 v6，2026-10-31）：①改写也算含——成稿以其他表述
   覆盖即勾销不补；②**核实为可舍弃**——确属重复表述/元信息/过程叙述/寒暄/与主线无关的岔题
   （或 support 且主线放得下），勾销不补，稿尾记一行 `舍弃：条目 <id> —— 一句理由`；
   ③缺才补——主干（trunk）内容确认缺失才**纯插入**定点插段，原稿其余逐字不动；
   补后**仅对新增覆盖的条目**刷新稿内 EXTRACT_CHECKLIST（出口②勾销的不登记）。
   v6 起机械自查仅拦截 trunk 条目（`shared/note_coverage.py` tier 过滤），support/drop
   缺落点进 info 报告观察不拦。
3. **只插不改核验**：`--file <补稿稿> --verify-insert-only <原稿>`——exit 0 通过才可落盘；
   exit 5 说明补稿引入了 replace/delete（F7 停止条件：判定该篇补稿失败，保留原稿走人工定位，
   不静默采纳改动稿）。
非 pack_only 条目该脚本报 `not_pack_only` 正常跳过。**F7 试点已过验证（2026-10-30，po 6/6 全胜单轮，全链输入按当时口径 1.31–1.39×）**，全长度启用待用户裁决。**意图树契约 v6 批 A（2026-10-31）**：自查升 trunk-only、补稿升三出口（见上），P1 v6 全胜（压缩率 1.16→0.99、零失真）、P4 触发停止点①（3 处失真全为写作回退）；**成本口径复核发现 F7 的 1.31–1.39× 分母偏大（装配后 prompt+源当单轮臂），按登记口径复算 F7 亦 >3×——启用成本账须重算**；生成端有限修正与 P4 定向复验已确认（计划 §9，待新会话执行）；六源扩批及生产开关仍暂缓。裁决前 `NOTE_DISTILL_DISPATCH` 仍 0，生产不制造 pack_only 条目，本节命令在开关打开或显式试点时生效。

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
**零件**：`scripts/scys_batch_fetch.py: ScysBatchFetcher.fetch_article`、`shared/cdp_session.py`、`shared/feishu_ext.py`（**飞书懒加载全文单点实现（2026-10-25）**：`collect_full_text` 按容器优先级增量滚动逐视口收集，优先级 `[.bear-web-x-container`（wiki/docx）, `.rc-virtual-list-holder`（**妙记 minutes 转写列表**，2026-10-26 补）]；缺后一档会退化成滚 window，而妙记根本不滚 window → 只抓到首屏（实测 1h27m 会议仅 1093 字，补后 19623 字）。
- 🔴 **走 CDP 的前提是 URL 判定命中**：`is_feishu_doc_url`（唯一判定，现含 `wiki|docx|doc|minutes|s` 且要求 token）不命中时链接会掉进普通 HTTP 下载，而飞书是 SPA，HTTP 只拿到空壳 HTML → 顶层报「抓取过短」，**易被误判成死源**（2026-10-26 补 minutes 同理）。
- `scys_batch_fetch._fetch_external` 已接入（修复旧 window.scrollTo 只抓目录+开头的截断 bug，一批 32 条实测）；scys 侧外链识别按 `EXTERNAL_DOC_HOSTS`（host 级），`/minutes/` 自动跟进。
- `articles/fetch.py` 单篇飞书链接分流同源。
- 存量重抓 `scripts/feishu_ext_refetch.py` + `scripts/refetch_feishu_ext_batch.py`（飞书外链全文重抓 / 批量版；⚠️ URL 取 `<a href>` 或 `_ext` 文件头完整版，正文显示文本带 `...` 会 404）。
- ⚠️ **该脚本成功门槛只有 ≥300 字** —— 抓一半也会打绿勾（妙记 bug 因此藏了一年）。已加行数哨兵 `THIN_LINES=30`（healthy 文件最少 69 行、中位 182 行，而 bug 态仅 4 行），低于则在结尾汇总告警；**不阻断生产，靠它把同类问题暴露出来**。
- 🔴 真死源（飞书返回「页面不存在」且 URL 与原帖 `<a href>` 一致、三变体均 404）**直接放弃，不要反复重试**；区分方法与判据见 [决策记录](../docs/decisions/) 与 `memory/2026-10-26.md`。

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
| `shared.image_inventory` | D2 图片缺口台账：抓取时记 `有图/已读/取不到图片信息`，派单加一行提示，审核元数据带 `image_state`；**报告项、不下载不识图、永不抛异常** | `articles/fetch.py`（记账）、`articles/main.py`（派单提示 + `_enqueue_async_review` extra） |
| `prompts.extract_checklist` | A4 长源提取清单：按源长条件注入两阶段指令（>10000）／短源 T0 报告块（<2000）；剥离清单→机械校验（原句须命中源）→对账→按源 hash 存档，**全部报告项、永不抛异常** | `prompts.templates.get_note_prompt`（注入）、`save_summary_only`、`resum_save_batch` |
| `prompts.claim_coverage` | A5 论点级判据（**报告项**）：源侧断言候选＋疑似无落点＋失真嫌疑（源外数字/方向反转）；机械覆盖率**禁当指标** | `scripts/run_async_review.py --emit`（注入派单） |
| `prompts.content_signals.CONDITIONAL_SIGNALS` | A6 条件必备结构的源侧信号（反例/公式/章节/对比/数值密集） | `content_signals.structure_obligations` → 机械门禁硬拦 |
| `prompts/section_budget.py`（模块 `prompts.section_budget`） | 短源篇幅取向：源长 ≤5000 注入「关停分析层与元层」块 + **层的准入凭据表**（背景/因果/证据/结论/原则-启示 各要源内凭据；必留四类优先），**长源一行不动**。`NOTE_SHORT_GRANULARITY=1` 另开「禁止并列铺开」条款（**已否决**，默认 0，仅留档） | `prompts/templates.get_note_prompt`（注入）；A/B 用 `build_round.py --subset short --prompt-profile baseline|noblocks` |
| `scripts/measure_fact_density.py` | 事实密度量尺（对照轮判据 3）：正文数字串÷千字，去 `#` 标题行、源侧另去 `> 标题/时间` 元信息；`--against` 支持 A/B 两臂对打 | 人工验收（`PLAN-20261019` §4）；与 `build_round.py measure`（变长）配套 |
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
| 飞书外链全文（懒加载） | `shared/feishu_ext.py: collect_full_text`（单点实现，2026-10-25 自 feishu_ext_refetch 下沉） | 按 `[.bear-web-x-container`（wiki/docx）, `.rc-virtual-list-holder`（**妙记 minutes 转写列表**，2026-10-26 补）] 优先级滚容器；消费方：scys 批量外链 / feishu_ext_refetch 存量重抓 / articles 单篇飞书分流 |
| 飞书正文型链接判定（→ 是否走 CDP） | `shared.feishu_ext.is_feishu_doc_url` | **唯一判定，必须与 CDP 分流同源**：`wiki\|docx\|doc\|minutes\|s` 且要求 token。不命中 → 掉进普通 HTTP 下载 → 飞书 SPA 只返回空壳 → 被误报成「源死了」（2026-10-26 补 minutes） |
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
