# 订阅监控（monitors/）

持续订阅 **B站UP主** 与 **公众号**，发现新内容 → AI 总结 → 落 Obsidian + 飞书（双写）。
本文件是监控模块的操作文档 + 注意事项；决策背景见 `docs/decisions/DECISION-20260720-sub-monitor.md`。

## 架构

| 文件 | 职责 |
|------|------|
| `state.py` | 每源去重状态（`state.json`），per-source 裁剪防膨胀 |
| `wechat.py` | 公众号源（经 `weread.111965.xyz` 转发发现新文）；token 数小时失效，交互式弹码续期、headless 跳过 |
| `bilibili.py` | B站UP主源（官方 API + WBI 签名，带登录 Cookie） |
| `ad_filter.py` | 广告过滤：整篇纯广告 skip / 干货夹广告净化保留 |
| `run.py` | CLI + 调度入口（`--apply` 直接调总结管线） |
| `_auth.py` | 公众号扫码登录 / 轮询换 JWT（落盘 `.wechat_auth.json`，日志 `.poll_daemon.log`） |

## 抓取规则（当前版本 · 暂定）

- **时间窗口替代纯数量**（关键改动）：
  - 首跑（`--mode first`）：最近 `BILI_FIRST_WINDOW_DAYS`=**7 天**
  - 每日增量（`--mode auto`）：最近 `BILI_DAILY_WINDOW_DAYS`=**1 天**
  - 单页拉满 `BILI_PAGE_SIZE`=**50** 覆盖整个窗口；每类型另有安全上限 `BILI_SAFETY_CAP`=**50**（防极端 UP 单窗口刷爆笔记）。正常情况下窗口 + 单页上限已约束条数。
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
- **触发方式（2026-07-24 更新 · 已移除自动调度）**：不再挂每日 10:00/17:00 自动化。改为**用户主动触发**——用户说「跑一次 / 跑一下」等关键词即运行 `python monitors/run.py --mode auto --apply`（抓公众号 + B站UP 并总结双写）。

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
4. **Agent 总结闭环**：本会话（执行模型）读队列 → 派**子 Agent** 按 `note_type` 模板总结 → `save_summary_only` 落盘（Obsidian + 飞书双写）→ 出队。**原子化**：成功才出队，中断可安全重跑。
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
| `BILI_DAILY_WINDOW_DAYS` | 1 | 每日增量时间窗口（天） |
| `BILI_PAGE_SIZE` | 50 | 单页拉取条数 |
| `BILI_SHORT_DYNAMIC_MAX` | 80 | 短动态轻量化阈值（字） |
| `FIRST_RUN_LIMIT` | 50 | 首跑每类型安全上限（同时影响视频/动态，实际受 `BILI_SAFETY_CAP` 夹取） |
| `STATE_KEEP` | 1000 | 每源 `seen` 保留的最大 ID 数（防 `state.json` 膨胀） |

## 注意事项 / 已知坑

1. **断跑丢内容**：每日窗口 = 1 天，若定时任务偶发断跑数日，中间那几天的内容会被窗口滤掉丢失（首跑 7 天不受影响）。如需余量，调大 `BILI_DAILY_WINDOW_DAYS`（如 3 或 7）。
2. **公众号 token 不稳定**：`weread.111965.xyz` 转发服务器共享 IP 被微信读书风控，JWT 数小时即失效，**无「稳 + 免费 + 免维护」方案**。检测到失效时 `run.py` **本次跳过公众号源、保 B站照跑**；交互式（Windows 本机）会话会弹二维码（`RELOGIN_QR:` 路径 + `_notify_user` 弹图片查看器+提示框），用户扫码后续期，**下次运行**恢复公众号抓取；headless/自动化下无人看码，等价于跳过公众号。
   - **续期流程**：`run.py` 检测到 token 失效 → `trigger_relogin()` 生成二维码（`login_qr.png`）+ 启动后台轮询 daemon（`python _auth.py poll`）；用微信扫该码即自动把 JWT 落盘 `.wechat_auth.json`，**下次运行自动恢复**公众号抓取。
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

- **派子 Agent 执行（强制，保持主会话干净）**：每文件夹起一个子 Agent（如 `副业增长/生财有术` 一个、`投资交易/中金点睛` 一个），串行处理避免飞书并发建节点重复；子 Agent 读 `raw_file` → 按 `note_type` 模板总结 → 调 `scripts/persist_summary.py` 落盘（双写 Obsidian + 飞书，保存成功后**自动从队列移除该条**，中途停止可安全重跑）。
- 严禁在主会话里直接总结——会污染上下文、降低总结质量。

**双队列模型（务必分清）**：

| 队列 | 含义 | 重试入口 |
|------|------|------|
| `pending_refetch.json` | **抓取失败**：正文被限流成空 / fetch 报错 | `python monitors/run.py --refetch-only` |
| `pending_summaries.json` | **有正文但无 AI**：等待外层派子 Agent 总结 | 外层派子 Agent 读 raw → `persist_summary.py` |

- 不变量：`pending_summaries` 里的条目**必须携带真实正文**；若某条 raw 缺失 / 过短（限流空壳），`--refetch-only` 会自动把它**提升回 `pending_refetch`** 重抓。故 `--refetch-only` 是唯一抓取重试入口，`scripts/refetch_recover.py` 已删除（其职责被该提升逻辑吸收）。
- 频率保护：`--refetch-only` 逐篇 `WECHAT_GAP=6s` + 抖动，避免再被限流。

## 用法

```bash
# 仅发现新内容（输出 JSON，不总结）
python monitors/run.py                 # 等价 --mode auto
python monitors/run.py --mode first    # 首跑回填（7 天窗口）

# 发现并直接调总结管线落盘（Obsidian + 飞书）
python monitors/run.py --apply
python monitors/run.py --mode first --apply
```

订阅配置：`monitors/subscriptions.json`（参考 `subscriptions.example.json`）。
