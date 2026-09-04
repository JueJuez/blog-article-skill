# 配置说明

## 一、配置文件

配置文件 `.env` 需要放在技能根目录下：

```env
# AI Provider 配置
# 默认 FORCE_AGENT_MODE=1：总结由当前执行模型（主/子 Agent）完成，不再调用外部 Provider。
# 外部 Provider 仅在 FORCE_AGENT_MODE=0 或代码显式调用外部接口时生效。
FORCE_AGENT_MODE=1

# AI_PROVIDER=openai
# OPENAI_API_KEY=sk-xxxx

# 输出目标配置
OBSIDIAN_VAULT_PATH=D:\你的Obsidian库路径
FEISHU_WIKI_SPACE=你的知识库空间ID
FEISHU_WIKI_PARENT_NODE=父节点Token

# 视频字幕抓取代理（可选）：仅本机裸跑且需代理时设置，脚本自动映射到 HTTP(S)_PROXY
# YT_PROXY=http://127.0.0.1:7890
```

## 二、AI Provider 配置

### 支持的 Provider

| Provider | 说明 | 配置要求 | 获取地址 |
|----------|------|----------|----------|
| `trae` | Trae SDK（子会话模式） | 需安装 trae Python 包 | - |
| `openai` | OpenAI API | `OPENAI_API_KEY` | https://platform.openai.com/api-keys |
| `anthropic` | Anthropic Claude API | `ANTHROPIC_API_KEY` | https://console.anthropic.com/settings/keys |
| `google` | Google Gemini API | `GOOGLE_API_KEY` | https://aistudio.google.com/app/apikey |
| `local` | 本地模型（Ollama） | `LOCAL_API_BASE` | https://ollama.com |
| `mock` | 模拟 Provider | `AI_PROVIDER=mock` | 仅测试用 |

### 自动检测逻辑

1. 若 `FORCE_AGENT_MODE=1`（默认）：总结由当前执行模型完成，**不检测也不调用外部 Provider**。
2. 若 `FORCE_AGENT_MODE=0`：优先使用 `AI_PROVIDER` 指定的外部 Provider（openai/anthropic/google/local）；未指定则按 `openai` > `anthropic` > `google` > `local` 自动检测。
3. Trae SDK 不参与自动检测，需显式设置 `AI_PROVIDER=trae` 才会启用；无外部 Provider 时触发降级流程，由外层对话接手。
4. 使用第一个可用的 Provider。

### 配置示例

**OpenAI**
```env
AI_PROVIDER=openai
OPENAI_API_KEY=sk-xxxx
```

**Anthropic Claude**
```env
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-xxxx
```

**Google Gemini**
```env
AI_PROVIDER=google
GOOGLE_API_KEY=xxxx
```

**本地 Ollama**
```env
AI_PROVIDER=local
LOCAL_API_BASE=http://localhost:11434/v1
```

### 验证 Provider 配置

```python
from articles import list_available_providers, get_ai_provider

# 查看所有可用的 Provider
print("可用 Provider:", list_available_providers())

# 获取当前 Provider
provider = get_ai_provider()
print("当前使用:", provider.name if provider else "无")
```

## 三、Obsidian 配置

### 前提条件

- 已安装 Obsidian 客户端
- 已创建 Obsidian 知识库（Vault）

### 配置步骤

1. 打开 Obsidian，创建或打开一个知识库
2. 获取知识库路径：
   - 在 Obsidian 中打开设置（Settings）
   - 点击 "Vault" -> "Open folder"
   - 复制文件夹路径

3. 在 `.env` 文件中添加：
   ```env
   OBSIDIAN_VAULT_PATH=D:\Your\Obsidian\Vault\Path
   ```

### 效果

配置成功后，文档将自动同步到你的 Obsidian 知识库中。

## 四、飞书配置

### 前提条件

1. **安装飞书CLI**：
   ```bash
   npx @larksuite/cli@latest install
   ```

2. **完成应用配置**：
   ```bash
   lark-cli config init
   ```

### 配置步骤

1. 获取飞书知识库空间 ID：
   - 打开飞书知识库
   - 进入目标空间
   - 从 URL 中提取空间 ID

2. 在 `.env` 文件中添加：
   ```env
   FEISHU_WIKI_SPACE=7636965310725115074
   ```

### 效果

