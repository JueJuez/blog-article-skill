# RULES.md — blog-article-skill 项目规则与地图索引（规则级文档）

> **角色声明**：这是项目的「规则级」文档（地图索引），是**规则的唯一来源（source of truth）**。
> AI 在本项目**任何会话开始**时，必须先读本文件并严格遵守；本文件随项目演进**持续更新**。
> **本文件只放「规则 + 地图/指针」，不堆长篇实现细节**——细节在 `references/` 与各模块源码。

---

## 0. 同步约定（本文件 ↔ 平台规则）

本文件会持续更新。为保证「换会话/换 AI 也不遗忘」，确立以下同步机制：

1. **每次更新本文件后，AI 必须同步**：
   - 把关键变更写入 `.workbuddy/memory/MEMORY.md` 的「规则摘要」区块（该文件在会话开始时被注入，是跨会话不忘的兜底）；
   - 确保 `SKILL.md` 顶部保持指向本文件；
   - 后续会话把本文件作为**强制规则**加载。
2. **AI 平台加载方式**：会话开始时注入的 `MEMORY.md` 携带「规则摘要 + 指向 RULES.md」；技能被触发时 `SKILL.md` 也指向本文件。两者结合，本文件即被「加载进规则」。
3. **`alwaysApply: true` 类规则（平台级）必须同步**：本文件中带「平台级/alwaysApply」语义的条款——**尤其是 §6 grill_rules（认知拉齐拷问规则）**——各 AI 平台须作为平台级规则加载并在更新时同步，不得只在单次会话内存活。任何涉及「架构级改动 / 新功能链路 / 跨多模块改动」的开发任务，**先按 §6 grill_rules 拷问拉齐认知，再动手编码**。

---

## 1. 文档职责划分（每个文件该干什么）

| 文件 | 角色 | 是否放细节 |
|------|------|-----------|
| **RULES.md（本文件）** | 规则 + 地图索引（规则唯一来源） | ❌ 只放条目与指针 |
| `SKILL.md` | 技能触发条件 + 调用入口 + 对话输出规范（面向「怎么用」） | 少量用法，规则指向 RULES.md |
| `AGENTS.md` | **平台无关真源入口**（跨 WorkBuddy / Cursor / Claude / Codex / Copilot / 裸 API 通用） | 能力清单 + 接口速查 + 配置引导 + 各平台加载方式，指向本文件 |
| `references/` | 专项详细文档（`config.md` 配置、`youtube-cdp-workflow.md` 抓取流程、`testing_rules.md` TDD 流程、`glossary.md` 术语表；`PRD.md` / `scys-cdp-lessons-learned.md` 已归档至 `_archive/`） | ✅ 放深入细节 |
| `docs/decisions/` | grill_rules 的「产出 A：决策清单」存放地（`DECISION-YYYYMMDD-{slug}.md`，≤15 行） | ✅ 极轻量 |
| `articles/` `videos/` `prompts/` `shared/` | 实现细节的唯一真相 | ✅ 代码即文档 |
| `.workbuddy/memory/MEMORY.md` | 长期要点 + 「规则摘要」（会话开始注入） | ❌ 只摘要点 |
| `.workbuddy/memory/YYYY-MM-DD.md` | 每日工作日志（做了什么、踩了什么坑） | 过程记录 |
| `notes/` | 产出与原始内容（gitignore，不进仓库） | — |

---

## 2. 项目定位与适用场景

- **一句话定位**：把「文章/视频链接、原文、字幕」自动转成结构化/要点笔记，并归档到飞书（默认）/ Obsidian（按需）。
- **触发场景（同时满足）**：① 用户说「总结/提炼/整理/归档/保存笔记」类词；② 给了素材（文章链接、原文粘贴、视频/字幕）。
- **不触发**：只聊概念没给素材、或纯答疑。
- **默认行为**：不在对话框输出完整笔记正文，只给 1~3 句核心结论 + 成品路径。

---

## 3. 两条业务路线（怎么跑）

> 两条路线都复用 `prompts/`（笔记模板）与 `articles` 的保存能力（`OutputManager`），**入口函数即真相**，勿手写抓取/总结。详细运行流程与已知坑见 **`monitors/README.md`**（监控运营）与各模块 `run.py --help`。

### 3.0 默认飞书、Obsidian 按需（强制 · 2026-08-08 单写优先）

