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

- **一句话定位**：把「文章/视频链接、原文、字幕」自动转成结构化/要点笔记，并归档到本地 `notes/` / Obsidian / 飞书。
- **触发场景（同时满足）**：① 用户说「总结/提炼/整理/归档/保存笔记」类词；② 给了素材（文章链接、原文粘贴、视频/字幕）。
- **不触发**：只聊概念没给素材、或纯答疑。
- **默认行为**：不在对话框输出完整笔记正文，只给 1~3 句核心结论 + 成品路径。

---

## 3. 两条业务路线（怎么跑）

> 两条路线都复用 `prompts/`（笔记模板）与 `articles` 的保存能力（`OutputManager`），**入口函数即真相**，勿手写抓取/总结。

### 3.0 多端双写契约（强制 · 解决「新会话不自动存飞书」）

> **这是用户反复强调的红线**：任何总结成品，**必须**同时落到「本地 `notes/` + Obsidian + 飞书」三端（已配置的输出一个不漏）。新会话、新 AI 都必须默认双写，不得只存本地或只存 Obsidian。

- **双写是代码保证，不是靠 AI 记性**：
  - 文章路线：`articles.main.save_summarized_article` → `OutputManager.save_all()`，**遍历 `get_available_outputs()` 返回的全部输出**逐一写入（本地 / Obsidian / 飞书）。
  - 视频系列课：`videos.main._save_series_note` 与 `_generate_series_overview` 同样**遍历所有可用输出**，对每个输出调 `out.save_series(...)`。
- **飞书已配好，新会话直接生效**（无需再配）：`.env` 里 `FEISHU_WIKI_SPACE` + `FEISHU_WIKI_PARENT_NODE`（指向「AI 总结笔记」节点）已就绪；user 身份已授权。只要 `lark-cli auth status` 显示 user ready，跑总结就会自动落飞书。
- **飞书系列课容器逻辑**：飞书下会**先建一个以系列名命名的 wiki 节点**（如「千刀千法」），各集笔记与 `00_系列总览.md` 都挂在这个容器节点下——与 Obsidian 的 `千刀千法/` 文件夹一一对齐。容器建重已做查重 + 进程内缓存，不会重复建。
- **AI 执行后必须自检双写结果**（防止「以为存了其实没落」）：
  - [ ] 本地 `notes/` 有文件？
  - [ ] Obsidian vault 对应路径有文件？
  - [ ] 飞书知识库「AI 总结笔记」下出现对应节点（系列课则在「系列名」容器内）？
  - 任一缺失 → 检查该输出 `is_available()`（路径/授权），缺失是配置问题不是代码问题，补配置后重跑 `save_*` 即可，不要改总结内容。
- **单端失败不影响其他端**：每个输出写入都是独立 try（非致命），某端暂时不可用（如飞书 CLI 掉线）只告警、不中断其他端；恢复后重跑保存即可补齐。

### 路线 A：文章总结（`articles/`）
- **入口**：`articles/run.py --url/--content/--batch`；或直接 `from articles import skill_main; skill_main({...})`。
- **流程**：抓取 `articles.fetch.fetch_web_content` → 去重 `articles.dedup` → 自动分类 `prompts.classify.classify_note_type` → AI 总结 `articles.main.summarize_content` → 多端保存 `articles.manager.OutputManager.save_all`（**遍历所有可用输出：本地 `notes/` + Obsidian + 飞书，详见 §3.0 双写契约**）。
- **降级**：无外部 AI 时 `skill_main` 返回 `need_continue_summary` + `prompt` + 原文，交外层（WorkBuddy）总结后调 `skill_continue_summary` / `save_summary_only` 存档。
- **细节**：见 `SKILL.md`「执行流程」与 `README.md`。

