# 决策索引

本目录记录技术选择和理由；原始决策保留当时的证据、限制与取代关系。命名与新决策篇幅要求见 [RULES](../../RULES.md) §6.2，历史长文不据此机械删减。

## 当前行为从这里追溯

| 主题 | 决策记录 | 当前实现 / 规则 |
|---|---|---|
| 默认本地 Obsidian | [Obsidian 默认](DECISION-20260904-obsidian-default.md) | RULES §3.0，`articles/manager.py` |
| 内容完整与门禁 | [content-first](DECISION-20260915-content-first-gate.md) | RULES §4.6；后续内容拦截以 `prompts/verifier.py`、`articles/main.py` 为准 |
| 源忠实与条件结构 | [模板忠实契约](DECISION-20261014-template-fidelity.md) | RULES §4.6；`SOURCE_FIDELITY_RULES` 与 `SECTION_CONTRACTS`；字数兜底与失控保护保留 |
| 类型适配与版本来源 | [类型与追踪](DECISION-20261015-type-provenance.md) | `prompts/classify.py`、`prompts/provenance.py`；legacy只报差异，PIPELINES §4 |
| 明确主张的证据门禁 | [笔记证据](DECISION-20261015-note-evidence.md) | `prompts/evidence.py`与[证据契约](../../references/note-evidence-contract.md)；确定缺陷不受force豁免 |
| URL 去重与系列简化 | [系列去流程化](DECISION-20260908-series-deflow.md) | `articles/dedup.py` 与 PIPELINES §5 |
| 迁移成功替换后清旧文 | [迁移门禁 v3](DECISION-20260908-regen-gate-v3.md) | PIPELINES §8 |
| CDP 健康复用 | [健康复用](DECISION-20260910-cdp-health-reuse.md) | [CDP 手册](../../references/login-required-cdp-workflow.md)；实例内核在用户级技能 |
| 微信读书替代旧代理 | [直连源](DECISION-20260919-weread-direct-source.md) | [weread 手册](../../references/weread-direct-source.md)；后续登录恢复与配额契约在手册持续更新 |
| 主题标签与目录字段 | [语义标签](DECISION-20260913-namespace-semantic-tags.md)、[脚本命名与 folder](DECISION-20260920-script-naming-and-folder-field.md) | `shared/note_classify.py`、`shared/routing.py` |
| 重做的受控替换 | [受控替换](DECISION-20261018-controlled-replace.md) | `shared/controlled_replace.py`、`scripts/resum_save_batch.py`；PIPELINES §3.1 |
| 批次5 评估收尾 | [批次5 收尾](DECISION-20261018-batch5-closeout.md) | 模板质量计划状态行与 §10.6；质量模块已解冻 |
| 评估集标注方式 | [AI 标注](DECISION-20261018-ai-annotation.md) | A1 表单（AI 标注基线）；`scripts/score_eval_set.py`、评估目录 `ANNOTATION.md` |
| 输出覆盖范围（散落信息点） | [覆盖口径](DECISION-20261018-info-coverage-scope.md) | SOURCE_FIDELITY / COVERAGE 的收紧方向；A4 提取清单与 A6 模板落点 |
| A4 长源提取清单落点 | [A4 落点](DECISION-20261018-a4-agent-mode-landing.md) | `prompts/extract_checklist.py` + `get_note_prompt` 条件注入；清单内化（选 a）、持久化契约、报告项出口；Loop 0/1 见知识层计划 §8 |
| A5 论点级判据 / A6 条件必备结构 | [A5/A6 落点](DECISION-20261018-a5-a6-landing.md) | `prompts/claim_coverage.py`（报告项，**机械覆盖率禁当指标**）、`SECTION_CONTRACTS[*].conditional` + `content_signals` 五信号（硬拦，1298 篇实测误报 1 例）；模板侧散落信息点/推广口径 |

## 历史关系与阅读方式

- [2026-09-11 重试放行](DECISION-20260911-gate-retry-bypass.md) 已废止；不等同于当前显式 force 修订，现行边界看 RULES §4.6。
- 旧默认飞书、旧系列状态机等已归档记录位于 `_archive/decisions/`；不要据旧记录覆盖当前规则。
- 已完成阶段和快照状态见 [文档导航](../README.md)；决策确认、代码实现、验证通过与发布是不同状态。

变更既有决定时在原记录标取代关系，新记录写真实理由和已知确认来源；未知日期或决策者不补造。当前命令始终回到 [PIPELINES](../PIPELINES.md)，不要从历史方案直接执行。

批次4全文审核与失败准入：[决策](DECISION-20261015-fulltext-review.md)，实现边界见[全文审核说明](../../references/fulltext-review.md)。

批次4候选收尾、独立复审及数字噪声范围：[收尾决策](DECISION-20261015-batch4-closeout.md)。当前验收状态以开发计划§8.4为准。

批次5首轮三组对照、候选留出与真实成本边界：[评估决策](DECISION-20261016-batch5-evaluation.md)，进度见开发计划§8.5；该评估的剩余项（严格留出/人工标注/费用）已按 [收尾决策](DECISION-20261018-batch5-closeout.md) 于 2026-10-18 关闭。

A4/A6 正式对照轮（隔离手段、结果与三条口径边界）：[决策](DECISION-20261018-a4-a6-formal-rounds.md)，结果见 [PLAN-20261018 知识层](../plans/PLAN-20261018-knowledge-layer.md) §8。
