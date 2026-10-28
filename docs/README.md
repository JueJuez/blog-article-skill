# 文档导航与维护

状态：现行导航。2026-10-11 按仓库代码核对主要入口与落盘契约；历史实跑结果保留原日期，本次未运行真实抓取。

## 从任务找到真源

| 信息职责 | 维护位置 |
|---|---|
| 项目定位、安装、最短用法 | [根 README](../README.md) |
| Agent 开工顺序与稳定执行约束 | [AGENTS](../AGENTS.md)；强制规则真源 [RULES](../RULES.md) |
| 命令入口、零件、数据流与常见误用 | [PIPELINES](PIPELINES.md) |
| 订阅与队列运营 | [monitors/README](../monitors/README.md) |
| 环境变量说明 / 示例 | [config](../references/config.md) / `.env.example`；实际默认值核对代码 |
| 浏览器会话与专项抓取 | [CDP](../references/login-required-cdp-workflow.md)、[YouTube](../references/youtube-cdp-workflow.md)、[scys](../references/scys-fetch-sop.md)、[weread](../references/weread-direct-source.md) |
| 转写依赖与恢复 | [ASR 手册](../references/asr-bilibili-sandbox.md) |
| 模板与质量契约 | `prompts/templates.py`、`prompts/verifier.py`、`prompts/content_signals.py`；执行规则 RULES §4.6 |
| 路由、去重、最终输出 | `shared/routing.py`、`articles/dedup.py`、`articles/manager.py`；管线入口见 PIPELINES |
| 接地审计（分析层数字 / 表格归属） | `shared/note_grounding.py`、`scripts/audit_note_grounding.py`；入口与边界见 PIPELINES §3.9，理由见 [接地审计决策](decisions/DECISION-20261019-grounding-audits.md) |
| 测试纪律与术语 | [testing_rules](../references/testing_rules.md)、[glossary](../references/glossary.md) |
| 决策、理由与取代关系 | [决策索引](decisions/README.md) |
| 已知缺陷与优化方向（增量生产 + 存量重做） | [KNOWN_DEFECTS](KNOWN_DEFECTS.md)；本轮任务与验收见下方长源总结计划 |
| 外部仓库调研参考（open-notebook / open-webui，未立项） | [external-repo-benchmarks](../references/external-repo-benchmarks.md)；决策见 [DECISION-20261024](decisions/DECISION-20261024-external-repo-benchmarks.md) |
| 项目抽取归档 | [项目抽取技能](../tools/project_import/SKILL.md) 与 [用法](../tools/project_import/README.md) |

## 当前实现边界

主链路为「入口 → 抓正文/字幕 → 去重、选模板 → 会话模型总结 → 门禁 → 路由、落盘、登记」。`FORCE_AGENT_MODE=1` 下，脚本不会自行消费全部待总结队列。监控和 scys 队列使用 `save_pending.py` 保存，由编排方统一清队列；存量重做使用 `resum_save_batch.py` 覆盖已知 `note_path`，两者不能混用。

`articles/` 承接文章与最终输出，`videos/` 承接字幕、ASR 和视频编排，`monitors/` 承接订阅发现与运行状态，`prompts/` 承接内容契约，`shared/` 承接公共路由、清洗与会话适配，`scripts/` 提供维护入口。CDP 实例机制依赖用户级 `cdp-automation-profile`，项目薄适配层为 `shared/cdp_session.py`；换机须核对该依赖的安装情况。

## 计划与交接的适用状态

以下是文档证据的定位，不是运行现场进度表。继续旧任务前核对分支、未提交修改、实际队列、登记表和产物，不能仅凭历史计数续跑。

