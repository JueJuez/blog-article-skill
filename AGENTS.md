# blog-article-skill — Agent 工作入口

将文章、视频、字幕和订阅内容转成结构化笔记，默认归档到本地 Obsidian；开源项目抽取单独入项目库。

## 开工读取顺序

1. [docs/PIPELINES.md](docs/PIPELINES.md)：管线入口唯一真源，先确定任务入口。
2. [RULES.md](RULES.md)：强制规则、输出与质量契约，按任务读取相关小节。
3. [docs/README.md](docs/README.md)：文档职责、模块边界、计划与历史导航。
4. 再读所选管线的专项说明，不默认加载全部历史日志。

## 任务路由

| 用户意图 | 完整流程 |
|---|---|
| 总结文章链接、原文、视频或字幕 | PIPELINES §2；文章 `articles/run.py`，视频 `videos/run.py` |
| 跑一次 / 跑监控 | PIPELINES §4；默认 B站 + scys，公众号单独 `--mode weread`。跑完若要验证接地：按 §3.9 跑 `audit_note_grounding.py --recent-hours 6` + 消费异步复审队列 |
| 关注 / 订阅账号 | [监控说明](monitors/README.md)；B站走 `--subscribe` 查重，不手改名单 |
| 公众号补全 / 补到指定日期 | PIPELINES §4；`--weread-backfill`，旧代理 `--backfill` 已停用 |
| 补齐 UP 主历史视频 | PIPELINES §6；抓取须经 `scripts/run_with_env.py` 包装 |
| 补齐 scys / 生财有术 | PIPELINES §7 + [scys SOP](references/scys-fetch-sop.md) §9；长任务走 launcher |
| 补齐系列课 | PIPELINES §5；单集随普通队列，整季走 `backfill_series.py` |
| 重做旧笔记 / 补丢失的源 | PIPELINES §3；重做用 `resum_save_batch.py` 原路径覆盖；重做前先 `check_resum_necessity.py` 判该不该改（§3.1） |
| 分享 / HTML 版 | PIPELINES §2.1；按需生成，无此要求只落笔记 |
| 落盘后复审 / 建评估集 / 查悬空指代 / 跑对照轮 / 查接地 | PIPELINES §3.2–§3.9；异步审核 `run_async_review.py --plan → --emit --batch → 子 Agent → --ingest --batch`（§3.2.1），评估集 `build_eval_set.py`，提取调参环 `loop0_extract_coverage.py`，对照轮 `build_round.py`（**normalize 必跑**，见 §3.6），接地审计 `audit_note_grounding.py`（§3.9） |
| 评估 / 归档开源项目，从文章或视频提取项目 | [项目抽取技能](tools/project_import/SKILL.md)；不触发原文总结 |
| 迁移体检 / 清异常笔记 | PIPELINES §8；先只读扫描，成功替换并核验后才清旧文 |

## 执行必守