配置成功后，文档将自动同步到你的飞书知识库中。

## 五、输出规则

| 配置情况 | 默认输出（`OBSIDIAN_WRITE` 未设 / 为 0） | Obsidian 成默认（`OBSIDIAN_WRITE=1`） |
|----------|----------|----------|
| 无任何配置 | 保存到 `notes/` 目录（本地兜底） | 同上（无 Obsidian 可写则回退 `notes/`） |
| 仅配置 Obsidian | 保存到 `notes/`（未请求 Obsidian，且无飞书） | 输出到 Obsidian 知识库 |
| 仅配置飞书 | 输出到飞书知识库 | 同上（无 Obsidian 可写则仍写飞书） |
| 两者都配置 | 默认只输出**飞书**（2026-08-08–2026-09-03 规则） | 同时输出到 Obsidian + 飞书（双写） |

> **本项目当前默认（2026-09-04 起）：只写本地 Obsidian，不写飞书。** `.env` 已设 `OBSIDIAN_WRITE=1`（Obsidian 成默认目标）+ `DISABLE_FEISHU_SYNC=1`（关飞书写入），故 `OutputManager()` 默认只解析到 Obsidian。这是 2026-08-08「默认飞书、Obsidian 按需」规则的翻转——用户改用本地库为主、放弃飞书同步。详见 `RULES.md` §3.0 与 `docs/decisions/DECISION-20260904-obsidian-default.md`。

## 六、验证配置

运行以下代码验证配置是否正确：

```python
from articles import OutputManager

manager = OutputManager()
available = manager.get_available_outputs()
print(f"可用输出模块: {[o.name for o in available]}")
```

## 七、视频字幕抓取代理（可选）

YouTube / Bilibili 字幕抓取在 WorkBuddy 沙箱内可直接运行，无需代理。

- **本机且仅有「浏览器扩展代理」（无本地端口）**：无需任何配置。`fetch_transcript` 会先试直连 API（超时约 25s），失败后**自动用 CDP 驱动本机带代理插件的 Chrome 抓取字幕**（见 `references/youtube-cdp-workflow.md`）。这是本机 YouTube 字幕的终极解法。
- **本机且开了 Clash 等系统代理（有可复用端口）**：可设 `YT_PROXY` 让底层请求走代理。

```env
YT_PROXY=http://127.0.0.1:7890   # 本地代理端口（如 Clash 系统代理开启后）
```

设置后脚本自动映射到 `HTTP_PROXY` / `HTTPS_PROXY` 供底层请求使用。

## 八、常见问题

### Q: 飞书CLI 安装失败？

**A:** 确保 Node.js 版本 >= 14.0.0，然后重新安装：
```bash
npm install -g @larksuite/cli
```

### Q: Obsidian 路径配置后无法输出？

**A:** 检查路径是否正确，确保路径存在且有写入权限。

### Q: 飞书文档创建失败？

**A:** 检查飞书CLI 是否已正确配置：
```bash
lark-cli auth login --status
```

### Q: AI Provider 无法使用？

**A:** 检查对应的 API Key 是否配置正确，或尝试设置 `AI_PROVIDER` 指定具体的 Provider：
```env
AI_PROVIDER=openai
OPENAI_API_KEY=sk-xxxx
```

## 九、质量闸门（可选开关）

质量闸门是「总结后的第二遍 AI 把关」：笔记生成后，再调一次 AI 按 6 条红线（忠于原意 / 不敷衍 / 上下文清晰 / 思维模型落地 / 结构合规 / 可信度标注）打 0–100 分，低于阈值则把问题清单反馈给总结模型**重试一次**。

> **默认关闭**。原因：开启会多消耗一轮 AI 调用。需要更严格质检时随时开启。

### 去哪里开关

在技能根目录的 **`.env`** 文件里设置环境变量（与 `AI_PROVIDER`、`OBSIDIAN_VAULT_PATH` 等同文件；模板见 `.env.example`）：

| 变量 | 默认值 | 作用 |
|------|--------|------|
| `NOTE_QUALITY_GATE` | `0`（关） | 置 `1` 即开启质量闸门；置 `0` 或不写即关闭 |
| `NOTE_GATE_THRESHOLD` | `85` | 评分阈值；仅开启闸门后生效，低于此分触发重试 |

