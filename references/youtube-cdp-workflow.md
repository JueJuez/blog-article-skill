# YouTube 字幕抓取 · CDP 方案（共享克隆实例 + ensure_endpoint 编排）

> **这是 YouTube 字幕问题的「终极解法」文档。换会话、换 AI，只要照此执行，即可一次性走通「给链接 → 抓字幕 → 总结 → 存档」全流程。**
> **2026-09-12 S3/S4 架构升级（PLAN-20260911）**：Chrome 实例层已上移用户级技能 `cdp-automation-profile`——共享克隆目录 + 动态端口 + `ensure_endpoint` 三段式编排；`videos/cdp_launch.py` 已删除（S5，2026-09-12 用户二次批准提前执行）。本文档已全面对齐新架构。

---

## 0. TL;DR — AI 拿到 YouTube 链接后只做一件事

```bash
cd <项目根>
python videos/run.py --url "https://www.youtube.com/watch?v=XXXX"
```

- 脚本会自动：① 尝试 youtube-transcript-api（沙箱直连时用）② 超时则**自动经 `ensure_endpoint` 确保共享克隆 Chrome 实例就绪**并抓取字幕 ③ 总结 ④ 存档到 Obsidian / notes。
- 若无外部 AI Provider → 返回 `need_continue_summary` + 字幕文本 + 对应模板；**由外层对话（你）读字幕、写总结、调用 `save_summary_only` 存盘**（详见第 4 节）。
- 全程**不 kill、不重启用户的真实 Chrome**：用共享克隆目录 `%LOCALAPPDATA%\CdpAutomationProfile\Chrome` 的实例；连接只断开、实例常驻（D6：最后持有者才关灯）。

### 0.1 新会话最短路径（拿到 YouTube 链接后，照这三句走，别自创）

> AI 一拿到链接就该立刻知道这三步对应代码在哪、由谁自动完成——**不要自己写脚本、手搓 URL、或调试中间步骤**。

1. **确保 CDP 端点就绪（三段式）** → 自动由 `ensure_endpoint()`（技能内核 `cdp-automation-profile`，项目侧经 `shared/cdp_session.py` 薄子类调用）完成：
   - ① 探测 DevToolsActivePort 端口文件，活实例直接 connect_over_cdp 复用（`action=reused`）；
   - ② 端口失效先按活进程命令行自愈 stale 端口文件（`action=healed`）；
   - ③ 仍失败才锁内冷启动（`action=rebuilt`；clone 陈旧/缺失按 3 天 marker 关 Chrome + 一次性全量复制）。
   - 返回 `EnsureResult(endpoint, port, profile_dir, action, proc)`。
2. **打开这个视频路径** → 自动由 `videos.cdp_capture.capture_transcript()` 完成：经 CDP 用 **PUT** 开标签 `https://www.youtube.com/watch?v=XXXX`，ws 连动态端口（`suppress_origin=True`），等页面加载。
3. **访问字幕接口** → 自动由同一函数完成：触发播放器开字幕，让它**自发** `/api/timedtext` 请求（自带正确 `pot`），用 CDP `Network` 域**拦截响应体** → 解析为纯文本。

**以上三步全部封装在一条调用里**：`fetch_transcript(url)`（只取字幕）或 `videos/run.py --url <链接>`（取字幕→总结→存档）。AI 只需调一个函数，剩下交给代码。

---

## 1. 为什么需要 CDP（背景，务必理解，避免重复踩坑）

### 1.1 网络现实
- 本机（用户机器）**只有浏览器扩展代理能上 YouTube**，没有给 Python/curl 用的本地端口（用户不开 Clash 系统代理）。
- 因此：Python `urllib` / `requests` / `yt-dlp` 连 `youtube.com:443` **直接 TCP 超时**；bookmarklet 也要求用户手动点。
- **但沙箱里 `youtube-transcript-api` 能直连 YouTube**（库每次调用实时生成签名、无过期问题）。

