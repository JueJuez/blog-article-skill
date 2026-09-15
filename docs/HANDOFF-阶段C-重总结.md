# HANDOFF · 阶段C 全量重总结（新会话从这里接手）

> 触发词：**「继续阶段C重总结」** 或 **「重跑」**。
> 本文档自包含：新会话不需要读历史对话，按本 SOP 执行即可。
> 决策依据：`docs/decisions/DECISION-20260915-content-first-gate.md`（§7~§8）。

## 一、任务是什么

把 `notes/_meta/resummarize_queue.json` 里 **690 条**旧笔记按**新版生成 prompt** 重写并落盘
（旧笔记压缩过狠：源 2 万字 → 笔记 1000 字、缺必备模块；新版 prompt 已在试点 A/B 验证通过，
见 `notes/_reports/20260916_重总结试点A_B对照.md`）。

## 二、已就绪的产物（本会话已备好）

| 产物 | 位置 |
|---|---|
| **67 个批次文件**（长源 4 条/批 · 中源 8 条/批 · 短源 16 条/批，prompt 已逐条预计算） | `notes/_meta/resum_batches/batch_NN.json` |
| 批次清单（含 6 条「存疑」源匹配，先核对再跑） | `notes/_reports/20260916_重跑批次清单.md` |
| 重跑队列原始数据（690 条） | `notes/_meta/resum_queue.json`（= `resummarize_queue.json`） |
| 旧版归档脚本（每批跑前先执行） | `scripts/archive_old_before_resum.py` |

## 三、执行 SOP（每批 5 步，严格按序）

1. **归档旧版**：`python scripts/archive_old_before_resum.py notes/_meta/resum_batches/batch_NN.json`
2. **派子 Agent**：读 `batch_NN.json`，逐条串行：读 `source_path` 全文 → 严格按条目 `prompt` 写笔记 →
   写临时 md（`_tmp/resum_c/out/<bvid或slug>.md`）。硬性格式：无一级标题 / 无来源链接行 / 无标签行 /
   首行 `**作者**：…` / 末行 `【核心主题词】词1 | 词2 | 词3`。
3. **落盘**：`python scripts/_save_summary_from_file.py <bvid> <md绝对路径> --force`
   （force=覆盖同名+豁免 dedup；返回 `ALREADY_EXISTS` 视为成功）。
4. **批量派单三禁令**（RULES.md）：禁子 Agent 自写脚本直调 API / 禁对已存在笔记二次 force /
   Agent 工具报错 ≠ 没干活——**先核验目标目录再决定重派**。
5. **每批收尾**：跑 `python scripts/audit_gate_signals.py --corpus batch` 级别的抽检
   （或对当批笔记跑 content_signals），命中项填 `root_cause`（`prompts/review_rubric.py: log_review`）；
   同一根因 ≥3 次 → 停下来改 prompt，不是继续抽。

## 四、执行注意（血泪教训，勿跳过）

- **长源批只能 4 条/批**（2~5 万字源），短源才 16 条/批——已按源长切好，不要合并批次。
- **轻模板重点抽检**：opinion(27) / key_points(13) / interview(5) 没参加过试点，第一批抽检以它们优先。
- **A超长句**（试点 3/5 命中）是已知观察项：抽检命中就 fix_inline 拆句；全量后仍高发再把「单句≤60字」升硬性。
- **存疑 6 条**（批次清单最后一批）：先人工核对 `source_path` 是否真为该笔记的源，错了修队列再跑。
- 落盘顺序：**先归档（第1步）再 force**，否则旧版无备份。
- 队列清洗：`filter_pending.py` 只清洗 `pending_summaries.json`，**不适用于本队列**；本队列按批次文件消费，
  消费完把批次文件改名加 `.done` 或记录进度到 `notes/_meta/resum_batches/PROGRESS.md`。

## 五、不在本任务内（另列）

- 待重抓（下阶段）：**B站 15 + scys 83**，清单 `notes/_reports/20260915_待重抓清单.md`；
  另有 `manual_queue.json` 25 条《中国好公司》5~29 集抓取失败，需单独排重抓。
- 语义未明的旧队列：`scys/resummarize_list.json`(65)、`langzi_queue/_groups_todo.json`(7)/`_refetch_todo.json`(86)。
- 源留存策略（公众号 raw 会被覆盖 → 源缺失根因）待拍板后实现。