```env
# 开启质量闸门
NOTE_QUALITY_GATE=1
# 可选：调高/调低门槛（默认 85）
NOTE_GATE_THRESHOLD=85
```

### 行为说明

- **开启（`NOTE_QUALITY_GATE=1`）**：`verify_note()` 调外部 AI Provider 审核 → 返回 `{score, passed, issues}`；`should_gate_retry()` 在 `score < NOTE_GATE_THRESHOLD` 时返回 `True`，由调用方把问题清单追加进 prompt **重试一次**。
- **关闭（默认）**：`verify_note()` 直接返回 `None`，跳过整轮审核，不重试、不额外耗 token。
- **降级路径（无外部 AI Provider）**：闸门无法调 AI，此时走「自检闸门」——`QUALITY_GATE_SELFCHECK` 文本会被追加进模板 prompt，由外层对话模型按 6 红线自行核对（无循环依赖、不阻塞）。
- **阈值与重试用环境变量即可调节**，无需改代码。代码层见 `prompts/templates.py` 的 `QUALITY_GATE_ENABLED` / `GATE_THRESHOLD`。

## 十、订阅监控（B站 / 公众号）配置

监控模块见 `monitors/`，运营细节与已知坑见 `monitors/README.md`。订阅源本身写在 `monitors/subscriptions.json`（非 `.env`），结构参考 `monitors/subscriptions.example.json`：

```json
{
  "wechat":   [{"mp_id": "MP_XXX", "name": "公众号名（可选）"}],
  "bilibili": [{"uid": "22675713"}]
}
```

### B站监控相关 `.env` 变量

| 变量 | 默认值 | 作用 |
|------|--------|------|
| `BILI_COOKIE` | 空 | B站登录态 Cookie（**动态接口硬性要求**；缺失降级游客态并告警）。浏览器 F12→Application→Cookie 复制整段 |
| `BILI_GAP` | 30 | 跨源退避秒数（`run.py` 额外加 ±5s 随机抖动，避免被识别成脚本） |
| `BILI_INTRA_GAP` | 2 | 同源「视频→动态」之间退避秒数 |
| `BILI_BACKOFF` | 5 | 重试退避基数（动态接口偶发 `-352`/`4101129` 列入退避重试） |
| `BILI_MAX_REQ_PER_HOUR` | 170 | **请求级滑动 1h 预算**（挂在 `_bili_urlopen` HTTP 单一咽喉，所有 B站管线共用；命中 412 自动 ×0.7 降档，下限 30；0=不限） |
| `BILI_FIRST_WINDOW_DAYS` | 30 | 首跑时间窗口（天）：只处理 N 天内发布的视频/动态（默认值见 `monitors/bilibili.py` 的 `_BILI_FIRST_WINDOW_DAYS`） |
| `BILI_DAILY_WINDOW_DAYS` | 1 | 每日增量**基础**时间窗口（天）；`auto` 非首次运行时按「距上次成功运行天数 + 1」自动拉长补齐 |
| `BILI_MAX_WINDOW_DAYS` | 30 | 每日增量窗口**封顶**（天）；断跑超过此天数只补到此处（更长历史用 `--mode first`） |
| `WECHAT_WINDOW_DAYS` | 2 | 公众号每日增量基础窗口（天）；同样支持自动补齐，封顶 `WECHAT_MAX_WINDOW_DAYS` |
| `WECHAT_MAX_WINDOW_DAYS` | 30 | 公众号每日增量窗口封顶（天） |
| `BILI_PAGE_SIZE` | 50 | 单页拉取条数（覆盖整个时间窗口） |
| `BILI_SHORT_DYNAMIC_MAX` | 80 | 短动态轻量化阈值（字）：净化后正文 ≤ 此值走「速览」，不走重总结模板 |
| `FIRST_RUN_LIMIT` | 50 | 首跑每类型安全上限（实际受 `BILI_SAFETY_CAP`=50 夹取，防极端 UP 刷爆） |
| `STATE_KEEP` | 1000 | 每源 `seen` 保留的最大 ID 数（`mark_seen` 按源裁剪，防 `state.json` 膨胀） |

### 公众号认证（非 `.env`）