### 1.2 三条互相独立的「为什么不能直接抓」
1. **无出口**：本机裸跑脚本连不上 YouTube（见 1.1）。
2. **手搓 timedtext URL 会过期**：用户曾存的 `https://www.youtube.com/api/timedtext?...&signature=...&pot=...` curl 失效，因为 `signature`/`ei`/`pot` 及请求头里的 `X-Goog-Visitor-Id`/`X-Youtube-Identity-Token` 全是**会话级、会轮换**的；`expire` 通常 ~24h。
3. **裸拼 baseUrl 拿到空 200**：页面 `captionTracks[].baseUrl` 现带 `variant=gemini` 但**不带 `pot`(PoToken)** → 直接 fetch 返回 HTTP 200 但 body 长度为 0。`pot` 由播放器内部 BotGuard 现生成，手动补不了。

### 1.3 Chrome 136+ 三道安全坑（逐一踩过，必须遵守）
1. `--user-data-dir` 指向**默认目录**时调试端口被拒：日志 `DevTools remote debugging requires a non-default data directory`。→ **必须用非默认路径的克隆目录**（共享克隆目录本就非默认）。
2. `/json/new?url` 用 GET → **405 Method Not Allowed**。→ 必须用 **PUT**。
3. WebSocket 握手带 `Origin` 头 → **403 Forbidden**（即便 `--remote-allow-origins` 精确匹配也没用）。→ 连接时 `suppress_origin=True`（不发 Origin 头），Chrome 放行。
4. （bash 陷阱）`--remote-allow-origins=*` 的 `*` 会被 shell 当通配符展开 → 参数传错。→ 用 `subprocess` **列表传参**（代码里已这么写，勿回退成 shell 字符串）。

### 1.4 解法核心
> **不自己拼 URL，而是让 YouTube 播放器自己开字幕**（它会带上正确的 `pot` 发请求），用 CDP `Network` 域**拦截那条真实成功的 `/api/timedtext` 响应体**——等于在 F12 Network 里抓包。

### 1.5 背景注记：代理扩展 iGuge 与 `--load-extension` 的撤销（D4）
> 历史背景，现在**不要**再往启动命令加 `--load-extension`。

- 本机实际代理扩展是 **iGuge**（`ncldcbhpeplkfijdhnoepdgdnmjkckij`，`2.3.9_0`），装在用户真实 Chrome 的源 profile 里。
- 2026-09-12 起（决策 D4，随 Chrome 151+ 收紧一并落地）：启动**不再追加** `--load-extension`——iGuge 装在源 profile，随「一次性全量复制」自动带进共享克隆目录，无需双保险挂载。
- 代理是否生效属**运行时验证点**：若 YouTube 打不开回到 `PLAN-20260911-cdp-unified.md` §5.3 重议，不自行改启动参数。

---

## 2. 架构

```
用户给 YouTube 链接
      │
      ▼
videos/run.py --url
      │
      ▼
videos.fetch.fetch_transcript(url)
      │
      ├─① youtube-transcript-api 直连（沙箱可用；本机无出口会超时 ~25s）
      │
      └─② CDP 回退（本机首选）
            │
            ▼
         ensure_endpoint()（技能内核 cdp-automation-profile）
            │  三段式：①探测 DevToolsActivePort 复用（reused）
            │         →②自愈 stale 端口文件（healed）
            │         →③锁内冷启动（rebuilt；共享克隆目录
            │           CdpAutomationProfile\Chrome，clone 陈旧/缺失
            │           按 3 天 marker 全量复制；动态端口）
            │  → EnsureResult(endpoint, port, profile_dir, action)
            ▼
         videos.cdp_capture.capture_transcript(url)
            │  CDP: 开标签(PUT) → ws(suppress_origin, 动态端口) → 触发播放器开字幕
            │       → Network 拦截 /api/timedtext 响应体 → parse_body → 纯文本
            │  （模块级 _ENDPOINT_CACHE 复用端点，多轮抓取不重复编排）
            ▼
         .cache/yt_transcript_<vid>.{txt,json}
      │
      ▼
videos.main._summarize_and_save → 分段两段式总结 → 存档(Obsidian/notes)
      │
      ├─ 有 AI Provider → 直接出笔记
      └─ 无 Provider   → need_continue_summary + 字幕文本 + 模板 → 交外层对话
```