> **用户规则（2026-08-08）**：写两遍浪费，**默认只写飞书**，Obsidian 仅在用户明确要求时才写。代码门禁已落地（`OutputManager` 默认只写飞书），不靠 AI 记性。
> - 用户说"写到 obsidian / 双写"时，在对应入口加 `--obsidian`（文章/视频/监控均支持）；`.env` 设 `OBSIDIAN_WRITE=1` 可一键回退双写。
> - 飞书不可用（`DISABLE_FEISHU_SYNC=1`）且未请求 Obsidian 时回退本地 `notes/`，避免丢数据。

### 3.1 【待归类】收件箱约定（强制）

新总结先统一进「【待归类】」，用户后续手动拖到分类（启用 Obsidian 时与之对称，默认只写飞书）。单篇默认落「【待归类】」；系列课自带 `系列名/` 子目录，不进【待归类】。飞书分类节点与 Obsidian 分类文件夹一一对应（逻辑在 `articles/feishu.py` / `articles/obsidian.py`）。

### 3.2 监控账号归档：日更 + 系列自动归类 + 总览排序（2026-08-25）

- 非系列内容落 `日更` 节点（`【监控】/<平台>/<账号>/日更/`，纯名无【】）；系列课按 `subscriptions.json` 的 `series_patterns` 归入对应系列容器。
- 排序不靠飞书导航（wiki 无 sort_order），靠每个账号容器下的「总览文档」做唯一有序索引（自动按发布时间倒序、幂等去重）。存量重建见 `scripts/rebuild_overviews.py` / `promote_existing.py`。
- scys 新帖监控窗口以 `scripts/scys_projects.json` 的 `defaults.since_days` / `subscriptions.json` 的 scys 条目覆盖为准（不写死）。

### 路线入口速查

- **文章总结**：`articles/run.py --url/--content/--batch` 或 `skill_main({...})`；无外部 AI 时入 `pending_summaries.json` 交子 Agent（见 `monitors/README.md`「降级闭环」）。
- **视频总结**：`videos/run.py --url/--file/--content` 或 `summarize_video({...})`；YouTube 本机无出口走 CDP（见 `references/youtube-cdp-workflow.md`）。模板种类与分类优先级见 `prompts/templates.py` / `prompts/classify.py`。
- **订阅监控**：`monitors/run.py --mode first|auto --apply`；关注某账号走机械命令 `--subscribe`（详见 `monitors/README.md`）。

---

## 4. 项目规则（强制）

### 4.1 性能与实现规范
- **同步 → 异步**：网络抓取、AI 调用、磁盘 I/O 等阻塞调用，优先异步化（`asyncio` / `run_in_executor`），勿阻塞主流程。
- **循环 → 一次性查询后筛选**：循环内逐条查询/抓取/请求，优先改为「一次批量查询/抓取，再在内存里筛选」，**避免 N 次往返**。
- **串行 → 并行**：多个独立任务（多视频 / 多文件 / 多链接）评估并行（`asyncio.gather` / 线程池），注意限流与去重，避免无意义串行等待。
- **复用入口，不重复造轮子**：统一走 `fetch_transcript` / `skill_main` / `summarize_video` / `OutputManager` 等既有入口，禁止在多处复制抓取/保存逻辑。
- **已总结内容机械拦截（三层前置 · 2026-08-25）**：AI 只交总结，「要不要总结 / 写不写」由代码决定——①入队：`run.py` 查 dedup 索引，已总结 URL 不入队；②派单前：`python scripts/filter_pending.py` 清洗 monitors + scys 两队列（已总结条目出队，不浪费总结 token）；③落盘：`save_summary_only` / `_save_summary.py` 查索引，命中返回 `skipped` 并按成功出队（`force` / `--force` 强制重写）。多 Agent 接力（前一个积分耗尽/中断）不重复总结、不重复落飞书。决策见 `docs/decisions/DECISION-20260825-dedup-frontload-and-lock-release.md`。
- **长内容必走两段式分块**：超过单模型上下文的内容，先经 `shared.chunking` 分块再总结，禁止整篇直接喂模型。
- **大批量 → 子 Agent 隔离主线程（防上下文胀爆）**：当待处理内容达到批量阈值（如 >3 条笔记/视频，或单批原文大到会撑爆主会话上下文）时，**必须**用 Agent 工具派发子 Agent 并行处理，勿把全部原文/中间稿堆在主线程。注意：① 子 Agent 上下文是空白的，派发 prompt 必须**自包含**（嵌入输出契约：落盘闸门＝默认飞书、obsidian=True 时追加；入口函数 `videos/run.py --url` 或 `skill_main`、`note_type`、YouTube/无字幕规则按需）；② **飞书并发重复坑**：多子 Agent 同时 `save_series` 写飞书会因集级无查重建重复节点（见 §4.7）；**安全模式**＝子 Agent 只**返回成品 Markdown 文本＋元数据**（标题/作者/url/tags/note_type），由编排方**串行**调保存入口（`_save_series_note` / `save_all`）落盘，绝不让多子 Agent 并发各自调 `save_series`。