公众号经 `weread.111965.xyz` 转发发现新文，认证 token 落在 `monitors/.wechat_auth.json`（已 gitignore），是转发服务器自签 JWT，**数小时即失效**。`run.py` 检测到失效会弹二维码（`RELOGIN_QR:`）并阻塞等待扫码：交互式（Windows 本机）会话**扫到即刷新 token、本次自动继续抓取公众号源**（无需手动重跑）；若 `WECHAT_RELOGIN_WAIT`（默认 180s）内未扫码，则**本次跳过公众号源、保 B站照跑、下次运行恢复**；headless/自动化（`WECHAT_RELOGIN_WAIT=0`）下无人看码等价于跳过。无「稳 + 免费 + 免维护」方案，详见 `monitors/README.md` 注意事项。

续期排查：扫码后仍未恢复公众号，先看 `monitors/.poll_daemon.log` 是否出现 `[poll-success]`（说明 daemon 抓到 token）；若只有 `[poll-error#n]` 或一直 `status=pending`，说明 weread proxy 当前不稳定（超时/5xx）或二维码 UUID 已过期（被微信扫码后服务端会很快销毁旧 UUID），重新触发一次 `run.py` 生成新二维码再扫即可。

### 公众号历史回溯（backfill）`.env` 变量

补回某公众号**最近稳定窗口内**漏抓的文章，复用 `monitors/run.py --backfill` 子命令（详见 `monitors/README.md`「公众号历史回溯（续批）」）。

> ⚠️ **不追求抓全**：由于代理 `publishTime` 元数据伪造，无法可靠判定「已抓到起点」，故队列 job **跑一次即标记 done**，不追求 exhaustive 抓全。想补更多就多跑几轮续批。

以下为底层 env（通常由 `--backfill` 自动设置，一般无需手填）：

| 变量 | 默认值 | 作用 |
|------|--------|------|
| `WECHAT_BACKFILL` | 0 | 置 `1` 启用回溯模式：翻历史页 + 范围限定到 `WECHAT_BACKFILL_NAMES`。⚠️ **无自动完成判定**——`reached_since` / `proxy_depth` 判定已移除（代理 `publishTime` 伪造会导致假完成），队列 job 跑一次即标记 done |
| `WECHAT_BACKFILL_NAMES` | 空 | 回溯目标公众号名（逗号分隔，须存在于 `subscriptions.json`），**必填**；缺则 `discover` 直接 `raise` 拒绝，防污染其他源 `seen` |
| `WECHAT_BACKFILL_SINCE` | 0 | 回溯起点（Unix 时间戳，由 `--since` 自动换算，缺省=今天-`WECHAT_BACKFILL_DAYS`）；仅用于过滤返回文章，**不用于判定是否抓完** |
| `WECHAT_BACKFILL_PAGES` | 20 | 回溯最多翻页数（防御代理返回超深历史导致请求风暴）真源 `monitors/wechat.py:316` |
| `WECHAT_BACKFILL_DAYS` | 35 | `--backfill` 未显式给 `--since` 时的默认窗口天数（= 今天 - 35 天）真源 `monitors/backfill.py:32` |
| `WECHAT_RELOGIN_WAIT` | 180 | 微信 token 失效时等待扫码秒数；设 `0` 则无人值守直接跳过（自动化续批用此值防阻塞） |
| `FIRST_RUN_LIMIT` | 50 | 回溯每批上限（= `--batch`）；抓全历史页后只 `mark` 本批 `new` 为 seen，保留跨运行续批能力 |

> ⚠️ 代理按「条数」而非「时间」截断历史：发文稀疏的号（如哥飞）同样约 100 条上限即可铺到 2025 年中；发文极密的号（如生财有术）同样上限只够到 2026-06，更早文章在代理侧不可达，代码无解。

### UP 视频批量字幕抓取（scripts/fetch_up_range.py）

对「UP 主全量视频」批量抓字幕并**入待总结队列**（与 `scys_batch_fetch.py` 同构：抓取 → 入 `monitors/pending_summaries.json`，prompt/folder 预计算，子 Agent 消费，不再依赖会话手搓）。三步：

```bash
# 1) 拉 UP 全量视频列表 → notes/_scraped/bili_<uid>_videos.json
python scripts/list_up_videos.py --uid <数字UID>
# 2) 抓字幕 + 抓到即入队（结果日志落 notes/_scraped/<author>_fetch_results.json）
python scripts/fetch_up_range.py 1 242 --uid <UID> --author <UP名>
# 3) 清洗队列（dedup 已总结自动出队）后派子 Agent 消费（条目已带预计算 prompt/folder）
python scripts/filter_pending.py
```