---

## 3. 代码模块职责（改之前先读这里）

| 层 | 文件 | 职责 | 关键接口 |
|----|------|------|----------|
| 技能内核 | `cdp-automation-profile/cdp_session.py` | CDP 实例生命周期单一真源：连接语义 / `ensure_endpoint` 三段式 / 文件锁 / D6 holder 关灯 | `ensure_endpoint() -> EnsureResult`；`CdpSession`（`with` 语义：挂接 + 注册持有者，close 只断开，最后持有者关灯）；`CdpSession.from_endpoint(endpoint)`（并行 worker 复用） |
| 技能 CLI | `cdp-automation-profile/ensure_endpoint.py` | 手动诊断 / 纯探测 / 强杀兜底 | `--probe-only`（不可用退出码 2）、`--json`、`--dir`、`--kill` |
| 项目薄子类 | `shared/cdp_session.py` | 加载技能内核 + re-export，消费方零改动 | `SharedCdpSession`、`ensure_endpoint`、`CdpSession`、`EnsureResult`、`probe_endpoint` |
| 字幕拦截 | `videos/cdp_capture.py` | 经 CDP 拦截字幕响应体，转纯文本并落盘；已迁移 `ensure_endpoint` 编排（模块级 `_ENDPOINT_CACHE`） | `capture_transcript(url, port=None, wait)`；CLI `--port`（default None = 自动编排） |
| 获取层 | `videos/fetch.py` | `fetch_youtube_transcript` 先试 API(超时 25s) 再 CDP 回退；已迁移 `ensure_endpoint` 编排 | `fetch_transcript(url)`、`fetch_youtube_transcript_cdp(url)` |
| 编排 | `videos/main.py` | 获取 → 分段两段式总结 → 存档 | `summarize_video(dict)` |
| CLI | `videos/run.py` | 命令行入口 | `python videos/run.py --url "..."` |

**路径与契约常量**（技能内核统一管理，项目侧不自定义）：
- 共享克隆目录：`%LOCALAPPDATA%\CdpAutomationProfile\Chrome`（环境变量 `CDP_PROFILE_DIR` 覆盖；旧 `ProfileClone` 目录已弃用）
- 调试端口：**动态**（以 DevToolsActivePort 端口文件为准，不再固定 9222）
- clone 新鲜度：`clone_is_fresh()` 3 天 marker；过期/缺失才关 Chrome + 一次性全量复制（增量同步会破坏 Secure Preferences 导致扩展/登录态丢失，已废弃）
- 并发：冷启动第③段全程持克隆目录旁 `.{目录名}.cdp.lock` 文件锁 + 锁内 double-check
- 退出（D6）：`close()` = 断开 + 自注销持有者；最后持有者关灯（`.cdp_holders` 注册表 + PID 存活探测 + 锁内原子判定；`CDP_IDLE_SHUTDOWN` 默认开，置 0 = 纯常驻）
- 技能定位：`$CDP_SKILL_DIR` → `dirname($CDP_SKILL_PY)` 两条回退链，落空报错指引 `.env` 配置（本项目 `.env` 已配 `CDP_SKILL_DIR`）

---

## 4. 完整业务流程（AI 执行清单）

> 触发条件：用户说「总结/整理」+ 给了 YouTube 链接（见 SKILL.md 触发规则）。

### 步骤 1 — 抓字幕（全自动）
```bash
cd <项目根>
python videos/run.py --url "https://www.youtube.com/watch?v=XXXX"
```
- 若沙箱直连可用 → 走 API，秒回。
- 若本机无出口 → API 超时后自动 CDP：实例未起时先全量复制克隆目录（几十秒）并冷启动 Chrome（独立窗口），之后复用常驻实例，秒级。
- 字幕落到 `.cache/yt_transcript_<vid>.txt`（纯文本）与 `.json`（含标题/作者/时长/简介/章节）。