### 4.2 工程纪律
- **个人信息保护**：`.env`（含 `OBSIDIAN_VAULT_PATH`、`FEISHU_WIKI_SPACE` 等）与 `notes/` 已被 gitignore，**禁止**手动 `git add` 提交。
- **文件写入用 Python 原生 IO**（UTF-8），**禁止**用 shell 命令写文件（飞书 CLI 上传是唯一例外）。
- **新增笔记类型**：只在 `prompts/templates.py` 的 `NOTE_TEMPLATES` 加一项，无需改其他代码。
- **默认不在对话框输出完整笔记正文**；只给结论 + 成品路径。
- **不手搓 timedtext URL**：YouTube 字幕必须走 `fetch_transcript`（API 或 CDP），旧格式手搓 URL 已 404，新格式需 PoToken 由库/播放器处理。
- **不改动 `.env` / `notes/` 结构** 以外的隐私数据。

### 4.3 文档职责（与本文件一致）
- **规则/索引只放 RULES.md**；长篇实现放 `references/` 或代码注释；RULES.md 不堆细节。
- **改了代码行为 → 同步更新 RULES.md 相关条目与 `references/` 对应文档**，保持地图与实现一致。

### 4.4 视频字幕抓取 · 终端行为与「无 CC 字幕」约定（强制）

> 这条约定是**终端行为**，不是可选项：视频在 YouTube 上没字幕时，AI 必须**原样回下面这句话并停止**，不得补救、不得开发。

- **统一入口（勿另写脚本）**：YouTube/Bilibili 字幕只用 `videos.fetch.fetch_transcript(url)`（或 `videos/run.py --url`）。它已内置 API→CDP 全自动回退，**禁止**为「只收集字幕」自写自定义抓取脚本。
- **「无 CC 字幕」→ 自动走 ASR 兜底，失败才终态**（用户规则 2026-08-06 授权）：当 `fetch_transcript` 返回 `None`（视频本身无 CC / 自动字幕轨道），**不再直接停**，而是由 `videos.main._handle_single_video` 自动调用 `videos.asr.transcribe_video`（下载音频 + 本地 faster-whisper 转写）。
  - ASR 成功 → 继续正常总结并落盘（默认飞书，带 `obsidian` 时双写）。
  - ASR 也失败（音频下载或本地转写未成功，可能需 B站登录态 / 网络受限 / YouTube 无出口）→ 才回下面这句并停止：
  > **【此视频暂无可用字幕（CC 与 ASR 兜底均失败），无法总结内容。】**
  - **不要**在 `videos/asr.py` 已提供的兜底之外「自作主张开发新兜底」。环境坑（HF 镜像 / xet / CUDA dll / 沙箱安全删除）已由 `asr.py` 的 `_apply_env_defaults()` 自动处理，**无需手敲 export、不要 diagnose**。
- **区分「真无字幕」vs「抓取机制故障」（避免误判导致乱调试）**：
  - 真无字幕：`capture_transcript` 已连上 9222、页面正常加载（能拿到标题）、但 `captionTracks` 为空 → **直接回上面那句话**，不要调试。
  - 抓取机制故障：9222 连不上 / 页面空白 / 代理失效（YouTube 打不开）→ 这是**基础设施问题**，不是视频没字幕；按 `references/youtube-cdp-workflow.md` §6 排查（重跑 `python videos/cdp_launch.py` 自修复），**不要**把它当成「无字幕」回给用户。
- **最短路径（新会话拿到链接即走这条，勿自创）**：`fetch_transcript(url)` → 内部自动完成：
  ① **复制/同步 Chrome 配置**（代理扩展 iGuge → 独立副本 `Chrome-CDP`，`ensure_chrome_running`）→
  ② **用 CDP 打开视频路径**（PUT 开标签 + ws `suppress_origin=True`）→
  ③ **访问字幕接口**（让播放器自发 `/api/timedtext`，Network 拦截响应体转纯文本）。
  AI 无需关心中间步骤，调**一个函数**即可；不要自己写脚本、手搓 URL、或调试这三步。

