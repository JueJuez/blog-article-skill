# 外部仓库调研参考：open-notebook / open-webui（2026-10-24 快照）

状态：**留档参考，全部未立项**（决策见 [DECISION-20261024-external-repo-benchmarks](../docs/decisions/DECISION-20261024-external-repo-benchmarks.md)）。
触发条件满足时从本文出发立项，不重开调研。文中 star 数、版本、上游机制均为 2026-10-24 快照，立项前先复核上游是否已变。

## 两仓库定位（为什么是物种差异）

- **open-notebook**（lfnovo/open-notebook，~40.5k stars，TypeScript+Python，SurrealDB）：Google NotebookLM 开源替代——「对话式研究工作台」，人围着 notebook 聊天、RAG 问答、播客生成。
- **open-webui**（open-webui/open-webui，~157k stars，Python 后端 `backend/open_webui/`）：多模型自托管聊天前端，重心在会话、插件体系（Tools/Functions）、团队协作（RBAC/SSO）。
- **本项目**：无人值守批量生产管线 + 下游 agent 按段落消费。对方强在「人和库对话」，我们强在「无人值守产笔记 + 质量门禁」。

## 已覆盖、不搬（对照结论）

| 对方功能 | 本库对应物 |
|---|---|
| open-notebook transformations（可配置 prompt 产 insight） | 9 种模板 + A4 提取清单 + 门禁体系（本库强得多） |
| open-notebook URL 引擎链（Firecrawl→Jina→Crawl4AI→bs4 自动降级） | CDP 登录态 + scys/weread 特化分流 |
| open-notebook context level + token 预算显示 | 「下游每篇取 2 段 ≈3.2k」、短源 ≤2800 字判据 |
| open-webui Automations（定时跑 prompt） | `monitors/run.py` + 队列 + launcher |
| open-webui 插件体系（Filters/Pipes/Actions，官方自认任意代码执行无沙箱） | 管线注册表 + `pipeline_gate` 门禁，不引入 |
| open-webui 团队功能（RBAC/SSO/多向量库/Channels） | 单用户纯负担 |

## 留档参考 ①：播客出口（来源 open-notebook，新消费出口候选）

上游管线：**outline 模型出 N 段大纲（3-20 段）→ transcript 模型按说话人人设（1-4 人，各自 voice ID / backstory / personality）逐段写对话 → TTS 逐句合成拼接**。三种内置节目型态（tech_discussion / solo_expert / business_analysis），全程后台任务，失败带原因说明 + 手动 Retry。内容深度两档：Summary（只用 insight）/ Full content（全文）。

对本库的落法（若立项）：输入 = 现成笔记（摘要档可用 A4 提取清单/元信息，全文档用正文）；触发形态同分享版 HTML——默认不生成，用户说「出播客」才触发。成本边界：一次生成 = 大纲 + 全稿两轮 LLM 调用（输入含所选内容）+ 整篇 TTS；中文 TTS edge-tts 免费 / OpenAI TTS 按字符计费。上游文档：`docs/2-CORE-CONCEPTS/podcasts-explained.md`。

## 留档参考 ②：库内检索层参数（来源 open-webui，若做检索直接取用）

- **Markdown 标题切分**：H1-H6 作边界、保留标题、`chunk_min_size_target` 合并过碎块；默认 `RecursiveCharacterTextSplitter` 1000/overlap 100，可切 token 级。与「下游按段落取素材」天然对齐。
- **Hybrid search**：BM25（rank_bm25）+ 向量，EnsembleRetriever 加权融合 → reranker（本地 CrossEncoder 或外部）；换 embedding 模型需重索引。
- **引用回跳**：chunk metadata 带 file_id，prompt 注入 `<source id="N">` 要求模型只对带 id 的 source 输出 `[id]`。
- 上游实现：`retrieval/utils.py`、`routers/retrieval.py::process_file`、`config.py`（RAG 模板）。
- ⚠️ 不重开 B 组结项判断（B1 下游已自行解决检索；B6 证伪的是「多源整合篇」非「下游检索」）。本项只在「将来要做库内检索」时启用。

## 留档参考 ③：insight 独立 embedding（来源 open-notebook）

设计：源上派生的 insight 单独向量化，源全文未 embed 也能通过 insight 被搜到（检索面 = 源 chunk + insights + notes 三层）。对本库对应：**A4 提取清单已按源 hash 存档于 `notes/_meta/extract_checklists/`，将来做检索时即现成语义索引料**，无需新增产物。仅在触发 ② 时一并考虑。
