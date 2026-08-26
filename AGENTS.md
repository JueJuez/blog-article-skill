# blog-article-skill — 项目入口（平台无关 · 真源）

> **本文件是跨平台「真源」引导入口。** 无论你用 WorkBuddy / Cursor / Claude / Codex / Copilot / 裸 API，
> 只要把本文件（或它指向的 `RULES.md`）加载进模型上下文，下面这套**流程、功能、配置**就全部接上。
> WorkBuddy 专属的 `SKILL.md` 只是「薄触发层」，所有真规则以本文件 + `RULES.md` 为准，避免平台切换时流程丢失。

## 一句话定位
把「文章 / 视频链接、原文、字幕、订阅的 B站UP主 / 公众号」自动转成结构化笔记，归档到 **飞书（默认）；Obsidian 仅在明确要求时追加写入**。

---

## 三个核心能力

### 能力 1 · 一次性总结（文章 / 视频）
用户给**文章链接、原文、或视频（YouTube / Bilibili）链接 / 字幕** → 总结成笔记。
- **入口（任选）**
  - **统一入口（推荐）**：`python articles/run.py "https://..."` 或 `from articles import skill_main; skill_main({"content": url_or_text})` —— `fetch_web_content` 检测到 `scys.com` 链接自动分流 CDP 登录态抓取，普通博客走 requests。**用户给单篇或多篇混合链接（普通博客 + scys）都逐条自动分流，前端无感。**（回归测试 `tests/test_scys_routing.py`）
  - **需登录态文章的底层路径**（诊断 / 显式抓取时用）：按用户主 Chrome 状态自动判别——
  - **路径 A · CDP 接管活 Chrome（优先）**：Chrome 恰好带 debug 端口运行 → Playwright 通过 CDP 接管，登录态由浏览器携带。入口：`python scripts/login_cdp_fetch.py "<URL>" [out.md]`。**scys（生财有术）付费文章专用照做 SOP 见 `references/scys-fetch-sop.md`（含前提 / 判别墙·真文 / 故障，新会话直接照做）。**
  - **路径 B · profile_clone_fetch（Chrome 151+ 默认回退）**：没有 debug 端口 → `login_cdp_fetch.py` 自动回退到 `profile_clone_fetch.py`（复制真实 profile 到临时目录，用临时 dir 启 headless Chrome，非默认 dir → Chrome 151+ 放行）。入口：`python scripts/profile_clone_fetch.py "<URL>" [out.md]`。代价：需短暂关闭 Chrome（脚本自动 kill 释放 cookie 锁），复制 ~16GB profile 需 ~1 分钟，抓完重开 Chrome。`python scripts/login_cdp_fetch.py "<URL>"` 也会自动回退。
  - ⚠️ **Chrome 151+ 已废弃 junction 方案**（2026-08-24 实测）：旧方案用 junction（`DebugUDD` → `User Data`）绕过远程调试限制，但 Chrome 151 能检测 junction 指向同一物理目录，触发安全清理：清空 `extensions.settings` → `extension_garbage_collector` 删扩展文件（实测 22 个扩展被删）→ 清 Google 账号关联。**新方案不再用 junction / 不再改 Chrome 快捷方式 / 不再需要 `--remote-debugging-port`。** 详见 `references/login-required-cdp-workflow.md` §1.1~§1.2。
  - **scys 按领域批量抓取**：`python scripts/scys_batch_fetch.py --project <领域>`（领域 / menuId / 时间窗在 `scripts/scys_projects.json` 配置，换领域每半年重抓只改 JSON 不改代码）。**触发词：用户说「补齐scys / 补齐生财有术」即自动启动全流程（默认=精华+高互动非精华，2026-08-21 起），后缀自然语言改参数（领域/时间/仅精华），语义见 `references/scys-fetch-sop.md` §9。**
  - 视频：`python videos/run.py --url "https://..."` 或 `from videos import summarize_video; summarize_video({"url": url})`（含 ASR 兜底）
- 自动按内容类型选模板（`structured` / `key_points` / `interview` / `roundup` / `reading` / `case` / `opinion`）；**默认写飞书**，用户说「写到 obsidian / 双写」时才追加 Obsidian（传 `obsidian=True` 或 `--obsidian`，详见 `RULES.md` §3.0）。
- **降级**：无外部 AI 时 `skill_main` 返回 `need_continue_summary` + 原文 + 模板 prompt；外层模型总结后调 `save_summary_only` 存档。

### 能力 2 · 订阅监控（关注 B站UP主 / 公众号 / scys 领域）
让用户「持续订阅某账号，自动发现新内容并总结」。
- **配置订阅**：编辑 `monitors/subscriptions.json`（参考 `monitors/subscriptions.example.json`）
  - B站：`{"uid": "数字UP主ID"}`
  - 公众号：`{"mp_id": "..."}` 或 `{"share_url": "公众号分享链接"}`
  - scys：`{"project": "领域名"}`（领域→menuId 映射在 `scripts/scys_projects.json`，当前=自媒体/出海/AI产品开发/小程序）
  - 用户口头说「关注 / 订阅 / 监控 XXX」时：
    - **B站UP主**：**走机械命令** `python monitors/run.py --subscribe --uid <id> --name <名> --category <类> [--sub-all | --sub-window <天>]`（命令会先查重，已在名单则回「已在监控名单内」且不添加，不手搓 JSON）。
    - **公众号 / scys 领域**：编辑 `monitors/subscriptions.json`，格式参考 `monitors/subscriptions.example.json`（公众号：`{"mp_id":"..."}` 或 `{"share_url":"..."}`；scys：`{"project":"领域名"}`）。
    - 不要手搓抓取代码。
