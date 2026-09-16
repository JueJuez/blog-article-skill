# HANDOFF · 阶段C 全量重总结（新会话从这里接手）

> 触发词：**「继续阶段C重总结」** 或 **「重跑」**。
> 本文档自包含：新会话不需要读历史对话，按本 SOP 执行即可。
> 决策依据：`docs/decisions/DECISION-20260915-content-first-gate.md`（§7~§8）。

## 〇、当前状态（2026-09-16 交接，实时进度以 PROGRESS.md 为准）

- **已收口 batch_01 – batch_39（277 / 690）**；下一待处理 **batch_40**。
- **预归档已完成 batch_40~67**（旧版在 `notes/_archive/resum_old/`），后续每批**无需再跑归档脚本**，直接 派单→落盘→抽检。
- **派单一律用 v3 标准指令 `notes/_meta/resum_batches/DISPATCH_PROMPT.md`**（让子 Agent 先 Read 该文件再干活）：
  - LENGTH RULE：目标 30–50%（叙述）/ 50–80%（教程干货），**硬性 ≤100%**（落盘会加标签/来源链接等约 15% 膨胀，故临时稿目标 ≤85%）；覆盖率 > 比例，硬事实一条不许丢。
  - 句式纪律：单句 ≤60 字；思维模型自检**逐模型分行**；30秒速览拆短句。
  - 版本沿革：v1（30–50%/60% 硬上限）→ 试点稳定 0.26–0.50×；v2 放太松致 batch_31 回潮 0.69–1.14×；**v3 校准后 batch_31 重做 0.37–0.70×、batch_32–39 稳定 0.5–0.9×**。

## 一、任务是什么

把 `notes/_meta/resummarize_queue.json` 里 **690 条**旧笔记按**新版生成 prompt** 重写并落盘
（旧笔记压缩过狠：源 2 万字 → 笔记 1000 字、缺必备模块；新版 prompt 已在试点 A/B 验证通过，
见 `notes/_reports/20260916_重总结试点A_B对照.md`）。

## 二、已就绪的产物

| 产物 | 位置 |
|---|---|
| **67 个批次文件**（长源 4 条/批 · 中源 8 条/批 · 短源 16 条/批，prompt 已逐条预计算） | `notes/_meta/resum_batches/batch_NN.json` |
| **v3 派单标准指令**（子 Agent 必读） | `notes/_meta/resum_batches/DISPATCH_PROMPT.md` |
| 进度台账（检查点 / 判据噪音档案 / 坑） | `notes/_meta/resum_batches/PROGRESS.md` |
| 批次清单（含 6 条存疑源匹配，已人工核对全部正确） | `notes/_reports/20260916_重跑批次清单.md` |
| 重跑队列原始数据（690 条） | `notes/_meta/resum_queue.json`（= `resummarize_queue.json`） |
| 旧版归档脚本（batch_40~67 已预归档，仅异常补跑时用） | `scripts/archive_old_before_resum.py` |
| 落盘脚本（直写 note_path，绕过空 pending 队列） | `scripts/_save_resum_batch.py <batch.json> <out_dir> --force` |
| 每批抽检脚本（content_signals，只读） | `scripts/_audit_resum_batch.py <batch.json>` |
| 抽检根因台账 | `notes/_review_log.jsonl`（`log_review` 写入） |

## 三、执行 SOP（每批 4 步，严格按序）

1. **派子 Agent**（general-purpose，单发最稳；并发 2 安全、3–4 必 429）：
   让子 Agent **先 Read `DISPATCH_PROMPT.md`（v3）**，再读 `batch_NN.json`，逐条串行：
   读 `source_path` 全文 → 严格按条目 `prompt` 写笔记 → 写临时 md
   `_tmp/resum_c/out/batch_NN_<idx02d>.md`（UTF-8）。硬性格式：首行 `**作者**：…` /
   无一级标题 / 无来源链接行 / 无标签行 / 末行 `【核心主题词】词1 | 词2 | 词3`。