- 入队条目：url/title/author/note_type（分类器）/tags/publish_time/folder（统一路由器预计算）/raw_file/prompt（`get_note_prompt + QUALITY_GATE_SELFCHECK`）/queued_at；
- 已在队列或已总结过（dedup 闸门）→ 自动跳过；`--no-enqueue` 可退回纯抓取；
- 队列路径可被 `MON_PENDING_SUMMARY_PATH` 覆盖（与 monitors 并行模式约定一致）。

**限速与风控防护（2026-09-03，两轮迭代）**——教训：零间隔连续抓 2 小时+ 会触发 B站 412 风控，且失败重试 + yt-dlp/ASR 兜底会让每条失败视频反而发出更多请求，越抓越拦：

- **请求级硬顶（2026-09-03 晚，Q2/Q3 落地）**：`BILI_MAX_REQ_PER_HOUR`（默认 **170** 请求/小时），滑动 1h 窗口，挂在 `videos/fetch.py:_bili_urlopen` 这个 HTTP 单一咽喉上——**单视频补齐 / 系列整季 / 监控 / 救回等所有 B站管线自动共用**，不再有「单视频有限流、系列批量零限流」的空洞。预算可算：每视频 ≈3 请求，假设安全上限 200 请求/小时（经验值，用 `<作者>_backfill_*.json` 运行日志里的密度/412 出现点校准），留 15% 余量 ≈ 56 视频/小时。**动态调整**：命中 412 自动降预算 ×0.7（下限 30），本轮内生效。仅靠条间延迟 15~30s 只能压到 ≈440 请求/小时，压不进安全区，必须有请求级硬顶。
- 条间随机延迟 **15~30s**（`--delay-min/--delay-max`），消除机器脉冲节奏（批内由 `run.py --batch-file` 循环执行，批间由编排层执行）；
- **批量化进程模型**：每 `--batch-size`（默认 8）条共用 1 个子进程（129 集 ≈ 17 进程，消除每条一次 Python 冷启动）；
- 单条 412「跳过+记录」进 `<作者>_risk_skip.json`，仅【连续】达 `--risk-threshold`（默认 3）才熔断整批（exit 87）；`--no-stop-on-risk` = 永不熔断；冷却后 `--reset-risk-skip` 重新尝试；
- cookie 惰性轮换：运行开始/命中 412 后自动 nav 校验，失效则从本机 Chrome 提取（事件记入运行日志；手动 `python scripts/bili_cookie_refresh.py`）；
- **结构化运行日志**：`notes/_scraped/<作者>_backfill_<ts>.log` + `.json`（请求级追踪：密度 avg/min/max gap、req/min、412/413/429/timeout 计数、逐条 outcome、cookie 事件）；运行开始自动清理超 `--log-ttl-days`（默认 7）且**无异常**的旧日志，有异常的永久保留；
- 「真成功」判定 = rc==0 且 stdout 含字幕内容（旧版超时条目会误记上一条 rc，重跑即自动修复）；只跳过真成功条目，失败/缺失自动重抓；
- 系列整季抓取（`_fetch_series_entries`）集间 2~4s 随机间隔（实测曾零间隔跑出 194 请求/分钟）。

批量模式自动注入的子进程环境变量（日常单视频调用不受影响）：

| 变量 | 默认 | 作用 |
|------|------|------|
| `BILI_SUB_RETRIES` | 3 | 字幕列表接口（dm/view）重试次数；批量脚本自动设 1（失败不当场重试，留给下一轮） |
| `BILI_FAILFAST_412` | 空 | 置 `1` 后命中 HTTP 412 立即终止该视频请求链（跳过 yt-dlp/ASR 兜底）并输出 `RISK_CONTROL_412_STOP` 标记；批量脚本自动设 1 |

### 调度

**已移除自动调度（2026-07-24）**：不再由 WorkBuddy automation 每日 10:00/17:00 驱动。改为**用户主动触发**——用户说「跑一次 / 跑一下」等关键词即运行 `python monitors/run.py --mode auto --apply`（详见 `monitors/README.md`「新会话快速执行」）。
