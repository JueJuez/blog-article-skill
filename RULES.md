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
| `references/` | 专项详细文档（`config.md` 配置、`youtube-cdp-workflow.md` 抓取流程、`PRD.md` 需求、`testing_rules.md` TDD 流程） | ✅ 放深入细节 |
| `docs/decisions/` | grill_rules 的「产出 A：决策清单」存放地（`DECISION-YYYYMMDD-{slug}.md`，≤15 行） | ✅ 极轻量 |
| `articles/` `videos/` `prompts/` `shared/` | 实现细节的唯一真相 | ✅ 代码即文档 |
| `.workbuddy/memory/MEMORY.md` | 长期要点 + 「规则摘要」（会话开始注入） | ❌ 只摘要点 |
| `.workbuddy/memory/YYYY-MM-DD.md` | 每日工作日志（做了什么、踩了什么坑） | 过程记录 |
| `notes/` | 产出与原始内容（gitignore，不进仓库） | — |

---

## 2. 项目定位与适用场景

- **一句话定位**：把「文章/视频链接、原文、字幕」自动转成结构化/要点笔记，并归档到 Obsidian / 飞书（本地 `notes/` 仅在两者都未配置时兜底，详见 §3.0）。
- **触发场景（同时满足）**：① 用户说「总结/提炼/整理/归档/保存笔记」类词；② 给了素材（文章链接、原文粘贴、视频/字幕）。
- **不触发**：只聊概念没给素材、或纯答疑。
- **默认行为**：不在对话框输出完整笔记正文，只给 1~3 句核心结论 + 成品路径。

---

## 3. 两条业务路线（怎么跑）

> 两条路线都复用 `prompts/`（笔记模板）与 `articles` 的保存能力（`OutputManager`），**入口函数即真相**，勿手写抓取/总结。

### 3.0 多端双写契约（强制 · 解决「新会话不自动存飞书」）

> **这是用户反复强调的红线**：总结成品**必须落 Obsidian + 飞书双端**（已配置的输出一个不漏）。本地 `notes/` **仅在 Obsidian 与飞书都未配置时才作为兜底写入**——用户原话"有 obsidian 和飞书，本地就不需要写了"（代码 `videos.main._local_write_enabled()` 已强制：两者任一可用即不写本地，**AI 不要手动写 `notes/`**）。新会话、新 AI 都必须默认双写双云，不得只存本地或漏飞书。

- **双写是代码保证，不是靠 AI 记性**：
  - 文章路线：`articles.main.save_summarized_article` → `OutputManager.save_all()`，**遍历 `get_available_outputs()` 返回的全部输出**逐一写入（本地 / Obsidian / 飞书）。
  - 视频系列课：`videos.main._save_series_note` 与 `_generate_series_overview` 同样**遍历所有可用输出**，对每个输出调 `out.save_series(...)`。
- **飞书已配好，新会话直接生效**（无需再配）：`.env` 里 `FEISHU_WIKI_SPACE` + `FEISHU_WIKI_PARENT_NODE`（指向「AI 总结笔记」节点）已就绪；user 身份已授权。只要 `lark-cli auth status` 显示 user ready，跑总结就会自动落飞书。
- **飞书系列课容器逻辑**：飞书下会**先建一个以系列名命名的 wiki 节点**（如「千刀千法」），各集笔记与 `00_系列总览.md` 都挂在这个容器节点下——与 Obsidian 的 `千刀千法/` 文件夹一一对齐。容器建重已做查重 + 进程内缓存，不会重复建。
- **AI 执行后必须自检双写结果**（防止「以为存了其实没落」）：
  - [ ] Obsidian vault 对应路径有文件？
  - [ ] 飞书知识库「AI 总结笔记」下出现对应节点（系列课则在「系列名」容器内）？
  - [ ] 本地 `notes/`：**预期为空**（Obsidian+飞书都可用时 `_local_write_enabled()` 返回 False 不落本地）；仅当两者都未配置时才应有文件。
  - 任一云端缺失 → 检查该输出 `is_available()`（路径/授权），缺失是配置问题不是代码问题，补配置后重跑 `save_*` 即可；**不要手动写 `notes/`、也不要改总结内容**。
- **单端失败不影响其他端**：每个输出写入都是独立 try（非致命），某端暂时不可用（如飞书 CLI 掉线）只告警、不中断其他端；恢复后重跑保存即可补齐。

### 3.1 待归类收件箱约定（强制 · 新笔记落点）

> **用户偏好**：新总结先统一进「待归类」，用户后续手动拖到分类，避免长列表一眼看不到、难管理。Obsidian 与飞书两侧结构对称。

