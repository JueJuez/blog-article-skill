# 订阅监控（monitors/）

持续订阅 **B站UP主** 与 **公众号**，发现新内容 → AI 总结 → 默认落飞书（需 Obsidian 时加 `--obsidian` 双写，见 `RULES.md` §3.0）。
本文件是监控模块的操作文档 + 注意事项；决策背景见 `docs/decisions/DECISION-20260720-sub-monitor.md`。

## 架构

| 文件 | 职责 |
|------|------|
| `state.py` | 每源去重状态（`state.json`），per-source 裁剪防膨胀 |
| `wechat.py` | 公众号源（经 `weread.111965.xyz` 转发发现新文）；token 数小时失效，交互式弹码续期、headless 跳过 |
| `bilibili.py` | B站UP主源（官方 API + WBI 签名，带登录 Cookie） |
| `ad_filter.py` | 广告过滤：整篇纯广告 skip / 干货夹广告净化保留 |
| `run.py` | CLI + 调度入口（`--apply` 直接调总结管线）；末尾自动调 `drain_series_pending` 收尾系列课 |
| `_auth.py` | 公众号扫码登录 / 轮询换 JWT（落盘 `.wechat_auth.json`，日志 `.poll_daemon.log`） |
| `apply_pending_series.py` | 系列课降级待总结队列 drainer：`drain_series_pending` 被 `run.py` 自动调用，把 `pending_series.json` 里已有 `.body.md` 的集串行落飞书 + 重生成总览（详见下方「系列课全自动闭环」） |
| `../shared/series_state.py` | 系列课增量去重状态（`monitors/series_state.json`）：记录每集 base/URL 是否已总结，每日增量只抓未总结的集 |

## 抓取规则（当前版本 · 暂定）

- **时间窗口替代纯数量**（关键改动）：
  - 首跑（`--mode first`）：最近 `BILI_FIRST_WINDOW_DAYS`=**7 天**
  - 每日增量（`--mode auto`）：基础窗口 `BILI_DAILY_WINDOW_DAYS`=**1 天**，**自动补齐**（见下）——断跑数日再跑会按 gap 拉长窗口抓回漏掉的内容，平时按时跑则维持 1 天不变
  - 单页拉满 `BILI_PAGE_SIZE`=**50** 覆盖整个窗口；每类型另有安全上限 `BILI_SAFETY_CAP`=**50**（防极端 UP 单窗口刷爆笔记）。正常情况下窗口 + 单页上限已约束条数。
- **自动补齐窗口（2026-07-28 新增 · 解决断跑丢内容）**：`auto` 非首次运行时，窗口不再死用每日值，而是 `max(BILI_DAILY_WINDOW_DAYS, 距上次成功运行天数 + 1)`，封顶 `BILI_MAX_WINDOW_DAYS`=**30** / `WECHAT_MAX_WINDOW_DAYS`=**30**。由 `state.json` 的 per-source `last_check` 驱动，`seen` 去重保证多跑/漏跑都不会重复总结。即：**你多久没跑，它就自动补多久（封顶 30 天）**，无需改命令、无需手动 `--mode first`。
- 视频与动态各自独立计入窗口、互不抢占；`DYNAMIC_TYPE_AV`（视频转发）在动态侧跳过，由视频路由覆盖，不会双写总结。
- **无干货动态屏蔽**：去掉链接后正文 <15 字，或命中系统通知模板（充电专属问答 /「我回复了@」/「快来围观吧」/「为我充电」）→ 直接丢弃，不进总结管线（但仍记入 `seen`，避免下次重复拉取）。
- **短动态轻量化**：动态正文净化后 ≤ `BILI_SHORT_DYNAMIC_MAX`=**80 字** → 存「短动态速览」（原文 + 元信息），不走重 LLM 总结模板，省 token、防短评灌水。
- **新鲜度标签**：笔记自动带 `#🔥当日` / `#本周` / `#更早` + frontmatter `published_at`（内容**原始发布时间**，中国时区），不再只用"我们处理它的时间"。
- **充电专属视频**：标记 `is_charging`，apply 阶段跳过正文抓取（付费内容无 transcript），仅监控"发过"。