### 步骤 2 — 判断总结路径
- 脚本若直接输出 `✅ 视频总结已自动保存` + 文件名 → **完成**，告知用户路径即可。
- 若输出 `⚠️ 未配置外部 AI Provider，已准备好字幕内容，等待外层对话（Agent）按笔记模板总结` → 进入步骤 3（降级模式）。字幕正文会直接吐到 stdout（无需再读文件）。

### 步骤 3 — 降级模式（外层对话做总结）
1. 读取字幕：直接用 stdout 里的【原始字幕内容】，或 `Read .cache/yt_transcript_<vid>.txt`。
2. 按视频性质选 `note_type`：
   - 公开课/讲座/演讲/播客/访谈/视频口播 → `key_points`（要点提炼）
   - 教程/方法论/实操 → `structured`（结构化复盘）
   - （不指定则 `videos.main` 会用 `classify_note_type` 自动判定）
3. 用对应模板（`prompts.get_note_prompt(note_type)`）撰写总结。
4. 调用保存（关键，否则等于没存）：
   ```python
   from articles.main import save_summary_only
   save_summary_only({
       "summarized_content": "<你写的总结>",
       "original_url": "https://www.youtube.com/watch?v=XXXX",
       "original_title": "<视频标题>",
       "tags": ["要点提炼"],
       "note_type": "key_points",
   })
   ```
   （`save_summary_only` 自带 dedup 闸门 + 机械质量门禁 + folder 自动路由，`folder` 不传即可；拦截返回 `VERIFIER_FAILED:<issues>` 时按 issues 修复重试。）
5. 告知用户：一句话结论 + 笔记类型 + 成品路径。

---

## 5. 会话启动快速自检（换 AI/换会话照做）

1. ✅ 读本文档（路径：`references/youtube-cdp-workflow.md`）。
2. ✅ 确认 Chrome 装在 `C:\Program Files\Google\Chrome\Application\chrome.exe`（默认即在）。
3. ✅ 无需 kill 用户 Chrome；CDP 用共享克隆实例（连接只断开，实例常驻，最后持有者才关灯）。
4. ✅ 直接跑 `videos/run.py --url <链接>`，让脚本自己处理 ensure_endpoint 编排。
5. ✅ 若无 AI Provider，按第 4 节步骤 3 走降级，并务必调用 `save_summary_only` 存盘。

---

## 6. 故障排查

| 现象 | 原因 / 解决 |
|------|------|
| CDP 端点连不上 | `ensure_endpoint` 三段式会自动自愈/冷启动；若仍失败，跑技能 CLI `ensure_endpoint.py --probe-only --json`（不可用退出码 2）看端口/进程状态；兜底 `--kill` 清实例后重跑（marker 未过期不会重新复制）。 |
| 冷启动端口迟迟不就绪（仅 TRAE 沙箱） | 沙箱按路径拦截 Chrome **启动期写盘**（Crashpad/BrowserMetrics/lockfile）→ 30s 端口不就绪、端口文件状态机受扰。**规避**：先在沙箱外起好实例，再 `python videos/cdp_capture.py --url <链接> --port <显式端口>` 直连（已验证）；生产环境（用户日常跑）不受影响。详见 `docs/decisions/DECISION-20260910-cdp-sandbox-bypass.md`。 |
| `DevTools remote debugging requires a non-default data directory` | 误用了默认 user-data-dir。共享克隆目录本就是非默认路径，勿改成默认路径。 |
| `403 Forbidden` on WebSocket | ws 连接带了 Origin 头。确认 `create_connection(..., suppress_origin=True)`。 |
| `405 Method Not Allowed` on `/json/new` | 用了 GET。确认用 PUT。 |
| 抓到空 200（字幕 0 字） | 仍在「自己拼 baseUrl」老路。必须用「播放器自发请求 + Network 拦截」（`cdp_capture` 已是此路）。 |
| YouTube 页面打不开 / 空白 | 代理失效（**基础设施问题，不是没字幕**）。iGuge 装在源 profile、随全量克隆自带（D4，勿再加 `--load-extension`）；若代理真失效属运行时验证点失败，回 `PLAN-20260911-cdp-unified.md` §5.3 重议。 |
| `websocket` 模块缺失 | `pip install websocket-client`。 |
| 想清掉共享实例 | 跑技能 CLI `ensure_endpoint.py --kill`（强杀兜底）；**勿手动删克隆目录**——3 天 marker/锁文件与目录同生命周期，乱删会导致编排状态机受扰。 |