- 默认只写本地 Obsidian；飞书仅明确启用双写或镜像时使用。配置与兜底见 RULES §3.0，不手动写 `notes/` 充当成品。
- 复用入口，抓取、路由、门禁、登记由代码负责。不要手搓平台私有接口、临时抓取脚本或直接调生产落盘函数绕过门禁。
- `FORCE_AGENT_MODE=1` 下消费入口返回的 prompt 与原文；批量总结派子 Agent，主会话负责编排与核验。
- 监控结束在本次会话闭环 monitors / scys 队列：`filter_pending.py` 刷新 prompt 并清已总结条目 → 子 Agent 按条目 prompt 写稿 → `save_pending.py` 落盘 → 编排方统一再清队列。字段与命令见 PIPELINES §4。
- 当前默认采用「提炼 → 写作 → 既有机械/内容门禁 → 原入口落盘」，不再逐篇派模型核对、自动补稿或修补后复审。`NOTE_DISTILL_DISPATCH` 默认开启，显式 `0` 可关闭；适用范围 = 长度成本边界（≥10000 字）+ **内容类型**（`shared/source_types.py`：教程与系列课（缠论/交易体系/波浪理论等）走两阶段，观点/研报/宏观/行情复盘/公司基本面分析/框架方法论走单轮，未识别走两阶段），切块不变；旧的两道数字闸（密度 0.08、不同数字串 130）已于 2026-10-29 删除。执行以 PIPELINES 为准；决定与边界见[四步主流程决策](docs/decisions/DECISION-20261029-distill-write-default.md)与[门禁审计决策](docs/decisions/DECISION-20261029-gate-audit.md)，完整开发计划已归档。
- 先读 `filter_pending.py` 的 `distill` / `next_action`：合格源显示 `needs_distill`、`incomplete` 或 `stale` 时，先经 `distill_prepare.py --emit → 子 Agent 提炼 → --ingest --prepare`，不能直接拿未提炼 prompt 写稿；已有同版有效 pack 则复用。单篇/单视频入口（≥10000 字且类型判为可沉淀：教程/系列课或未识别）会**自动登记一次性队列**并返回 `distill_*` 交接字段：`distill_then_write` 先提炼再写作，`write` 直接消费队列里的装配 prompt；落盘用 `save_pending.py --queue <JSON路径> --key <url> --file <稿>`。细节按 PIPELINES §2。
- `generation_context.distill_input_mode == "pack_only"` 时，prompt 已含完整材料（原文素材 + 未选区间附录）；写作只消费该 prompt，默认不再附原文全文或整篇读 `raw_file`，按需按行段回源。写完直接走原保存入口；`check_note_coverage.py` 仅作可选机械报告，可疑不触发默认模型任务，不能将报告当语义通过。
- 补审工具及测试保留、勿删，后续可按用户要求沿原入口重新编排；当前不接入主流程：`run_async_review.py`（异步复审消费端）、`check_note_coverage.py --emit-patch / --verify-insert-only`（定向补稿及纯插入核验）、`NOTE_QUALITY_GATE`（落盘前第二遍 AI 把关）、`audit_note_grounding.py`（接地审计）。只在用户明确要求或专门评估时运行，不新增每篇的前置/后置步骤，除用户明确要求外不主动消费复审队列。
- 类型与版本刷新以 PIPELINES §4 为准；`filter_pending.py --dry-run` 只读预览，不清队列。auto可重算type，manual保留，legacy只报差异；版本失配不能用force绕过。
- 返回 `VERIFIER_FAILED` 或 `VERIFIER_CONTENT_FAILED` 时按 issues 修稿重交，最多两次；机械缺陷不能用 force 绕过。仍失败按 RULES §4.6 处理，不静默声称完成。
- 存量重做走原路径覆盖入口，不能用增量脚本重新推导文件名；重做前备份，见 PIPELINES §3。
- 单视频无 CC 字幕自动 ASR 兜底；UP 批量默认不做 ASR，需显式 `--with-asr`。环境限制见 [ASR 手册](references/asr-bilibili-sandbox.md)。
- 公众号恢复只本地检测 cookie 变化，恢复后消费当前账号正式第一页结果；不能绕过配额与风险冷却。见 [weread 手册](references/weread-direct-source.md) §5。
- 数小时任务用登记的 launcher 或用户自带终端；不要假定会话后台进程能跨会话存活。见 RULES §4.7。
- 不提交 `.env`、`notes/`、cookie、签名和私人库数据。
- 新增 / 改造管线先更新 PIPELINES；规则改 RULES，专项机制改 references。文档维护检查见 docs/README。

## 配置与平台入口

变量说明见 [references/config.md](references/config.md)，配置模板为 `.env.example`；当前订阅以 `monitors/subscriptions.json` 为准，scys 窗口以 `scripts/scys_projects.json` 为准。

本文件是项目维护的跨平台入口。平台是否自动加载须核对实际设置与会话；未自动加载时显式读取本文及 RULES。WorkBuddy 本地触发层位于 `.workbuddy/skills/blog-article-skill/SKILL.md`（不入库）；缺失时仍从本文及 PIPELINES 查流程。