---

### 4.5 作者信息提取约定（强制）

> 目标：笔记作者栏**永远显示真实作者/UP主**，不再出现【作者未知】。

- **作者必须自动从源元数据提取**，禁止无端输出【作者未知】。`KEY_POINTS_PROMPT` 已写明「作者必须提取真实名字，除非源确实无任何作者信息，否则禁止输出【作者未知】」。
- **视频（B站）链路**：`videos.fetch._bili_get_video_info` 取 `owner.name`；`fetch_bilibili_series` 的 `ugc_season` / 多P 两分支返回带 `author`；`videos.main._handle_bilibili_series` 用 `author = series.get("author","") or input_data.get("author","")` 兜底——**调用方无需手传 author**。
- **文章链路**：`articles` 抓取/总结时同理取作者字段，调用方无需手传。
- **唯一例外**：源元数据确实为空（真无作者信息）时，才允许标【作者未知】。

### 4.6 质量保障三件套（提质 · 默认行为说明）

> 目标：让笔记「质量高、上下文清晰、不破去水分红线」。三件套均在 `prompts/templates.py` 落地，由入口函数自动套用，**AI 无需手动触发**。

- **A. 质量闸门（second-pass verifier）**：总结后再调一次 AI 按 6 红线打 0–100 分，低于阈值带反馈重试一次。**默认关闭**（省一轮 AI 调用）；开关见下方「去哪里开关」。无外部 AI 的降级路径走 `QUALITY_GATE_SELFCHECK` 自检段（模板 prompt 内嵌 6 红线，外层模型自核对）。
- **B. 字幕清洗层**：`shared/subtitle_clean.py` 纯函数（`preprocess_segments`/`preprocess_text`），已在 `videos/fetch.py` 三路径（B站原生 / YouTube API+CDP / yt-dlp 兜底）自动接入；只清洗口误填充词（删独立语气词 嗯/啊/呃）、合并相邻近重、≥8 字长句去重——**不激进折叠**（保留"然后/那个"等自然语流）。
- **C. 强制证据红线（思维模型透镜）**：全部 9 模板共用 `UNIVERSAL_RULES` 第九节（structured 内联第十四节），6 模型按序 LIST（第一性原理→5-Why冰山→二阶思维→脉络还原→奥卡姆剃刀→类比迁移）逐条过、不适用跳过；**每条适用模型须给「洞察（不同视角）+ 原文证据句（「」括起原话，禁改写）」**，禁只写"用了X 模型"；全不适用须逐条列 6 模型理由。与去水分红线兼容，不硬凑固定章节。
- **D. 元数据归一**：`UNIVERSAL_RULES` 强制 `#标签1 #标签2` 井号格式；`normalize_note_metadata()` 把 `**标签**：xxx` 转井号，`format_note_with_prompt` 自动应用。
- **E. 读书争议维度**：`reading` 模板含「争议与不同声音」段（作者回避点 / 学界不同声音 / 与已知冲突，标笔记者补充存疑）——**推荐、非强制**，非争议类书评不硬凑。

**去哪里开关（质量闸门 A）**：

在技能根目录 `.env` 设置环境变量（与 `AI_PROVIDER` 等同文件）：

```env
NOTE_QUALITY_GATE=1        # 默认 0（关）；置 1 开启第二遍 AI 把关
NOTE_GATE_THRESHOLD=85     # 评分阈值，默认 85；低于此分触发重试
```

详见 `references/config.md` §九 与 `.env.example`。

---

### 4.7 运维关键坑（必记，否则重踩）

> 以下是从实战踩坑提炼的「反直觉」点，文档其它处不展开，集中放这里。**新会话 / 新前端模型只要读项目，必须过这一节**，否则会在飞书 CLI 与系列课重生成上重踩。完整命令见 `references/feishu-cli.md`。

- **飞书 wiki CLI 4 坑**（详见 `references/feishu-cli.md`）：
  1. `wiki +node-list` 返回结构是 `data.nodes`（**不是** `data.items`）——遍历用 `.get("data",{}).get("nodes",[])`。
  2. **删节点用 `--obj-type wiki`**（不是 `docx`）——`wiki +node-delete --obj-type wiki --node-token <tok>`。
  3. **容器节点用 `--obj-type docx` 充当**（飞书 wiki 无独立 folder 类型）——建系列 / 收件箱容器时传 `--obj-type docx`、`--node-type origin`。
  4. **测完务必清理测试节点**：`wiki +node-delete --obj-type wiki --yes`（带 `--yes` 免确认），别留垃圾节点。
