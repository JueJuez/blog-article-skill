# HANDOFF · 阶段C 全量重总结（新会话从这里接手）

> 触发词：**「继续阶段C重总结」** 或 **「重跑」**。
> 本文档自包含：新会话不需要读历史对话，按本 SOP 执行即可。
> 决策依据：`docs/decisions/DECISION-20260915-content-first-gate.md`（§7~§8）。

## 〇、当前状态（2026-09-17 阶段C 真收口 + 待重抓 98 条补跑 + 引流帖外链修复二轮 全部完成，实时进度以 PROGRESS.md 为准）

- **✅ 阶段C 真正全部完成：batch_01 – batch_67 全收口（690 / 690）**，无下一待处理批次。
- **✅ 待重抓清单 98 条（B站 15 + scys 83）已补跑完成**（2026-09-17）：
  - 源已按清单抓回：B站 → `notes/_scraped/bili/<bvid>.md`（`scripts/fetch_bili_by_bvids.py`）、scys → `notes/_scraped/scys/<topicId>.md`（`scripts/fetch_scys_by_ids.py` / `launch_scys_refetch_detached.py`），refetch_state.json 记录 done。
  - 批次：`scripts/build_refetch_resum_batches.py` 从 `summary_registry.json` 按 URL 反查 note_path/note_type，source_path 指向新抓源，生成 `notes/_meta/resum_refetch_batches/refetch_batch_01~15.json`（长 4/中 8/短 16 条每批，prompt 预计算）。**不碰已收口的 690 条批次**。
  - 派单（DISPATCH_PROMPT v3）→ 落盘 `resum_save_batch.py --force`（98/98 覆盖写回，0 失败）→ 抽检。
  - 抽检：39 条命中入台账 root_cause=gate（锚点 28 判据噪音 / 复述 7 代码-Prompt 块 verbatim / 代码块内超长句 2 / D相邻段重复 2）；33 条正文 A超长句机械拆句修复（root_cause=agent，`；，、→`+数字圈拆行，加粗跨行已回合并复查 0 奇数 `**`）。剩余 2 条 A超长句在代码围栏内（verbatim 提示词）保留。
  - 新会话如需续此类任务：**先哈希/存在性核对**（临时产物 + 落盘文件 vs 批次 JSON 条目数），勿只凭累计数字。
- **✅ scys 引流帖外链修复已完成（24 条，两轮）**（2026-09-17，用户两次指出）：
  - **第一轮**：识别 24 条「简介+大纲+飞书外链」引流帖（正文主体在外链），`build_refetch_resum_batches.py --ext` 生成「主文+外链」合并源并重总结落盘——但当时误判「飞书 wiki 分享链接可见上限 ~2000 字」，且**没发现项目已有 `feishu_ext_refetch.py`**。
  - **第二轮（用户质疑「抓不到飞书源」正确）**：
    - 项目早有 `scripts/feishu_ext_refetch.py`（2026-08-21）：飞书 wiki/docx 正文渲染在 `div.bear-web-x-container` 容器（**非 window**）+ 虚拟化渲染（视口外卸载），须**增量滚动容器逐视口收集 innerText、按行去重合并**。用 SharedCdpSession + `collect_full_text` 验证：单条从 2043 → 5004 字（完整正文），推翻 ~2000 上限结论。
    - 新增 `scripts/refetch_feishu_ext_batch.py`（复用 collect_full_text）：批量重抓 24 条外链全文（0.5–3 万字/篇）。**关键坑**：主文源里飞书链接是显示文本（带 `...` 截断），正则提取拼出假 URL → 飞书 404「页面不存在」（18 字）；修复 = 从现有 `_ext_*.md` 文件头 `> 来源：` 取完整 URL（当时来自 `<a href>`）。
    - 重建合并源（源长 0.5–3 万字）→ `resum_ext_batches/ext_batch_01~06.json` → 重总结落盘 24/24 → 抽检：3 条锚点噪音 + 3 条代码块 verbatim 入台账（gate），13 条正文 A超长句机械拆句修复。
  - ⚠️ 教训：① 重抓 scys 源不要 `--no-external`（现成 fetch_article 默认抓飞书外链）；② **飞书正文抓取用 `feishu_ext_refetch.py` 的 `.bear-web-x-container` 增量滚动**，不是滚 window；③ 飞书 URL 从 `<a href>`/`_ext` 头取完整版，别从正文显示文本取。
- **⚠️ 2026-09-17 修正上一会话误判**：上一交接声称「562/690 全部完成」，实际漏了 **batch_53–60（8 批 128 条）**——
  那 8 批从未生成临时稿、从未落盘（临时产物目录缺失 + 磁盘文件哈希=归档旧版一致为证）。
  本会话补做：派单生成 8 批 → 逐批落盘（`resum_save_batch.py --force`，含 batch_60 一条 note_path 缺失自动新建落地）→ 抽检。
