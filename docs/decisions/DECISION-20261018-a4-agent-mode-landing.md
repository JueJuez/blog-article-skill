# DECISION-20261018-a4-agent-mode-landing
## 背景
A4（T0+T15+T6）改生成端，但主路径是 `FORCE_AGENT_MODE=1` 下执行模型按**条目 prompt** 写稿
（`articles/main.py:101`、`prompts/provenance.dispatch_fields`），没有外部 provider 可以「1 次低温调用」；
计划 §12.3 第 11 项把它列为开工前必拍。
## 决策
- **#11 选 a · 清单内化**：源长 > 10000 字时，把「先列清单、再成文」两阶段指令拼进条目 prompt；单次派工，
  清单以机器可解析块随稿提交，代码侧机械校验每条「原句能在源里命中」，命中不了即丢弃。
  理由：路由与门禁留在代码、编排零改动、monitors/scys/articles/videos/resum 统一；b（编排方前置独立提取
  子 Agent）要改各流程编排，留作升级路径。
- **#1 机械/模型分工**：机械层＝既有 `_anchor_specs`（数字/专名，全源零成本）；模型层＝长源四分类＋论点＋
  散落信息点＋流程内推广，每条带 `‖原句` 供机械校验（没有原句就只能判「像不像」＝没有判据）。
- **#2 两阶段形态**：清单与成文同一次输出（清单在前）；prompt 明写「清单是自查表，不按它组织正文」，防
  「清单当大纲驱动编造」（`content_signals.py` 已记此坑）。
- **#12 持久化契约**：落 `notes/_meta/extract_checklists/<source_sha256[:16]>.json`，含 items（cat/text/quote/
  verified）、对账报告、`prompt_sha256`（清单块版本）、`source_sha256`/`note_sha256`；三方共用（对账读、
  A5 复用、A3 异步审核据此知道「稿是按哪版清单写的」）。
- **#13 对账失败出口**：第一阶段＝**报告项**。未命中项随落盘 meta 存档 + 进异步审核队列观察（加 review
  reason），**不触发重交**；升级与否看报告期误报率（守 §3.3 判据分级）。
- **#3 T0**：同块内的报告项信号 `[SOURCE_THIN]`，只观察不拦。
## 不做什么
- 不改任何既有门禁判据、不给字数设上下限、不动 vault 目录结构。
- 不做 b（独立提取子 Agent）、不做清单的独立模型调用。
- 本轮只在 `save_summary_only` 与 `resum_save_batch` 两条门禁路径接线（评估集 30 篇全在 articles/resum 可达范围）；
  videos 单集/系列路径的清单注入与剥离列为后续项。