- **系列课并发 / 重生成 3 坑**：
  1. **集级无查重 → 并发写同集会建重复节点**：多子 Agent 同时 `save_series` 写飞书会因集级没查重建重复节点；安全模式＝子 Agent 只返文本＋元数据，编排方**串行**调保存入口落盘（详见 §4.1）。
  2. **重生成总览先删旧节点**：飞书 `save_series` 是「新建」非「更新」，若不先删旧 `00_系列总览` 节点，重生成会再建第 2 个总览。
  3. **全集写完后务必调一次 `_generate_series_overview` 刷新**：否则总览停留旧状态、各集「（待总结）」标记过期。

---

## 5. AI 执行约定（防遗忘清单）

- [ ] 会话开始：先读 **RULES.md（本文件）** + `SKILL.md`，确认两条路线入口与降级逻辑。
- [ ] 收到「总结/整理」+ 素材 → 调 `skill_main` / `summarize_video`，**不要手写抓取或手写总结**。
- [ ] 触发降级（`need_continue_summary`）→ **派子 Agent** 用返回的 `prompt` + `raw_file` 做总结，再调 `save_summary_only` 存档（主会话只做编排，不直写总结，保上下文干净）。
- [ ] **存档后自检落盘**：确认**飞书**知识库「AI 总结笔记」下出现对应节点；本次带了 `obsidian` 才检查 Obsidian vault 对应文件（没带则 Obsidian 不应有新文件，是预期不是失败）；本地 `notes/` **预期为空**（有飞书即不落本地），**不要因本地为空而误判失败**；飞书 user 身份须 `lark-cli auth status` ready，否则只告警不落飞书。
- [ ] 本文件（RULES.md）变更 → 同步进 `MEMORY.md`「规则摘要」并视作平台规则。
- [ ] 遇到网络/代理问题 → 先查 `references/youtube-cdp-workflow.md`，不要绕去挖代理配置。
- [ ] YouTube 字幕抓取返回 None 且页面已加载、`captionTracks` 为空 → `videos/main` 自动走 ASR 兜底；ASR 也失败才回终态文案「【此视频暂无可用字幕（CC 与 ASR 兜底均失败），无法总结内容。】」并停止（§4.4）。
- [ ] 笔记作者栏**必须显示真实作者/UP主**；视频链路 `series.get("author","") or input_data.get("author","")` 已兜底，调用方无需手传，禁止无端输出【作者未知】（§4.5）。
- [ ] **质量闸门（可选 · 默认关）**：要更严质检时在 `.env` 设 `NOTE_QUALITY_GATE=1`（阈值 `NOTE_GATE_THRESHOLD` 默认 85）；降级无外部 AI 时闸门自动转自检段，按 6 红线自核对，无需手动开。详见 `references/config.md` §九 与 §4.6。
- [ ] **涉及架构级改动 / 新功能链路 / 跨多模块改动** → 先按 §6 grill_rules 拷问拉齐认知，再动手。

---

## 6. grill_rules（认知拉齐拷问规则）

> **规则元数据（供 AI 平台加载）**：`alwaysApply: true` ｜ `description: 认知拉齐拷问规则。涉及架构级改动或新功能链路时，必须先执行拷问流程拉齐认知，再动手编码。产出为决策清单+测试用例草稿，不写长篇设计文档。` ｜ `globs: []`
> 本规则约束「动手前先拉齐认知」。目的是防止前期认知偏差导致返工，以及防止 AI 饮鸩止渴式 hotfix 堆积技术债。**各 AI 平台须作为平台级规则加载并随本文件同步。**

### 6.1 触发条件（满足任一即触发）

| 场景 | 是否拷问 |
|------|---------|
| 架构调整 / 新增功能链路 / 引入新约定 | ✅ 必须拷问 |
| 跨多模块改动（影响 2 个以上模块） | ✅ 必须拷问 |
| 修 BUG（根因涉及多模块或需重新设计） | ✅ 必须拷问 |
| 单模块内功能开发 / 单点修 BUG | ⚠️ 看复杂度——简单直接干，复杂拷问 |
| 改文案 / 调样式 / 补测试 / 重构不改行为 | ❌ 直接干 |

**判断标准一句话**：改动会影响多个模块、引入新约定、或需要重新设计时，拷问；否则直接干。

### 6.2 红线约束（违反即错，不可绕过）