- **单篇新笔记（文件名不含 `/`）默认落「待归类」**：
  - **Obsidian**：`articles/obsidian.py` 把无子目录的 filename 落 `<vault>/待归类/<filename>`（`OBSIDIAN_INBOX = "待归类"`），`get_output_path` 同步（去重检测不错位）。
  - **飞书**：`articles/feishu.py` 的 `ensure_inbox_node()` 在父节点下确保存在「待归类」容器节点；单篇 `save` / `save_async` 无显式 `parent_token` 时默认落此节点。
  - **系列课例外**：自带 `系列名/` 子目录（Obsidian）或走 `save_series` 显式传父节点（飞书），**不进待归类**，保持系列容器结构。
- **Obsidian 分类文件夹 ↔ 飞书分类节点一一对应**（用户在「AI 总结笔记」下手动维护）：`待归类` + `01_独立开发` / `02_流量变现` / `03_AI提效` / `04_流量获取` / `05_投资交易` / `06_认知成长` / `07_内容创作`，外加 `千刀千法` 系列容器。
- **历史补平（保持双端对称）**：当 Obsidian 有而飞书缺的笔记，按其在 Obsidian 所属分类**直接补到飞书对应分类节点（不是待归类，因为已分类）**；补前先查重避免重复节点，**串行**保存避免飞书并发重复。

### 路线 A：文章总结（`articles/`）
- **入口**：`articles/run.py --url/--content/--batch`；或直接 `from articles import skill_main; skill_main({...})`。
- **流程**：抓取 `articles.fetch.fetch_web_content` → 去重 `articles.dedup` → 自动分类 `prompts.classify.classify_note_type` → AI 总结 `articles.main.summarize_content` → 多端保存 `articles.manager.OutputManager.save_all`（**遍历所有可用输出（Obsidian + 飞书；本地 `notes/` 仅在两者都未配时兜底），详见 §3.0 双写契约**）。
- **降级**：无外部 AI 时 `skill_main` 返回 `need_continue_summary` + `prompt` + 原文，交外层（WorkBuddy）总结后调 `skill_continue_summary` / `save_summary_only` 存档。
- **细节**：见 `SKILL.md`「执行流程」与 `README.md`。

### 路线 B：视频总结（`videos/`）
- **入口**：`videos/run.py --url/--file/--content`；或 `from videos import summarize_video; summarize_video({...})`。
- **输入优先级**：YouTube/Bilibili 单视频 → 自动抓 CC 字幕；playlist/合集/系列课 → 逐条总结 + **必生成系列总览大纲**；本地文件 → ASR；直接给字幕文本 → 直接总结。
- **流程**：获取字幕 `videos.fetch.fetch_transcript`（**自动 API→CDP 回退**）→ 分块两段式 `videos.main` + `shared.chunking`（防超长爆上下文）→ AI 总结 → 复用 `articles` 保存。
- **YouTube 在本机无出口时**：走 CDP 全自动（驱动带代理插件的 Chrome 副本）→ 详见 **`references/youtube-cdp-workflow.md`**（换会话照此执行，AI 只需跑 `videos/run.py --url <youtube>`）。
- **公共机制**：笔记类型 `prompts/templates.py` 的 `NOTE_TEMPLATES`（7 种：structured 结构化复盘 / key_points 要点提炼 / case 案例拆解 / opinion 观点卡 / interview 访谈 / roundup 盘点横评 / reading 读书书摘）；新增类型只改这一处。分类详见 `prompts/classify.py`，优先级：教学超信号(structured) > 访谈(interview) > 要点(key_points) > 盘点(roundup) > 读书(reading) > 观点(opinion) > 案例(case) > structured 兜底。关键分类规则：① 教学/教程类视频（手把手/保姆/实操/从零/教程/课程）经「教学超信号」优先判 `structured`；② 访谈（访谈/对谈/专访/Q&A）已从要点词移出独立成类；③ **内容级访谈兜底**：标题无 cue 时（如「95后女老板Judy」式创业对谈），用正文前 2500 字做「主持人向特定嘉宾的人生/状态探针（你当时/你后来/你是怎么/你创业…）且观众独白口吻不占主导」判别，避免被误判为口播要点；④ **读书** 额外认书名号《》强信号、**盘点** 额外认「榜/榜单/红黑榜/种草/闭眼入/抄作业/排行」；⑤ **`视频` 已从要点词移除**——URL 本身即说明载体，不该当类型信号（否则字幕里一句「这个视频」就把盘点/访谈抢成口播要点）。**思维模型透镜（提质·按需）**：全部 7 模板共用 `UNIVERSAL_RULES` 第九节（structured 内联第十四节），6 模型按序 LIST（第一性原理→5-Why冰山→二阶思维→脉络还原→奥卡姆剃刀→类比迁移）逐条过、不适用跳过，直接服务「质量高、上下文清晰」且不破去水分红线。AI 调用优先级：外部 Provider → WorkBuddy 内置 AI → 降级。
- **系列课必生成总览大纲（规则，非开关）**：B站 `ugc_season` 系列课 / 多P 视频处理完成后，`videos.main._generate_series_overview` 自动扫描系列文件夹，抽取每集标题 + `一句话核心结论`，并用 AI 生成「学习路径」段（建议顺序 + 先修说明），生成 `00_系列总览.md`（**Obsidian + 飞书 双写，本地仅兜底**，详见 §3.0 双写契约），含「各集导航」表（集号 / 标题 / 一句话核心结论 / 笔记相对链接）与「学习路径」段。无需 `--overview` 开关即生效；该总览在每集总结后刷新，待总结的 raw 集在表中标「（待总结）」。飞书下总览与各集都挂在「系列名」容器节点内，与 Obsidian 的 `系列名/` 文件夹一一对齐。