- 抽检 batch_53–60：7 条命中全部核清——6 条锚点判据噪音（年份/抓取时间戳/分页控件 0/1200/微信号/评论区昵称，语义均已覆盖，root_cause=gate 入台账），
  batch_57[8][9] 为 interview 模板错用结构化复盘（与 batch_61–67 同类错误），已按访谈模板重做并复检 0 命中。
- **剩余收尾项（不在本任务内，见 HANDOFF「六、不在本任务内」）**：`manual_queue.json` 25 条《中国好公司》抓取失败、scys 旧队列 65、langzi 队列等。
- **派单一律用 v3 标准指令 `notes/_meta/resum_batches/DISPATCH_PROMPT.md`**（让子 Agent 先 Read 该文件再干活）：
  - LENGTH RULE：目标 30–50%（叙述）/ 50–80%（教程干货），**硬性 ≤100%**（落盘会加标签/来源链接等约 15% 膨胀，故临时稿目标 ≤85%）；覆盖率 > 比例，硬事实一条不许丢。
  - **门禁计数口径（2026-09-16 新增）**：`count_words` = 去空白后全部字符（**含 markdown 符号**与标点）。子 Agent 按「汉字+ASCII 词」自估会少算 15–35% → 以为 0.98× 实际门禁 1.28×。DISPATCH_PROMPT 已内置自测命令，派单时无需额外提醒。
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
| 落盘脚本（直写 note_path，绕过空 pending 队列） | `scripts/resum_save_batch.py <batch.json> <out_dir> --force` |
| 每批抽检脚本（content_signals，只读） | `scripts/resum_audit_batch.py <batch.json>` |
| 抽检根因台账 | `notes/_review_log.jsonl`（`log_review` 写入；归因同时落 `notes/_root_cause_ledger.jsonl`） |

### 二之二、补跑 / 外链修复专用脚本（2026-09-17 新增，下阶段同类任务直接复用）

| 产物 | 位置 |
|---|---|
| 按 bvid 列表逐条补抓 B站源（绕过 dedup、不总结不落盘） | `scripts/fetch_bili_by_bvids.py` |
| 按 topicId 列表逐条补抓 scys 源（`--list-md` 直读清单、`--no-external` 只主文） | `scripts/fetch_scys_by_ids.py` |
| scys 补抓 DETACHED 长任务启动器（单 Chrome 串行，限速 15–40s/条） | `scripts/launch_scys_refetch_detached.py`（子进程必带 `-u`，否则日志被块缓冲吞掉） |
| 批次反查重建（按 URL 从 `summary_registry.json` 反查 note_path/note_type，prompt 预计算） | `scripts/build_refetch_resum_batches.py`（`--ext` 生成引流帖外链合并源） |
| 批量重抓飞书外链全文（复用 `collect_full_text`） | `scripts/refetch_feishu_ext_batch.py` |
| 落盘（直写 note_path，绕过空 pending 队列） | `scripts/resum_save_batch.py <batch.json> <out_dir> --force` |

## 三、执行 SOP（每批 4 步，严格按序）

1. **派子 Agent**（general-purpose，单发最稳；并发 2 安全、3–4 必 429）：
   让子 Agent **先 Read `DISPATCH_PROMPT.md`（v3）**，再读 `batch_NN.json`，逐条串行：
   读 `source_path` 全文 → 严格按条目 `prompt` 写笔记 → 写临时 md
   `_tmp/resum_c/out/batch_NN_<idx02d>.md`（UTF-8）。硬性格式：首行 `**作者**：…` /
   无一级标题 / 无来源链接行 / 无标签行 / 末行 `【核心主题词】词1 | 词2 | 词3`。
2. **落盘**：`python scripts/resum_save_batch.py notes/_meta/resum_batches/batch_NN.json _tmp/resum_c/out --force`
   （复用生产链路变换 + 机械门禁；`note_path` 缺失自动新建落地；返回 `ALREADY_EXISTS`/已落盘视为成功）。
3. **批量派单三禁令**（RULES.md）：禁子 Agent 自写脚本直调 API / 禁对已存在笔记二次 force /
   Agent 工具报错 ≠ 没干活——**先核验目标目录再决定重派**（batch_39 实测：子 Agent 漏写 1 条却报「7/7 完成」，必须对照批次 JSON 条目数逐条核文件）。
4. **每批收尾抽检**：`python scripts/resum_audit_batch.py <batch.json>`，命中项填 `root_cause`
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

- ~~待重抓（下阶段）：B站 15 + scys 83~~ —— **✅ 已于 2026-09-17 补跑完成**（见「〇、当前状态」），原清单 `notes/_reports/20260915_待重抓清单.md` 视为已消费。
- 剩余未结：`manual_queue.json` 25 条《中国好公司》5~29 集抓取失败，需单独排重抓。
- 语义未明的旧队列：`scys/resummarize_list.json`(65)、`langzi_queue/_groups_todo.json`(7)/`_refetch_todo.json`(86)。
- 源留存策略（公众号 raw 会被覆盖 → 源缺失根因）待拍板后实现。