**拷问五原则**
1. **无情追问，逐个分支走**——沿着决策树的每个分支深入，一步一步解决依赖关系，直到双方对计划达成共识。
2. **每个问题给出推荐答案**——不要只问「你想怎么做」，要给出你认为的最佳方案，用户只需同意/否决/调整。
3. **每次只问一个问题**——问完等待用户反馈再继续。一次问多个会让人不知所措。
4. **能查代码就查代码**——如果某个问题可以通过探索代码库回答，直接去查，不要问用户。
5. **走完决策树才动手**——拷问没结束不准写功能代码。可以先写测试草稿（RED 状态）。

**产出物红线**——拷问结束后，必须产出以下两样东西，**不准写长篇设计文档**：

- **产出 A：决策清单（极轻量）**
  - 格式：纯 markdown，**不超过 15 行**；只记录「定了什么 + 为什么」，不展开论证。
  - 存放：`docs/decisions/DECISION-YYYYMMDD-{slug}.md`
  - 模板：
    ```markdown
    # DECISION-YYYYMMDD-{slug}
    ## 背景
    一句话说明为什么需要这个决策。
    ## 决策
    - 决策1：XXX（理由）
    - 决策2：XXX（理由）
    ## 不做什么
    - 明确排除的范围，防止 scope creep
    ```
- **产出 B：测试用例草稿**
  - 能转化为测试的约束，**必须写成 pytest 用例**（RED 状态，预期失败）。
  - 测试文件放 `tests/`，命名 `test_{feature}.py`。
  - 每个用例内加场景描述注释，说明这个测试在保护什么需求。
  - 拷问中确认的边界条件、异常场景，全部转成测试。
  - **测不了的约束**（如 Git 提交格式、文档结构约定、UI 模板选择规则）留在决策清单里，不单独写文档。

**禁止行为红线**
- ❌ **禁止拷问中途写功能代码**——认知没拉齐就动手是返工的根源。
- ❌ **禁止把拷问产出写成长文档**——超过 15 行的决策清单说明没想清楚，继续拷问。
- ❌ **禁止跳过拷问直接 hotfix**——临时 hotfix 堆多了，兼容/降级逻辑会彻底救不回来。
- ❌ **禁止拷问完不产出测试草稿就动手**——没有 RED 测试锁定的需求等于没拉齐。

### 6.3 执行流程

```
1. 识别任务类型 → 判断是否触发拷问
2. 触发 → 开始拷问（逐个问题，给推荐答案，能查代码就查）
3. 拷问完成 → 产出 A（决策清单 ≤15行）+ 产出 B（测试草稿 RED）
4. 用户确认产出 → 进入 TDD：RED → GREEN → REFACTOR
5. 测试转绿 → 功能交付
```

### 6.4 与其他规则的关系
- 本规则管「动手前」，`references/testing_rules.md` 管「动手时」的 TDD 流程。
- 本规则产出的测试草稿，遵循 `references/testing_rules.md` 的 fixture 与断言规范。
- 本规则产出的决策清单，是对 `docs/bug_library.md`（未来改为 issue 管理）的补充——决策记录「为什么这么定」，issue 记录「出了什么问题」。

### 6.5 文档同步纪律（防漂移）

> 历史教训：模板数曾同时写 7/8/9，scys 窗口曾写 7/35/182/548，BILI 首跑窗口曾写 7/30——都是"易变事实在多处文档各自写死"导致的漂移。本小节是唯一硬纪律。

- **易变数字一律指向代码真源，不硬编码**：模板种类数、各抓取窗口天数、脚本名、环境变量默认值等"会随代码变"的事实，文档只写"以 `<文件>:<符号>` 为准（当前由代码动态决定）"，禁止在多处文档各自写死同一数字。
- **改代码后必须同步改文档**：
  - 新增笔记模板 → 同步改 `prompts/templates.py`(NOTE_TEMPLATES) + `prompts/classify.py`(分类优先级) + 本文档模板清单引用。
  - 改 CLI 参数/命令 → 同步改 AGENTS.md 用法段 + README 对应段落。
  - 改窗口/阈值常量 → 同步改 references/config.md 变量表 + AGENTS.md 调参行。
- **废弃方案/过期文档即时归档**：已否决的方案（如 Chrome junction 直连、自动定时调度）或过期文档（pre-监控时期的 PRD、已 SUPERSEDED 的 lessons）移入 `_archive/`，原处留一行指针，禁止在现役文档树里继续当"现行做法"描述。