## 频率与风控

- 跨源退避 `BILI_GAP` 默认 **30s**，`run.py` 额外加 **±5s 随机抖动**（避免固定周期被识别成脚本）。
- 同源视频→动态之间退避 `BILI_INTRA_GAP`=**2s**。
- 重试退避 `BILI_BACKOFF`=**5s**（动态接口偶发 `-352`/`4101129`/`4101133` 列入退避重试）。
- **抓取条数多少不影响风控，频率（请求次数）才影响**——已放慢到 30±5s/UP，风控无忧。
- **触发方式（2026-07-24 更新 · 已移除自动调度）**：不再挂每日 10:00/17:00 自动化。改为**用户主动触发**——用户说「跑一次 / 跑一下」等关键词即运行 `python monitors/run.py --mode auto --apply`（抓公众号 + B站UP 并总结：默认写飞书，需 Obsidian 时双写，见 `RULES.md` §3.0）。

## 新会话快速执行（跑一次 / 跑一下）

> 目标：换会话 / 新前端模型也能**照着跑通**，不踩已知坑。完整坑见下方「注意事项」。

1. **运行**：`python monitors/run.py --mode auto --apply`（仅看发现列表就去掉 `--apply`）。
2. **发现阶段（discover_all）**：
   - 公众号：`weread` 代理拿列表（仅元数据）→ 时间窗口 + 去重 + 广告过滤；token 失效才弹码等扫码（≤180s），**token 有效时空轮会自动退避重试**，不会卡。
   - B站：官方 API 一步拿视频 + 动态，号间 30±5s 退避；某号异常只跳过该号、其他号照跑。
3. **抓取 + 总结（apply_summaries）**：
   - 公众号文章：`fetch_web_content` **直连微信**抽正文（`WECHAT_GAP=6s`+抖动防限流），异常/空页进 `pending_refetch` 下次重抓。
   - B站视频/动态：视频 `summarize_video`；动态 API 正文内联，短动态存「速览」、完整动态走重模板。
   - FORCE_AGENT_MODE=1：**不自动总结**，全部进 `pending_summaries.json` 队列。
4. **Agent 总结闭环**：本会话（执行模型）读队列 → 派**子 Agent** 按 `note_type` 模板总结 → `save_summary_only` 落盘（默认飞书，带 `--obsidian` 时追加 Obsidian，见 `RULES.md` §3.0）→ 出队。**原子化**：成功才出队，中断可安全重跑。
5. **看健康度行**：末尾 `📊 本轮健康度：...` 一行，异常（错误/限流待重试高）一眼可见。

**重试矩阵（无需手动干预）**：token 失效→弹码等扫码 / 401 瞬错×3 / 代理空轮退避重试 / 正文限流→`pending_refetch`（`python run.py --refetch-only` 统一重抓）。

## 配置（`.env`）

全部 B站监控变量见 `references/config.md` 的「订阅监控（B站 / 公众号）配置」段，要点：

| 变量 | 默认 | 含义 |
|------|------|------|
| `BILI_COOKIE` | 空 | B站登录态 Cookie（动态接口硬性要求；缺失降级游客态并告警） |
| `BILI_GAP` | 30 | 跨源退避秒数 |
| `BILI_INTRA_GAP` | 2 | 同源视频→动态退避秒数 |
| `BILI_BACKOFF` | 5 | 重试退避基数 |
| `BILI_FIRST_WINDOW_DAYS` | 7 | 首跑时间窗口（天） |
| `BILI_DAILY_WINDOW_DAYS` | 1 | 每日增量基础时间窗口（天）；断跑时自动拉长补齐 |
| `BILI_MAX_WINDOW_DAYS` | 30 | 每日增量窗口封顶（天）；断跑超过此天数只补到此处（更长历史需手动 `--mode first`） |
| `BILI_PAGE_SIZE` | 50 | 单页拉取条数 |
| `BILI_SHORT_DYNAMIC_MAX` | 80 | 短动态轻量化阈值（字） |
| `FIRST_RUN_LIMIT` | 50 | 首跑每类型安全上限（同时影响视频/动态，实际受 `BILI_SAFETY_CAP` 夹取） |
| `STATE_KEEP` | 1000 | 每源 `seen` 保留的最大 ID 数（防 `state.json` 膨胀） |