---

## 7. 与旧方案的关系（不要再走弯路）

| 方案 | 状态 | 说明 |
|------|------|------|
| `youtube-transcript-api` 直连 | ✅ 保留，沙箱首选 | 沙箱内直连成功，无过期问题。 |
| CDP 拦截（本文档：共享克隆实例 + ensure_endpoint） | ✅ **本机首选，已自动化** | 本机无 YouTube 出口时的终极解法，集成进 `fetch_transcript`。 |
| 旧 `cdp_launch.py`（Chrome-CDP 独立副本 + 固定 9222 + 每次强制同步） | ❌ **已删除（2026-09-12 S5）** | 被 `ensure_endpoint` 三段式取代：共享克隆目录、动态端口、健康复用优先、marker 过期才全量复制。 |
| bookmarklet → 本地桥 8899 | 🟡 仍可用但已非必需 | 纯浏览器方案，需用户点书签；CDP 全自动后一般不再需要。 |
| 手搓 timedtext curl | ❌ 废弃 | 签名/会话过期，无法复用。 |
| `YT_PROXY` 系统代理 | ❌ 本机无效 | 用户的代理只认浏览器扩展客户端，系统代理端口对 Python 拒连（SSL EOF）。仅沙箱或真有可复用端口时有意义。 |

---

## 8. 无 CC 字幕的情况（终端行为，必须照做）

> 权威定义见 `RULES.md` §4.4。视频在 YouTube 上没字幕时，**先由 `videos/main` 自动走 ASR 兜底**；ASR 也失败才进入终态回话，**不要自己开发新兜底**。

### 8.1 判定与流程

当 `fetch_transcript(url)` 返回 `None`，且 CDP 已成功打开页面（能拿到标题、`ytInitialPlayerResponse.captions` 为空 / 播放器 `movie_player` 字幕 tracklist 为空），即判定为**视频本身在 YouTube 上没有任何 CC / 自动字幕轨道**。

此时 `videos/main._handle_single_video` 会自动调用 `videos.asr.transcribe_video`：

- ASR 成功（下载音频 + 本地 faster-whisper 转写出文本）→ 继续正常总结并落盘（默认本地 Obsidian）。
- ASR 也失败（环境无 ffmpeg / faster-whisper / yt-dlp，或 Python 无 YouTube 出口下不了音频）→ **AI 原样回下面这句话并停止**：

> **【此视频暂无可用字幕（CC 与 ASR 兜底均失败），无法总结内容。】**

- **禁止**在 `videos/asr.py` 已提供的兜底之外「自作主张开发新兜底」。
- **禁止**改动代码去「优化/开发」兜底——除非用户**明确**要求开发。

### 8.2 区分「真无字幕」vs「抓取机制故障」（关键，避免误判乱调试）

| 信号 | 结论 | 做法 |
|------|------|------|
| CDP 端点连不上 / 页面空白 / YouTube 打不开（代理失效） | **抓取机制故障**（基础设施问题，不是视频没字幕） | 按 §6 排查：`ensure_endpoint.py --probe-only` 诊断、`--kill` 兜底；**不要**回「无字幕」 |
| 页面正常加载、能拿到标题、但 `captionTracks` 为空 | **真无 CC 字幕** | 交给 `videos/main` 自动 ASR 兜底；ASR 失败才回 §8.1 终态文案 |
| `fetch_transcript` 返回有效 `(title, segments)` | 成功 | 正常进入总结/存档流程 |

> 一句话：**页面打得开却没字幕轨道 = 让 ASR 兜底；页面都打不开 = 基础设施坏了，去修 CDP（ensure_endpoint 自愈 / 技能 CLI 诊断），别把它当「无字幕」。**
