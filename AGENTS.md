# blog-article-skill — 项目入口（平台无关 · 真源）

> **本文件是跨平台「真源」引导入口。** 无论你用 WorkBuddy / Cursor / Claude / Codex / Copilot / 裸 API，
> 只要把本文件（或它指向的 `RULES.md`）加载进模型上下文，下面这套**流程、功能、配置**就全部接上。
> WorkBuddy 专属的 `SKILL.md` 只是「薄触发层」，所有真规则以本文件 + `RULES.md` 为准，避免平台切换时流程丢失。

## 一句话定位
把「文章 / 视频链接、原文、字幕、订阅的 B站UP主 / 公众号」自动转成结构化笔记，归档到 **Obsidian + 飞书（双写）**。

---

## 三个核心能力

### 能力 1 · 一次性总结（文章 / 视频）
用户给**文章链接、原文、或视频（YouTube / Bilibili）链接 / 字幕** → 总结成笔记。
- **入口（任选）**
  - 文章：`python articles/run.py "https://..."` 或 `from articles import skill_main; skill_main({"content": url_or_text})`
  - 视频：`python videos/run.py --url "https://..."` 或 `from videos import summarize_video; summarize_video({"url": url})`
- 自动按内容类型选模板（`structured` / `key_points` / `interview` / `roundup` / `reading` / `case` / `opinion`），自动双写 Obsidian + 飞书。
- **降级**：无外部 AI 时 `skill_main` 返回 `need_continue_summary` + 原文 + 模板 prompt；外层模型总结后调 `save_summary_only` 存档。

### 能力 2 · 订阅监控（关注 B站UP主 / 公众号）
让用户「持续订阅某账号，自动发现新内容并总结」。
- **配置订阅**：编辑 `monitors/subscriptions.json`（参考 `monitors/subscriptions.example.json`）
  - B站：`{"uid": "数字UP主ID"}`
  - 公众号：`{"mp_id": "..."}` 或 `{"share_url": "公众号分享链接"}`
  - 用户口头说「关注 / 订阅 / 监控 XXX」时，**模型应把对应条目写进这个 JSON**，不要手搓抓取代码。
- **运行**
  - 首跑（回填最近 7 天）：`python monitors/run.py --mode first --apply`
  - 每日增量：`python monitors/run.py --mode auto --apply`（或挂定时任务，每日 **10:00 & 17:00**）
- **抓取规则**：按时间窗口（首跑 7 天 / 每日 1 天）+ 无干货动态屏蔽 + 短动态轻量化 + 新鲜度标签。细节见 `monitors/README.md`。
- B站需要登录态：`BILI_COOKIE` 环境变量（动态接口硬性要求）。

### 能力 3 · 用户侧怎么用（给链接 / 怎么关注）
- 给链接 → 走**能力 1**。
- 说「关注 / 订阅 / 监控 XXX」 → 走**能力 2**（改 `subscriptions.json` + 跑一次首跑）。
- 不确定走哪条 → 先读本文件 + `RULES.md`，不要凭空造流程。

---

## 配置（`.env`）
复制 `.env.example` → `.env`，至少关注：
- `AI_PROVIDER` + 对应 key（openai / anthropic / google / local）—— 不配也能降级由对话模型总结
- `OBSIDIAN_VAULT_PATH` —— Obsidian 库路径（双写一端）
- `FEISHU_WIKI_SPACE` + `FEISHU_WIKI_PARENT_NODE` —— 飞书知识库（双写另一端）
- `BILI_COOKIE` —— B站登录态 Cookie（订阅监控动态接口必需）
- 监控可调参：`BILI_GAP`(30) / `BILI_FIRST_WINDOW_DAYS`(7) / `BILI_DAILY_WINDOW_DAYS`(1) / `BILI_PAGE_SIZE`(50) / `STATE_KEEP`(1000) / `BILI_SHORT_DYNAMIC_MAX`(80)
- 完整变量见 `references/config.md`

## 红线（必须遵守）
- **双写契约（强制）**：成品必须落 Obsidian + 飞书；本地 `notes/` 仅在两者都未配时兜底。
  禁止 AI 手动写 `notes/`、禁止只存本地漏飞书。
- **复用入口，不手写抓取 / 总结**：一律走 `skill_main` / `summarize_video` / `fetch_transcript` / `monitors/run.py`，
  不要临时写 `_xxx.py` 脚本、不要手搓 URL、不要 diagnose 平台私有接口。
- **YouTube 无 CC 字幕**：直接回固定文案「【此视频暂无 CC 字幕，无法为你抓取字幕总结内容。】」，不诊断、不走 ASR、不开发兜底。

---

## 各平台如何加载本文件
- **WorkBuddy**：激活 `blog-article-skill` skill（`SKILL.md` 触发）；也可作为项目级 rules 注入。
- **Cursor**：`.cursorrules` 或 `.cursor/rules/*.mdc`；也认 `AGENTS.md`。
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