## 注意事项 / 已知坑

1. **断跑丢内容（已修复，2026-07-28）**：原每日窗口 = 1 天，断跑数日会丢中间内容。现改为**自动补齐窗口**——`auto` 模式按 `state.json` 的 per-source `last_check` 动态拉长窗口（`max(BILI_DAILY_WINDOW_DAYS, 距上次运行天数 + 1)`，封顶 `BILI_MAX_WINDOW_DAYS`=30 / `WECHAT_MAX_WINDOW_DAYS`=30），断跑多久自动补多久，`seen` 去重保证不重复总结。正常每日跑行为不变。想一次补回超过 30 天的极老内容，仍可手动 `python monitors/run.py --mode first --apply`。
2. **公众号 token 不稳定**：`weread.111965.xyz` 转发服务器共享 IP 被微信读书风控，JWT 数小时即失效，**无「稳 + 免费 + 免维护」方案**。`run.py` 检测到失效 → 自动弹二维码（`RELOGIN_QR:` 路径 + `_notify_user` 弹图片查看器+提示框），交互式（Windows 本机）会话用户扫码后续期，**本次运行即继续抓取**公众号（刷新 token 后重试整轮）；headless/自动化下无人看码，等价于本次跳过公众号、保 B站照跑。
   - **续期流程**：`run.py` 检测到 token 失效（或全源 discover 持续 401 兜底）→ `trigger_relogin()` 生成二维码（`login_qr.png`）+ 启动后台轮询 daemon（`python _auth.py poll`）；用微信扫该码即自动把 JWT 落盘 `.wechat_auth.json`，**本次运行立即续抓**（无需下次重跑）。
   - **2026-07-28 修复（过期不再静默丢源）**：`is_token_valid` 探针原打 `list_articles`（过期返回 200 空、失明）→ 过期 token 被误判有效、不弹码、公众号静默全挂。改用 `resolve_mp(force=True)`（过期稳定 401）；并新增「全源零结果 + 持续 401」兜底自动重登。回归测试 `tests/test_wechat_relogin_fallback.py`。
   - **可观测性**：轮询 daemon 输出写入 `monitors/.poll_daemon.log`（含 `[poll-start]` / `[polling] status=...` / `[poll-error#n]` / `[poll-success]`）；巡检该日志可确认扫码是否被捕获、API 是否在超时。
   - **防重复弹窗**：`trigger_relogin()` 带跨进程互斥锁（Windows `msvcrt.locking`）+ 5 分钟幂等 TTL，多进程同时触发（如手动 + 定时重复跑）也只弹一个码、只起一个轮询 daemon（PID 锁定于 `.poll_daemon.pid`）。
   - **失败容忍**：`poll_login` API 偶发超时/5xx 时，`_auth.py` 指数退避重试（3s→6s→…→30s，连续 10 次失败退出），不会因一次抖动就放弃。
   - ⚠️ 同一二维码（UUID）被微信扫码后，weread 服务端会很快销毁旧 UUID（再 poll 返回 500）。若扫完仍 0 条，优先查 `.poll_daemon.log` 是否捕获到 `[poll-success]`；未捕获则重新触发一次让 `run.py` 生成新二维码再扫。
3. **自建 wewe-rss 救不了公众号稳定性**：其 `PLATFORM_URL` 默认仍指向同一转发服务器，脏活没变。
4. **B站 `-352` 真因**：缺 `dm_img_*` WebGL 指纹 + 无登录态 + `web_location` 写错；已带 `BILI_COOKIE` + 指纹修复。付费 / 粉丝可见内容 `code=-404/-403` 直接跳过不重试。
5. **`state.json` 膨胀**：`mark_seen` 按源裁剪到 `STATE_KEEP`（默认 1000，首跑单源约 100 ID，留 10× 余量）。上限取决于"窗口内 ID 数"，与"运行次数"无关——每日跑两遍不会撑爆。
6. **健康度可观测**：`run.py --apply` 末尾打印统计行（视频/动态/速览/广告跳过/错误），监控异常一眼可见。