2. **落盘**：`python scripts/_save_resum_batch.py notes/_meta/resum_batches/batch_NN.json _tmp/resum_c/out --force`
   （复用生产链路变换 + 机械门禁；`note_path` 缺失自动新建落地；返回 `ALREADY_EXISTS`/已落盘视为成功）。
3. **批量派单三禁令**（RULES.md）：禁子 Agent 自写脚本直调 API / 禁对已存在笔记二次 force /
   Agent 工具报错 ≠ 没干活——**先核验目标目录再决定重派**（batch_39 实测：子 Agent 漏写 1 条却报「7/7 完成」，必须对照批次 JSON 条目数逐条核文件）。
4. **每批收尾抽检**：`python scripts/_audit_resum_batch.py <batch.json>`，命中项填 `root_cause`
   （`prompts/review_rubric.py: log_review`）；**同一根因 ≥3 次 → 先检测真因再改，不是无脑改 prompt**
   （判据噪音 vs 模板×源密度冲突 vs 子 Agent 执行，见 PROGRESS「判据噪音档案」与 `_review_log.jsonl` 的 batch_30/31 根因检测记录）。
   抽检干净后更新 PROGRESS.md 检查点。

## 四、执行注意（血泪教训，勿跳过）

- **长源批只能 4 条/批，中源 8 条/批，短源 16 条/批**——已按源长切好，不要合并批次。
- **轻模板重点抽检**：opinion(27) / key_points(13) / interview(5) 没参加过试点，第一批抽检以它们优先（批次 41~67 短源批注意）。
- **A超长句**是已知观察项：若命中「思维模型自检」整行（多个模型被「；」塞一行），机械拆行即可
  （按 `）、(?=第一性原理|二阶思维|…)` 拆，见 PROGRESS）；命中正文数据密集句则 inline 拆句。
- **存疑 6 条**（batch_67）：已人工核对 source_path 全部正确（关键词匹配器误报：B站转写/scys 原文不含标题原词）；
  6 条空 prompt 已补全（`get_note_prompt(结构化, 源长) + QUALITY_GATE_SELFCHECK`），直接可跑。
- 落盘顺序：**先归档再 force**（batch_40~67 已预归档，直接 force 即可；异常补跑需先跑归档脚本）。
- 队列清洗：`filter_pending.py` 只清洗 `pending_summaries.json`，**不适用于本队列**；本队列按批次文件消费，
  消费完更新 PROGRESS.md 检查点（不依赖改名 `.done`）。

## 五、判据噪音档案（抽检命中先对照，勿自动归因）

- **锚点召回<60%**：该类源持续误报——QQ号(361079)/抓取时间戳/作者引流句(4000人社群)/年份(2016/2025/2026)/
  演示数字(12345..789, 3818)/示例金额(5500/6000/7000)/词典误标(副业, K线)。命中先看 missed 列表上下文。
- **D相邻段重复**：术语卡列表统一样式（`- **术语**：解释`）致行间相似度高，实为不同术语。
- **A超长句**：多为「思维模型自检」多模型被「；」塞一行（真问题，拆行）；或 ASR 密集句（拆句）。
- 以上均已记入 `notes/_review_log.jsonl`（root_cause=gate）供查证。

## 六、不在本任务内（另列）

- 待重抓（下阶段）：**B站 15 + scys 83**，清单 `notes/_reports/20260915_待重抓清单.md`；
  另有 `manual_queue.json` 25 条《中国好公司》5~29 集抓取失败，需单独排重抓。
- 语义未明的旧队列：`scys/resummarize_list.json`(65)、`langzi_queue/_groups_todo.json`(7)/`_refetch_todo.json`(86)。
- 源留存策略（公众号 raw 会被覆盖 → 源缺失根因）待拍板后实现。
