# blog-article-skill 项目备忘

> ⚠️ **会话开始先看这里**：用户给 YouTube 链接要抓字幕/总结 → 直接 `videos/run.py --url <url>`（或 `fetch_transcript(url)`），内部自动 API→CDP 回退，**不要写临时脚本、不要手搓 URL、不要 diagnose**。返回 `None` 且页面能打开（`captionTracks` 为空）→ **原样回「【此视频暂无 CC 字幕，无法为你抓取字幕总结内容。】」并停止**，禁走 ASR/禁改代码兜底（除非用户明确要开发）。仅当机制故障（9222 连不上/页面空白）才查 `references/youtube-cdp-workflow.md`。

> ⚠️ **加载层架构**：`RULES.md`/`SKILL.md` 是源文件（source of truth）。`SKILL.md` 在 `.workbuddy/skills/blog-article-skill/`，但平台 `available_skills` 未含它，**新会话不自动注入 SKILL.md**。**真正每会话被注入的是本 MEMORY.md**——改规则必须同步到本文件顶部才能在换会话/换 AI 时生效。

---

## 规则摘要（完整见 RULES.md）

- **完整闭环 = 抓 → 找模板 → 按模板总结 → 存（换任何 AI/模型都照此四步；入口即真相，勿手写抓取/总结）**：
  1. **抓**：文章 `articles.skill_main({"content": url_或_原文})`；视频 `videos/run.py --url`（或 `videos.summarize_video`）→ `fetch_transcript` 自动 API→CDP、长内容走 `shared.chunking` 两段式。
  2. **找模板**：模板在 `prompts/templates.py` 的 `NOTE_TEMPLATES`（**7 种**：`structured` 结构化复盘 / `key_points` 要点提炼 / `case` 案例拆解 / `opinion` 观点卡 / `interview` 访谈 / `roundup` 盘点横评 / `reading` 读书书摘）；`prompts.classify_note_type(title, content)` 自动选型，或调用时手传 `note_type` 覆盖。**分类优先级**：教学超信号(structured) > 访谈(interview) > 要点(key_points) > 盘点(roundup) > 读书(reading) > 观点(opinion) > 案例(case) > structured 兜底。**分类修复（2026-07-21）**：教学/教程类视频（手把手/保姆/实操/从零/教程/课程/step by step）经「教学超信号」优先判 `structured`，不再被 `视频` 误判为口播要点；访谈（访谈/对谈/专访/Q&A）已从 KEY_POINTS 移出独立成 `interview` 类（不再误判口播要点）。**内容级访谈兜底（2026-07-21 二巡）**：标题无 cue 时（如「95后女老板Judy」式创业对谈）用正文前 2500 字做「主持人向特定嘉宾的人生/状态探针（你当时/你后来/你是怎么/你创业…）且观众独白口吻不占主导」判别；读书额外认《》书名号强信号、盘点额外认「榜/榜单/红黑榜/种草/闭眼入/抄作业/排行」；**`视频` 已从 KEY_POINTS 彻底移除**（URL 本身即载体，不当类型信号——否则字幕里一句「这个视频」就把盘点/访谈抢成口播要点）。**三类新模板（2026-07-21）**：`interview`（问答对+嘉宾背景+双方分歧表格+金句）、`roundup`（维度×对象对比矩阵+场景结论+选购建议）、`reading`（全书地图+核心论点拆解+金句+行动启发）。**思维模型透镜（提质·按需）**：全部 7 模板共用 `UNIVERSAL_RULES` 第九节（structured 内联第十四节），6 模型按序 LIST（第一性原理→5-Why冰山→二阶思维→脉络还原→奥卡姆剃刀→类比迁移）逐条过、不适用跳过，不破去水分红线；直接服务「质量高、上下文清晰」。
  3. **按模板总结**：入口函数自动取对应模板做 AI 总结；**无外部 AI → 降级**：返回 `need_continue_summary`+`prompt`(=该 note_type 模板)+原文，前端模型用该 `prompt` 自己总结后调 `skill_continue_summary`/`save_summary_only` 存档。
  4. **存**：见下「双写契约」，`OutputManager.save_all()` 自动落全部可用输出。
- **双写契约（强制·用户偏好，即第④步落点）**：总结成品**必须落 Obsidian + 飞书双端**。本地 `notes/` **仅在 Obsidian 与飞书都未配置时才作兜底写入**（用户原话"有 obsidian 和飞书，本地就不需要写了"；代码 `videos.main._local_write_enabled()` 已强制：两者任一可用即不写本地，**AI 不要手动写 `notes/`**）。飞书已配好（`.env` 的 `FEISHU_WIKI_SPACE`+`FEISHU_WIKI_PARENT_NODE`），user 身份已授权，跑总结自动落飞书。
- **待归类收件箱（用户偏好·2026-07-21）**：新单篇总结默认落「待归类」（Obsidian `待归类/` 文件夹 + 飞书「待归类」节点，由 `OBSIDIAN_INBOX`/`ensure_inbox_node` 保证），用户后续手动拖到分类。**系列课例外**（自带子目录/走 `save_series`，不进待归类）。Obsidian 分类文件夹 ↔ 飞书分类节点对称：`待归类`+`01_独立开发`~`07_内容创作`+`千刀千法` 系列容器。双端缺笔记时按原分类补平（补到分类非待归类）。详 RULES.md §3.1。
- **系列课（B站 ugc_season/多P）**：先建「系列名」容器节点（飞书），各集挂其下，并**必生成 `00_系列总览.md`**（Obsidian+飞书双写，从云真值读回重生成；重生成前先删旧总览节点，否则飞书会新建第 2 个总览）。总览现含「学习路径」段（AI 基于各集标题+一句话结论生成建议顺序+先修说明），保留原各集导航表。
- **grill_rules（RULES.md §6，alwaysApply）**：架构级/跨多模块改动 → 先拷问拉齐认知再动手（决策清单≤15行 + 测试草稿 RED）。小修/改文案直接干。
- **工程纪律**：`.env`/`notes/` 已 gitignore 禁提交；写文件用 Python IO 禁 shell（飞书 CLI 例外）；不手搓 timedtext URL；笔记作者栏显真实作者/UP主，禁无端【作者未知】。
- **大批量(>3条)→子Agent隔离**：用 Agent 派发子 Agent 并行处理防主线程胀爆，prompt 须自包含（双写契约/入口/note_type）。⚠️ 飞书并发重复坑见下「系列课并发」；**安全模式**：子Agent只返文本＋元数据，编排方**串行**调保存入口落盘。详 RULES.md §4.1。

## 关键坑（必记，否则重踩）

- **飞书 wiki CLI**：① `wiki +node-list` 返回 `data.nodes`（非 items）；② 删节点 `--obj-type wiki`（非 docx）；③ 容器用 `--obj-type docx` 节点充当；④ 测完务必 `--obj-type wiki --yes` 清理测试节点。
- **系列课并发/重生成**：① 集级无查重 → 并发写同集会建重复节点，需 `docs +fetch` 取哈希比对后 `wiki +node-delete` 清理；② 重生成总览先删旧节点（飞书 `save_series` 是新建非更新）；③ 全集写完后务必调一次 `_generate_series_overview` 刷新，避免总览过时。
- **YouTube 细节**：全自动见 `RULES.md §4.4` / `references/youtube-cdp-workflow.md`，AI 只需调一个函数，勿手搓/勿自写脚本。