## 降级闭环与子 Agent 委派

无 `AI_PROVIDER` 时，`skill_main` 进入降级：把原文 + 模板 `prompt` + `raw_file` + `folder` 写入 **`pending_summaries.json`**（按 `url` 去重），`run.py` 末尾打印 `NEED_CONTINUE_SUMMARY` 提示。该队列**不会自动消化**，由外层模型接单：

- **派子 Agent 执行（强制，保持主会话干净）**：每文件夹起一个子 Agent（如 `副业增长/生财有术` 一个、`投资交易/中金点睛` 一个），串行处理避免飞书并发建节点重复；子 Agent 读 `raw_file` → 按 `note_type` 模板总结 → 调 `scripts/persist_summary.py` 落盘（默认飞书，带 `--obsidian` 时追加 Obsidian，保存成功后**自动从队列移除该条**，中途停止可安全重跑）。
- 严禁在主会话里直接总结——会污染上下文、降低总结质量。

**双队列模型（务必分清）**：

| 队列 | 含义 | 重试入口 |
|------|------|------|
| `pending_refetch.json` | **抓取失败**：正文被限流成空 / fetch 报错 | `python monitors/run.py --refetch-only` |
| `pending_summaries.json` | **有正文但无 AI**：等待外层派子 Agent 总结 | 外层派子 Agent 读 raw → `persist_summary.py` |

- 不变量：`pending_summaries` 里的条目**必须携带真实正文**；若某条 raw 缺失 / 过短（限流空壳），`--refetch-only` 会自动把它**提升回 `pending_refetch`** 重抓。故 `--refetch-only` 是唯一抓取重试入口，`scripts/refetch_recover.py` 已删除（其职责被该提升逻辑吸收）。
- 频率保护：`--refetch-only` 逐篇 `WECHAT_GAP=6s` + 抖动，避免再被限流。

## 系列课全自动闭环（2026-08 新增）

B站系列课（多集连续内容）走一套独立的「全系列一次性总结 + 后续增量只抓新集」闭环，与单篇降级队列并存。

**核心语义（用户决策 · 2026-08）**：
- **首抓**：订阅的 UP / 公众号若含系列课，默认把**全系列**总结一次（落飞书，除非显式 `--obsidian`）。
- **增量（每日 `auto`）**：UP 更新后，按 `series_state.json` 跳过已总结的集，**只抓取未总结的新集**，避免重复总结。
- **落地全自动**：`monitors/run.py --apply` 末尾自动调 `drain_series_pending()`，无需再手动跑命令。

**三队列 / 三状态文件（务必分清）**：

| 文件 | 含义 | 去重/重试入口 |
|------|------|------|
| `pending_refetch.json` | **抓取失败**：正文被限流成空 / fetch 报错 | `python monitors/run.py --refetch-only` |
| `pending_summaries.json` | **单篇有正文但无 AI**：等外层派子 Agent 总结 | 外层派子 Agent 读 raw → `persist_summary.py` |
| `pending_series.json` | **系列课降级待落盘**：`run.py` 发现系列且降级时登记（含每集 `degraded_raws`）；`drain_series_pending` 把已产出 `.body.md` 的集落飞书后出队 | 被 `run.py` 自动 drain；也可手动 `python monitors/apply_pending_series.py [--regenerate] [--obsidian]` |
| `series_state.json` | **系列课增量去重状态**（运行时生成）：记录每系列已总结的集 `base`/`url`/`author`，每日增量据此跳过 | 代码内部读取；`python scripts/series_maintenance.py forget --series <名>` 可清空某系列记录重抓 |