- **运行**
  - 首跑（回填最近 30 天）：`python monitors/run.py --mode first --apply`
  - 每日增量：`python monitors/run.py --mode auto --apply`（**不再挂自动调度**；用户说「跑一次 / 跑一下」等关键词即触发，详见 `RULES.md` §3C）——**含 scys 四领域新帖增量**（2026-08-20 接入：`--apply` 收尾逐领域子进程跑 `scripts/scys_batch_fetch.py`；2026-08-21 起 35 天窗口+精华直通+非精华互动门槛（锚≥30，或 赞≥80且锚≥10 防官方帖污染）+done 去重+`.lock` 互斥，CDP 不可用则自动回退 profile_clone 不影响其他源）
  - **新会话执行步骤（照做即一帆风顺）**：
    1. 直接运行 `python monitors/run.py --mode auto --apply`。
    2. 公众号 token 失效 → 自动弹二维码（`RELOGIN_QR:` 路径），**本机会话扫码后续期，本次运行即继续抓取公众号**（刷新 token 后重试整轮）；headless/无人看码则本次跳过公众号、B站照跑不受影响。
    3. 发现 → 抓正文 → 进 `pending_summaries.json` 队列（FORCE_AGENT_MODE 下不自动总结）；系列课降级进 `pending_series.json` 队列；scys 新帖进 `notes/_scraped/scys/pending_summaries.json` 队列。
    4. 运行结束后，本会话（执行模型）**必须**在本次会议内闭环两类待总结队列（全自动，无需用户手动命令）：
       - **派单前先跑 `python scripts/filter_pending.py`**（机械清洗两队列：URL 命中 dedup 索引的已总结条目自动出队、scys 队列清 `summarized:true`——多 Agent 接力时已总结内容不再浪费 AI token、不重复落飞书）。
       - **单篇**：读 `pending_summaries.json`，派**子 Agent** 按模板总结并 `save_summary_only` 落盘（默认飞书，带 `--obsidian` 时追加 Obsidian，详见 `RULES.md` §3.0）。
       - **scys**：读 `notes/_scraped/scys/pending_summaries.json`，派**子 Agent** 按 `references/scys-fetch-sop.md` §9 语义总结并落飞书（folder=生财有术/<领域>）-> 出队。⚠️ 子 Agent **必须走正规入口**：先调 `get_note_prompt.py` 获取分类器自动选定的模板 prompt（含 `QUALITY_GATE_SELFCHECK` 质量自检闸门），按该模板总结。**不要全部用 structured 模板**（2026-08-22 分类修复，详见 `docs/decisions/DECISION-20260821-scys-classification-fix.md`）。
       - **系列课**：读 `pending_series.json`，对每个系列按 `notes/<系列名>/*_raw.md` 分片派**子 Agent** 总结成 `.body.md`，再跑 `python monitors/apply_pending_series.py` 落地（run.py 末尾已自动触发一次落地，body 存在时直接落；此处是为「刚总结出的新 body」补一遍落地）。系列课**只落飞书**，除非用户明确要双写/只写 Obsidian。
       - ⚠️ 系列课增量语义：每日重跑时，`videos.main` 已按 `monitors/series_state.json` 去重，**只把未总结的集**写入 raw 并排队；UP 更新后自动只抓新增集，已总结的旧集不会重复总结/落盘。
       - ⚠️ 系列课落盘结构（2026-08-23 修复）：系列容器挂 `【监控】/<平台>/<UP>/<系列名>/` 下（不是根），由统一路由器 `shared/routing.py: resolve_folder` 算路径。`apply_pending_series.py` 传给 `_save_series_note` 的 `folder` **只到账号层**（`rsplit('/',1)[0]`），系列容器由 `ensure_series_node` 单建——否则系列名被建两次造成嵌套。`_read_series_from_feishu` 的 `parent_token` 已是容器 token 时直接用，不再内部 `ensure_series_node`。
    5. 末尾看健康度行（视频/动态/文章/跳过/限流待重试/错误）确认是否异常。
    - 内置重试（无需手动）：token 失效弹码等扫码(≤180s) / 401 瞬错 ×3 / 代理空轮退避重试 / 正文限流进 `pending_refetch` 下次重抓。