### 路线 B：视频总结（`videos/`）
- **入口**：`videos/run.py --url/--file/--content`；或 `from videos import summarize_video; summarize_video({...})`。
- **输入优先级**：YouTube/Bilibili 单视频 → 自动抓 CC 字幕；playlist/合集/系列课 → 逐条总结 + **必生成系列总览大纲**；本地文件 → ASR；直接给字幕文本 → 直接总结。
- **流程**：获取字幕 `videos.fetch.fetch_transcript`（**自动 API→CDP 回退**）→ 分块两段式 `videos.main` + `shared.chunking`（防超长爆上下文）→ AI 总结 → 复用 `articles` 保存。
- **YouTube 在本机无出口时**：走 CDP 全自动（驱动带代理插件的 Chrome 副本）→ 详见 **`references/youtube-cdp-workflow.md`**（换会话照此执行，AI 只需跑 `videos/run.py --url <youtube>`）。
- **公共机制**：笔记类型 `prompts/templates.py` 的 `NOTE_TEMPLATES`（structured / key_points / case / opinion）；新增类型只改这一处。AI 调用优先级：外部 Provider → WorkBuddy 内置 AI → 降级。
- **系列课必生成总览大纲（规则，非开关）**：B站 `ugc_season` 系列课 / 多P 视频处理完成后，`videos.main._generate_series_overview` 自动扫描系列文件夹，抽取每集标题 + `一句话核心结论`，生成 `00_系列总览.md`（**本地 + Obsidian + 飞书 三端双写**，详见 §3.0 双写契约），含「各集导航」表（集号 / 标题 / 一句话核心结论 / 笔记相对链接）。无需 `--overview` 开关即生效；该总览在每集总结后刷新，待总结的 raw 集在表中标「（待总结）」。飞书下总览与各集都挂在「系列名」容器节点内，与 Obsidian 的 `系列名/` 文件夹一一对齐。

---

## 4. 项目规则（强制）

### 4.1 性能与实现规范
- **同步 → 异步**：网络抓取、AI 调用、磁盘 I/O 等阻塞调用，优先异步化（`asyncio` / `run_in_executor`），勿阻塞主流程。
- **循环 → 一次性查询后筛选**：循环内逐条查询/抓取/请求，优先改为「一次批量查询/抓取，再在内存里筛选」，**避免 N 次往返**。
- **串行 → 并行**：多个独立任务（多视频 / 多文件 / 多链接）评估并行（`asyncio.gather` / 线程池），注意限流与去重，避免无意义串行等待。
- **复用入口，不重复造轮子**：统一走 `fetch_transcript` / `skill_main` / `summarize_video` / `OutputManager` 等既有入口，禁止在多处复制抓取/保存逻辑。
- **长内容必走两段式分块**：超过单模型上下文的内容，先经 `shared.chunking` 分块再总结，禁止整篇直接喂模型。

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

---

## 5. AI 执行约定（防遗忘清单）

- [ ] 会话开始：先读 **RULES.md（本文件）** + `SKILL.md`，确认两条路线入口与降级逻辑。
- [ ] 收到「总结/整理」+ 素材 → 调 `skill_main` / `summarize_video`，**不要手写抓取或手写总结**。
- [ ] 触发降级（`need_continue_summary`）→ 用返回的 `prompt` 做总结，再调 `skill_continue_summary` / `save_summary_only` 存档。
- [ ] **存档后自检双写（§3.0 红线）**：确认 本地 `notes/` + Obsidian + 飞书 三端都落盘；飞书 user 身份须 `lark-cli auth status` ready，否则只告警不落飞书。
- [ ] 本文件（RULES.md）变更 → 同步进 `MEMORY.md`「规则摘要」并视作平台规则。
- [ ] 遇到网络/代理问题 → 先查 `references/youtube-cdp-workflow.md`，不要绕去挖代理配置。
- [ ] YouTube 字幕抓取返回 None 且页面已加载、`captionTracks` 为空 → **原样回「【此视频暂无 CC 字幕，无法为你抓取字幕总结内容。】」并停止**，不补 ASR、不改动代码（§4.4）。
- [ ] 笔记作者栏**必须显示真实作者/UP主**；视频链路 `series.get("author","") or input_data.get("author","")` 已兜底，调用方无需手传，禁止无端输出【作者未知】（§4.5）。
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