**闭环链路**（`run.py --apply` 一次跑完）：
1. discover → 抓到系列课（`fetch_bilibili_series` 拿全集字幕）。
2. 降级（无外部 AI）：每集产出 `notes/<系列名>/*_raw.md`；`run.py` 把系列登记进 `pending_series.json`。
3. 本会话（执行模型）派子 Agent 把 raw → `.body.md` 总结正文。
4. `run.py` 末尾自动 `drain_series_pending()`：串行调 `_save_series_note` 落飞书 → `series_state.mark_done`（增量去重关键）→ 删本地 raw/body → 重生成「00_系列总览」（upsert：删旧节点 + 建新，**不重复**）。

**关键工程纪律**：
- 系列课**只落飞书**（除非 `--obsidian` 双写），与单篇一致。
- 落盘用「删旧节点 + 建新」而非 `docs +update --command overwrite`——后者会把文档标题改写成正文首行，破坏标题去重、产生重复节点（2026-08 踩坑修复）。
- 增量靠 `series_state.json`，**不依赖本地 `notes/` 文件**；本地中间文件（`.body.md` / `_raw.md` / `*.manifest`）属冗余副本，可安全删除（`.gitignore` 已忽略，删除不可逆）。
- 维护工具：`scripts/series_maintenance.py`（`verify` 校验飞书节点一致性 / `regen-overview` 重生成总览 / `reland` 重落地），用于飞书侧系列运维。

## 用法

```bash
# 仅发现新内容（输出 JSON，不总结）
python monitors/run.py                 # 等价 --mode auto
python monitors/run.py --mode first    # 首跑回填（7 天窗口）

# 发现并直接调总结管线落盘（默认飞书，需 Obsidian 时加 --obsidian）
python monitors/run.py --apply
python monitors/run.py --mode first --apply
```

订阅配置：`monitors/subscriptions.json`（参考 `subscriptions.example.json`）。

## 公众号历史回溯（续批）

把某公众号 N 年内历史文章分批抓全并总结。**可复用**：今天哥飞+生财，明天别的号，只需往队列加 job，无需改代码/命令。

核心机制
- 游标 = `state.json` 的 `seen`（与日常监控同一套去重）；每账号回溯进度存 `state["backfill"][name]`（`done`/`reason`/`oldest_ts`）。不重置即可续批。
- 分批：每批约 15 篇，多跑几次自然往前翻；`discover` 的 backfill 分支**只 mark 本批 `new` 为 seen**，保留续批能力（日常监控才 mark 全部 fetched）。
- 完成判定（自动，免无限重复拉取）：本批 0 新 或 剩余未抓已全部落在 batch 内 →
  - 代理最老可达日期 `< since` → `reached_since`（已越过起点）
  - 否则 → `proxy_depth:<最老ts>`（代理历史深度上限，更早文章代理侧不可达，**非代码问题**）
- 范围保护：只处理目标号，绝不波及其他订阅源（`WECHAT_BACKFILL_NAMES` 门禁）。

用法
```bash
# 1) 入队一个回溯 job（名字须存在于 subscriptions.json 的 wechat 列表）
python monitors/run.py --backfill --names 哥飞,生财有术 --since 2026-01-01 --batch 15

# 2) 立即跑本批次并入队待总结（落盘游标 + 抓正文入 pending_summaries，由执行模型 drain 落飞书）
python monitors/run.py --backfill --names 哥飞,生财有术 --since 2026-01-01 --apply

# 3) 自动化续批：从 backfill_targets.json 取第一个未完成 job 跑一批（recurring 自动化即用此）
python monitors/run.py --backfill --drain --apply

# 4) 重置某号回溯完成状态（想重新往前翻时）
python monitors/run.py --backfill --reset-backfill 哥飞,生财有术
```

队列文件 `monitors/backfill_targets.json`（已 gitignore）：
```json
[{"names":["哥飞","生财有术"], "since":"2026-01-01", "batch":15, "done":false}]
```
「今天哥飞明天别的号」= 往队列加 job；一条 recurring 自动化 `--drain` 即可逐 job 续批。
注意：微信 token 数小时失效；自动化设 `WECHAT_RELOGIN_WAIT=0` 防无人值守阻塞，失效时跳过、token 有效时自动续。