| 文档 | 状态与用途 |
|---|---|
| [长源总结效率与质量计划](plans/PLAN-20261028-long-source-summary.md) | **当前任务与验收唯一真源**。N1（契约 v4 / pack schema v2）与 N2（结构感知切分）已离线实现并通过离线测试，**未跑模型、未做真实执行与独立语义验收**；从 §4 的 N3（全篇统稿与装配消费）接手。§3 逐项裁定旧计划遗留，§6 保留独立收尾与条件任务；`NOTE_DISTILL_DISPATCH` 仍默认关闭 |
| [低成本质量改进开发计划](plans/PLAN-20261026-low-cost-quality.md) | **2026-10-28 已退役，未全部验收**。完整保留 S1–S9 的历史实现、有限证据与失败修正；后续只按新计划执行，不再按旧 §8.5 续跑 |
| [模板质量提升计划](plans/PLAN-20261014-template-quality.md) | §8.4为批次4收尾；§8.5~§8.10为批次5七组评估（17组诊断样本全部纳入同条件评估§8.9；完整讲书形态源§8.10）；**2026-10-18 按有限验证收尾**（§8.11：留出/人工/费用三项不再执行、缺口登记，见 DECISION-20261018-batch5-closeout），质量模块已解冻；§9/§10 系历史快照，勿据其续跑 |
| [知识层计划](plans/PLAN-20261018-knowledge-layer.md) | 方向已定为「服务下游 agent」，按 §6 的 A/B 分组执行。**A 组（自身受益）7 项已全部验收完成（2026-10-18）**：A1 基线 84.2%（AI 标注 30 篇）、A2（T13+T8 受控替换）、A3（T7×2 稿件不丢+异步审核）、A7 悬空扫描（0.92%，不立项）；**A4 正式轮 84.2%→99.4%**（代价变长 +173% 未压下；⚠️ **A4 单步净效应经补跑的干净 A/B 未检出**，该数应读作「当前整套提示词 + 重新生成 vs 历史旧稿」——见 [A4 视频侧决策](decisions/DECISION-20261018-a4-video-side.md)）、A5 报告项（机械覆盖率**禁当指标**）、A6 正式轮（保留率无可测增量、结构硬拦误报 0）。**B 组（下游受益）已于 2026-10-19 全部结项（无一保留）** —— B1 已由下游自行解决、B2/B3 收益不足、B4/B5 下游无需求、B6 前提被证伪（库内无跨源整合层）且成本高，逐条结论见计划 §6「B 组结项说明」。进度真源＝§6 表 + §8 三层循环结果；工具入口见 [PIPELINES §3.1–§3.7](PIPELINES.md) |
| [短源瘦身计划](plans/PLAN-20261019-short-source-trim.md) | **已结案（2026-10-19）**：只改短源（≤5k）——关停分析层与元层，**不对内容小节「禁铺」**，改用**按凭据准入分层**（背景/因果/证据/结论/原则-启示 各要一个源内凭据，没有就不立层；必留四类优先）。三轮实测（各 12 篇配对）：现状 96.4%/1.89·1.13、禁铺 93.6%/**1.49**·0.96（丢 2 条必留 → 否决）、**准入 97.2%**/1.80·1.13（层 53→38、0-2k 密度 1.54→**2.65**、失真 0）。**判据随之修订**：0-2k 撤销 1.5 比例线，改绝对值 ≤2,800 字。禁铺条款 `NOTE_SHORT_GRANULARITY` 默认关、仅留档 |
| [并行监控计划](plans/PLAN-20260828-parallel-monitor.md) | 实现完成；2026-09-02 实跑证据见 [过程档案](parallel-monitor-runbook.md)。旧三源、飞书和 redrive 描述属当时方案 |
| [迁移收尾计划](plans/PLAN-20260907-migration-finish.md) | 2026-09-07 快照；使用已冻结的迁移工具，不能直接当当前任务执行 |
| [统一去重与系列计划](plans/PLAN-20260908-unified-dedup-series.md) | 架构实施记录；阶段 5 数量是 2026-09-09 快照，当前队列进度须现场核对 |
| [CDP 统一计划](plans/PLAN-20260911-cdp-unified.md) | §13 记录 S1–S6 完成，保留当时沙箱限制；现行操作看 CDP 手册 |
| [weread 源计划](plans/PLAN-20260919-weread-source-module.md) | 初始方案已落地并持续演进；当前登录恢复、配额和过码以 weread 手册及代码为准 |
| [阶段 C 交接](HANDOFF-阶段C-重总结.md) | 2026-09-17 完成快照；复用重做流程从 PIPELINES §3 开始，勿把历史批次当待办 |

`notes/_meta/resum_batches/DISPATCH_PROMPT.md` 与 `PROGRESS.md` 是本机批次材料，未入库；当前本机存在，换机不保证存在。缺失时先生成、核对批次与派单契约，不用历史累计数猜进度。

## 维护与验证

沿用既有目录，不为格式建立空需求、状态或日志文件。维护责任随内容归属：改行为者更新对应真源；AI 可在授权范围内整理文档，不能把未确认方案标成已采纳。

- 改命令或管线：更新 PIPELINES；README / AGENTS 仅同步必要示例和导航。
- 改规则：更新 RULES；专项实现细节落对应 references / 模块文档，其他位置留指针。
- 改方向：需求和验收写回关联计划，理由写决策；保留旧决定和取代关系。未知来源或原因明确写未知。
- 完成阶段：计划写实际产物和验证证据；完成、验证和发布分别记录。交接是快照，长期结论回写真源。
- 旧方案：有历史价值则保留并在开头标范围与现行入口；必要时归档，正文先保存再留桩，同批更新引用。
- 交付检查：本地链接、锚点、脚本和参数存在；再核对说明与代码一致。格式检查不代替真实抓取或独立接手验证。

旧代理证据保留在 [PROXY_NOTES](../monitors/PROXY_NOTES.md)，外部研究索引在 [wechat-mp-sources](../references/wechat-mp-sources.md)。其中可用性、版本与外部链接仅代表原记录时点，本次未联网复核；不得据此启用旧代理。

本次整理后可从 README → AGENTS / PIPELINES → 专项手册定位目标、入口和限制。后续修改按上述职责回写真源；本次没有新增业务待办，也未把历史计划判定为当前运行进度。

## 本次整理验证（2026-10-11）

- 63 份入口、docs、references、monitors 与项目抽取文档中，90 个本地 Markdown 链接及相应锚点检查通过；外部链接未联网核验。
- `audit_pipeline_coverage.py`：98 个 CLI 入口，未登记 0 个；`git diff --check` 通过。
- `test_pipeline_gate`、`test_inplace_resum_pipeline`、`test_overwrite_semantics`、`test_note_mechanical_gate`、`test_weread_login_recovery`：85 passed。
- 全量测试未完成，已中止。单独运行 `tests/test_cdp_session_reuse.py::TestCloseD6Contract -q -x` 复现 `AttributeError: CdpSession has no attribute _use_daemon`：该测试的 `_reused_session` 使用 `__new__` 构造旧字段集，而当前本机用户级内核的构造函数初始化 `_use_daemon`，close 读取该字段。属于测试替身与外部技能契约不一致，本次未修改代码或技能依赖，不能据此认定生产 close 失败。该关闭行为用例使用模拟浏览器对象，不启动 Chrome、不打开网页、不抓取内容；后续可补齐测试替身字段，再验证关闭行为及全量测试，无需用户扫码或提供链接。
- 未做真实抓取、写入成品库或独立新会话接手验证；导航与源码核对不能替代这些验证。