---

## 4. 项目规则（强制）

### 4.1 性能与实现规范
- **同步 → 异步**：网络抓取、AI 调用、磁盘 I/O 等阻塞调用，优先异步化（`asyncio` / `run_in_executor`），勿阻塞主流程。
- **循环 → 一次性查询后筛选**：循环内逐条查询/抓取/请求，优先改为「一次批量查询/抓取，再在内存里筛选」，**避免 N 次往返**。
- **串行 → 并行**：多个独立任务（多视频 / 多文件 / 多链接）评估并行（`asyncio.gather` / 线程池），注意限流与去重，避免无意义串行等待。
- **复用入口，不重复造轮子**：统一走 `fetch_transcript` / `skill_main` / `summarize_video` / `OutputManager` 等既有入口，禁止在多处复制抓取/保存逻辑。
- **长内容必走两段式分块**：超过单模型上下文的内容，先经 `shared.chunking` 分块再总结，禁止整篇直接喂模型。
- **大批量 → 子 Agent 隔离主线程（防上下文胀爆）**：当待处理内容达到批量阈值（如 >3 条笔记/视频，或单批原文大到会撑爆主会话上下文）时，**必须**用 Agent 工具派发子 Agent 并行处理，勿把全部原文/中间稿堆在主线程。注意：① 子 Agent 上下文是空白的，派发 prompt 必须**自包含**（嵌入双写契约 Obsidian+飞书不写本地、入口函数 `videos/run.py --url` 或 `skill_main`、`note_type`、YouTube/无字幕规则按需）；② **飞书并发重复坑**：多子 Agent 同时 `save_series` 写飞书会因集级无查重建重复节点（见 §3.0 系列课运维坑）；**安全模式**＝子 Agent 只**返回成品 Markdown 文本＋元数据**（标题/作者/url/tags/note_type），由编排方**串行**调保存入口（`_save_series_note` / `save_all`）落盘，绝不让多子 Agent 并发各自调 `save_series`。

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
- **「无 CC 字幕」= 终态，不是失败**：当 `fetch_transcript` 返回 `None`，且 CDP 已成功打开页面（能拿到标题、`ytInitialPlayerResponse.captions` 为空 / 播放器 `movie_player` 字幕 tracklist 为空），即判定为**视频本身在 YouTube 上没有任何 CC / 自动字幕轨道**。此时 AI **必须直接回这句话并停止**：
  > **【此视频暂无 CC 字幕，无法为你抓取字幕总结内容。】**
  - **禁止**自作主张走 ASR 语音转写、或改动代码「优化/开发」去兜底——除非用户**明确**要求开发。本环境 ASR 也不可行（无 ffmpeg / faster-whisper / yt-dlp，HuggingFace 不可达，且 Python 无 YouTube 出口下不了音频）。
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
- **C. 强制证据红线（思维模型透镜）**：全部 7 模板共用 `UNIVERSAL_RULES` 第九节（structured 内联第十四节），6 模型按序 LIST（第一性原理→5-Why冰山→二阶思维→脉络还原→奥卡姆剃刀→类比迁移）逐条过、不适用跳过；**每条适用模型须给「洞察（不同视角）+ 原文证据句（「」括起原话，禁改写）」**，禁只写"用了X 模型"；全不适用须逐条列 6 模型理由。与去水分红线兼容，不硬凑固定章节。
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
- [ ] 触发降级（`need_continue_summary`）→ 用返回的 `prompt` 做总结，再调 `skill_continue_summary` / `save_summary_only` 存档。
- [ ] **存档后自检双写（§3.0 红线）**：确认 **Obsidian + 飞书双端**都落盘；本地 `notes/` **预期为空**（Obsidian+飞书都可用时 `_local_write_enabled()` 返回 False 不落本地），**不要因本地为空而误判失败**；飞书 user 身份须 `lark-cli auth status` ready，否则只告警不落飞书。
- [ ] 本文件（RULES.md）变更 → 同步进 `MEMORY.md`「规则摘要」并视作平台规则。
- [ ] 遇到网络/代理问题 → 先查 `references/youtube-cdp-workflow.md`，不要绕去挖代理配置。
- [ ] YouTube 字幕抓取返回 None 且页面已加载、`captionTracks` 为空 → **原样回「【此视频暂无 CC 字幕，无法为你抓取字幕总结内容。】」并停止**，不补 ASR、不改动代码（§4.4）。
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