- **抓取规则**：按时间窗口（首跑 30 天 / 每日 1 天，断跑自动拉长封顶 30 天）+ 无干货动态屏蔽 + 短动态轻量化 + 新鲜度标签。细节见 `monitors/README.md`。
- **历史回溯（续批）**：把某公众号 N 年内历史文章分批抓全（例：哥飞可到 2025 年中、生财有术代理深度仅 2026-06，更早文章代理侧不可达、代码无解）。入口 `python monitors/run.py --backfill --names <逗号名> --since <YYYY-MM-DD> --batch 15`（入队 + 跑一批）；`--drain` 从 `monitors/backfill_targets.json` 取第一个未完成 job 自动续批（适合 recurring 自动化）。范围门禁只动目标号；游标复用 `state.json` 的 `seen` + `state["backfill"][name]`，不重置即可续批。完整机制见 `monitors/README.md`「公众号历史回溯（续批）」+ `references/config.md` backfill 段。
- B站需要登录态：`BILI_COOKIE` 环境变量（动态接口硬性要求）。

### 能力 3 · 用户侧怎么用（给链接 / 怎么关注）
- 给链接 → 走**能力 1**。
- 说「关注 / 订阅 / 监控 XXX」 → 走**能力 2**（改 `subscriptions.json` + 跑一次首跑）。
- 不确定走哪条 → 先读本文件 + `RULES.md`，不要凭空造流程。

---

## 配置（`.env`）
复制 `.env.example` → `.env`，至少关注：
- `FORCE_AGENT_MODE=1`（默认）：不调用外部 AI，总结一律由执行模型（主/子 Agent）完成；旧 `AI_PROVIDER` 配置已废弃。
- `OBSIDIAN_VAULT_PATH` —— Obsidian 库路径（按需端；仅 `obsidian=True`/`OBSIDIAN_WRITE=1` 时写入）
- `FEISHU_WIKI_SPACE` + `FEISHU_WIKI_PARENT_NODE` —— 飞书知识库（默认落盘端）
- `BILI_COOKIE` —— B站登录态 Cookie（订阅监控动态接口必需）
- 监控可调参：`BILI_GAP`(30) / `BILI_FIRST_WINDOW_DAYS`(7) / `BILI_DAILY_WINDOW_DAYS`(1) / `BILI_PAGE_SIZE`(50) / `STATE_KEEP`(1000) / `BILI_SHORT_DYNAMIC_MAX`(80)
- 完整变量见 `references/config.md`

## 红线（必须遵守）
- **输出默认飞书、Obsidian 按需（强制 · 2026-08-08）**：写两遍浪费，**默认只落飞书**；Obsidian 仅在用户明确要求（提前说「写到 obsidian / 双写」）时才写。由 `OutputManager` 代码门禁保证（没显式开启就不写 Obsidian），不靠 AI 记性。`.env` 设 `OBSIDIAN_WRITE=1` 可一键回退双写。本地 `notes/` 仅在飞书不可用且未请求 Obsidian 时兜底。禁止 AI 手动写 `notes/`、禁止只存本地漏飞书。
- **复用入口，不手写抓取 / 总结**：一律走 `skill_main` / `summarize_video` / `fetch_transcript` / `monitors/run.py`，
  不要临时写 `_xxx.py` 脚本、不要手搓 URL、不要 diagnose 平台私有接口。
- **无字幕自动走 ASR 兜底（2026-08-06 授权）**：`fetch_transcript` 返回 None（真无 CC 字幕）时，`videos.main` 自动调 `videos.asr` 下载音频 + 本地 Whisper 转写；成功则继续总结并落盘（默认飞书，带 `obsidian` 时双写），仅 ASR 也失败才回「无可用字幕」文案并停止。环境坑由 `asr.py` 自动处理，勿手敲 export / 勿额外开发兜底。

---

## 各平台如何加载本文件
- **WorkBuddy**：激活 `blog-article-skill` skill（`SKILL.md` 触发）；也可作为项目级 rules 注入。
- **Cursor**：`.cursorrules` 或 `.cursor/rules/*.mdc`；也认 `AGENTS.md`。
- **Trae**：原生认 `AGENTS.md`（需在 Settings → Import Settings 开启「Include AGENTS.md in the context」）；也支持 `.trae/rules/*.md` 项目规则（放一份 `alwaysApply: true` 的薄指针到 `AGENTS.md`+`RULES.md`，确保即使没开开关也必加载）。
- **Claude Code / Desktop**：`CLAUDE.md`（根目录；可 `cp AGENTS.md CLAUDE.md` 或软链）。
- **Codex / OpenAI**：`AGENTS.md`（本文件即）。
- **GitHub Copilot**：`.github/copilot-instructions.md`。
- **裸 API / 其他**：把本文件全文作为 system prompt 前置。

> ⚠️ 为避免规则漂移：**各平台入口文件应引用或复制本 `AGENTS.md`，真规则只维护一处**（本文件 + `RULES.md`）。

## 自举指针（不确定时读这些，按序）
1. `RULES.md` —— 规则唯一来源（地图 + 强制规则，最权威）
2. `SKILL.md` —— WorkBuddy 触发层 + 对话输出规范
3. `monitors/README.md` —— 订阅监控运营细节与已知坑
4. `references/config.md` —— 全部环境变量说明
5. `docs/decisions/` —— 关键架构决策记录（≤15 行 / 篇）
