# 抓取 scys.com（生财有术）付费文章 · 实操 SOP

> **用途**：把一个「需登录态」的 scys.com 文章正文化为本地 Markdown，供后续总结 / 落盘飞书。
> **定位**：本文件是 `references/login-required-cdp-workflow.md` 的 **scys 专用子集**。新会话 / 新模型想抓 scys，照本文件做即可，**不必先读通用文档**（需要机制细节再去看通用文档）。
> **通用入口**：`python scripts/login_cdp_fetch.py "<URL>" [out.md]` —— 同样适用于知乎 / csdn / 掘金 / Notion 私页 / substack / github 私有 repo 等任何需登录态的 URL。

---

## 0. 一句话（复制即可跑）

```bash
cd <项目根>
python scripts/login_cdp_fetch.py "https://scys.com/articleDetail/xq_topic/45544148552844858"
```

成功 → 正文落 `notes/_scraped/<slug>.md`（默认），或你指定的 `out.md`。

---

## 1. 前提（用户侧一次性，其余全自动）

1. **用户已在浏览器登录 scys.com**（用户唯一被允许做的事）。
2. **用户主 Chrome 已以调试模式启动**（非默认 user-data-dir + `--remote-debugging-port=5494`）。
   - ⚠️ **Chrome 151+ 已禁止在默认 user-data-dir 上开远程调试**，所以不能直接用「`chrome.exe --remote-debugging-port=5494`」（会报 `requires a non-default data directory`）。
   - 正确起法：建一个指向真实 profile 的**目录联接（junction）**，再拿这个非默认路径启动（见下「启动命令」）。这会读写同一份 profile，登录态原样保留。
   - 没启 → 脚本会清晰报错并给出一键命令（见 §4）。

<details><summary>启动命令（一次性建联接 + 以后直接起）</summary>

```bash
:: ① 建联接（一次性，无需管理员，重启后仍在）
mklink /J "%LOCALAPPDATA%\Google\Chrome\DebugUDD" "%LOCALAPPDATA%\Google\Chrome\User Data"
:: ② 用非默认目录启动调试 Chrome（仍指向你的真实 profile）
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=5494 --user-data-dir="%LOCALAPPDATA%\Google\Chrome\DebugUDD"
```
</details>

> ⚠️ **「一次性」的真正含义 = 对该 Chrome 进程的生命周期有效，不是永久有效。**
> `--remote-debugging-port` 是**启动参数**，只对「带这个参数启动的那一个 Chrome 进程」生效。
> Chrome 发生**自动更新 / 崩溃自重启 / 你手动重开**后，新进程不会带这个 flag → 调试端口消失（但磁盘上的 `DevToolsActivePort` 旧文件可能还在，造成「文件说开着、实际没服务」的假象，脚本探测会命中 404）。
> 另一个 2026-08-20 新坑：**Chrome 升级到 151 后，默认目录直接开调试会被拒**，必须走上面的 junction 方案（旧「只带端口」命令已失效，这就是「上次能、这次不能」的真凶）。
> 所以「我没关过浏览器，为什么突然不能访问」的标准答案：**Chrome 在你不知情时被重启 / 自动更新**，flag 随之丢失或旧命令不再生效。
> 修复永远是同一条：彻底关掉所有 Chrome 窗口 → 用上面的 ② 命令以调试模式重启 → 重新登录 → `smoke` 验证见到 `[OK]`。

> ⚠️ **登录态所在的浏览器是用户的 Chrome，不是 Edge** —— Edge 里没有 scys 登录态。2026-08-19 实测确认。若 CDP 误接到 Edge，会抓到「登录墙」而不是正文。

### 1.5 永久零操作方案（已在 2026-08-20 实施，优先用这个）

> 用户在 2026-08-20 授权，已把调试 flags **焊进 Chrome 快捷方式**，从此无需任何手动命令。

- **已改的快捷方式**（用户级，无需管理员）：
  - 任务栏 pin：`...\Quick Launch\User Pinned\TaskBar\Google Chrome.lnk`
  - 快速启动栏：`...\Quick Launch\Google Chrome.lnk`
  - 桌面兜底（新建）：`C:\Users\O1830\Desktop\Google Chrome.lnk`
  - 三处 `Arguments` 均已含 `--user-data-dir=C:\Users\O1830\AppData\Local\Google\Chrome\DebugUDD --remote-debugging-port=5494`。
  - 原快捷方式备份在 `C:\Users\O1830\AppData\Local\Temp\chrome_lnk_backup\`。
- **因此**：用户**正常双击/点任务栏开 Chrome** 就会自动带调试端口 + 继承登录态。Chrome 自动更新/重启后再开也自动恢复，**用户零操作**。
- **下个会话遇 scys 抓不通**：先 `python scripts/login_cdp_fetch.py smoke`；若 flag 没生效，优先怀疑「任务栏 pin 缓存了旧 AppID」（从桌面那枚新快捷方式重新 pin 任务栏即可），而不是 junction 失效（junction 在磁盘上持久化，不会丢）。**不要**再让用户手动敲启动命令——除非用户主动要求或快捷方式被破坏。
- 唯一仍需用户的可能动作：首次（或 Chrome 大版本更新后）弹一次 Windows 防火墙「允许 Chrome 通信」——与 8-19 那次一模一样，点允许即可。

---

## 2. 操作步骤（linear · 照做）

| 步骤 | 命令 / 动作 | 期望 |
|---|---|---|
| 1 | 打开项目根目录终端 | — |
| 2 | `python scripts/login_cdp_fetch.py smoke` | `[OK] port 5494 ws ... devtools-bridge alive`（失败见 §4） |
| 3 | `python scripts/login_cdp_fetch.py "https://scys.com/articleDetail/xq_topic/45544148552844858"` | 见下方真实输出示例 |
| 4 | 读产物（默认 `notes/_scraped/https-scys-com-articledetail-xq-topic-45544148552844858.md`）→ 按 `articles/skill_main` 模板总结 → `OutputManager.save_all` 落飞书（默认只写飞书；双写加 `--obsidian`） | — |

**步骤 3 真实输出示例（[1/3]→[3/3]）**：

```
[1/3] ws endpoint = ws://127.0.0.1:5494/devtools/browser/<uuid>  port 5494 (已通过 /json/version 验证)
[2/3] connect_over_cdp …
        goto https://scys.com/articleDetail/xq_topic/45544148552844858
[3/3] title = '研究了几个月，我终于把公众号做到了99%自动化'  body_chars = 21264  login_wall = 无
        saved → notes/_scraped/https-scys-com-articledetail-xq-topic-45544148552844858.md
```

**退出码**：`0` = 成功且登录态生效；`2` = Chrome 没启 debug；`3` = 撞登录墙（见 §4）。

> 机制：脚本读 `%LOCALAPPDATA%\Google\Chrome\User Data\DevToolsActivePort` → 端口探活 + `GET /json/version` 验真 → Playwright `connect_over_cdp(ws)` 接管活 Chrome → 新标签 → `page.goto` → 等 SPA 渲染 → 取正文（selector 链取最长段，兜底 `body.innerText`）→ 落盘。**不关用户 Chrome、不重启、登录态继承。**

---

## 3. 怎么判断「真抓到了」vs「撞墙」（防误判）

| 现象 | 含义 | 处理 |
|---|---|---|
| 正文 ≥ 几千字 + **无**「立即登录 / 付费查看 / 订阅解锁」字样 | **真抓到**（登录态生效） | 进入总结 / 落盘 |
| body 含「立即登录 / 登录后查看 / 成为会员 / 订阅后」 | **撞墙**（cookie 没到） | 用户在浏览器登录后重跑；文件已落，重跑覆盖 |

- **关键判别**：抓到的页面有「立即登录 / 付费查看」字样 = **壳**（cookie 没到）；无这些字样 + 字数 ≥ 几千 = **真正文**。
- **字节数 vs 字数**：磁盘文件大小（如 49 226 字节）含 Markdown frontmatter / 编码 / 换行；判别以**纯字数**（21 264）为锚，字节数仅用于判文件是否存在。

---

## 4. 故障排查

| 现象 | 根因 | 修法 |
|---|---|---|
| `[FAIL] 本机没找到任何 Chrome DevTools 监听端口…` | 用户 Chrome 没启 debug（或端口被占） | 一次性加 flag 启 Chrome（用 junction 方案，Chrome 151+ 必须）：先关所有 Chrome 窗口 → 执行下面两行 → 正常登录 scys 后重跑<br>```mklink /J "%LOCALAPPDATA%\Google\Chrome\DebugUDD" "%LOCALAPPDATA%\Google\Chrome\User Data"<br>"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=5494 --user-data-dir="%LOCALAPPDATA%\Google\Chrome\DebugUDD"```<br>⚠️ **快捷方式已焊好 flags（2026-08-20）**，正常重开 Chrome 即可。若仍失败才需手动跑上面命令。 |
| `http://127.0.0.1:5494/json/version` 返 **404 / 403**，或 smoke 报 `[FAIL] port 5494 不是 Chrome DevTools` | 三种可能：① Chrome 曾带 flag 启动但之后被重启/自动更新（Chrome 151+ 默认目录被拒后更常见），只剩 stale 的 `DevToolsActivePort` 文件；② 端口被非调试进程占用；③ 用了旧命令（不带 `--user-data-dir`）启动，Chrome 151+ 调试服务未真正起来 | **彻底关闭所有 Chrome 窗口** → 用上面的 junction 两行命令重新启动 → 若 Windows 防火墙弹窗问「允许 Chrome 通信吗」，点 **允许** → 登录 scys → 重跑 smoke |
| `[3/3] body_chars 很小 + login_wall 命中` | 用户在 scys 没登录，或 CDP 误接到 Edge（Edge 无 scys 登录态） | 确认 Chrome 是主浏览器且已登录 scys；`http://127.0.0.1:5494/json/version` 的 `Browser` 字段应是 `Chrome/...`（不是 `Edg/...`） |
| 页面空白 / 长白雪 | SPA 还没渲染完 | 脚本默认等 8s；在 `fetch()` 调 `wait_ms=` 调大（如 15000） |
| `ModuleNotFoundError: playwright` | 缺包 | `python -m pip install playwright`（不需下载浏览器，连现有 Chrome 即可） |

---

## 5. 实证状态（诚实标记 · 遵循通用文档 §10 沉淀纪律）

- **机制**：L3（设计如此，通用文档 §3 描述）。
- **scys 案例**：
  - 2026-08-19 21:18 会话用本流程跑通 —— 落盘 `scys_article.md`（49 226 字节 / 正文 **21 264 字** / 无登录墙）。
  - **2026-08-20 12:33 最终验证通过**（L1）：Chrome 151 根因定位 → junction 解法 → 快捷方式永久化 → smoke OK → scys tag 页 **106,389 字 + 主 tags 页 49,192 字**均无登录墙。永久 debug 模式已实施，新会话零操作。详见通用文档 §2.2 实操记录。

---

## 6. 不要再做的事（防漂移）

- ❌ **不要再用项目根的临时脚本 `scys_fetch.py`** —— 它硬编码了 URL 和输出路径，已被 `scripts/login_cdp_fetch.py` 取代（2026-08-20 已删除）。
- ❌ 不要手搓 cookie 导出 / 抓登录 API / 重新登录。
- ❌ 不要把「PW」理解成密码 —— 本项目语境 **PW ≡ Playwright**。
- ❌ 不要以为 Edge 能代替 Chrome 抓 scys（Edge 无该站登录态）。

---

## 7. 批量抓取「项目标签」全部帖子（2026-08-20 新增 · 已验证 · 参数化）

**入口**：`python scripts/scys_batch_fetch.py --project <项目名> [--limit M]`

- **可抓领域 / menuId 在 `scripts/scys_projects.json` 配置**（不在代码里）：projects（领域名→menuId）+ defaults（since_days=548 一年半 / digested_only=true / batch_limit=30 / min_reading）。**换领域、每半年调整时间窗、改批量大小都只改这个 JSON，脚本零改动。**
- 新领域 menuId 捕获方法：tags 页真实点击该标签 → DevTools Network 里 `searchTopic` 请求的 `menuId` 参数（或 `--list-projects` 查看已配置项）
- 命令行参数可临时覆盖配置：`--since-days` / `--no-digested-only` / `--min-reading` / `--limit`（非精华 P90 补抓：`--no-digested-only --min-reading 11097`）
- 机制：tags 页真实点击翻页捕获 `searchTopic` 响应（不手搓 API 请求）→ 按 `isDigested`+阅读数排序 → 逐篇 goto 正文页 DOM 抓取
- 自动处理：站内 `articleDetail` 引用（前情提要）递归抓一层；外部知识库（飞书/语雀等）滚动到底抓全文；登录墙检测
- **限速（模拟人类）**：篇间随机 15~40s；每 10~15 篇歇 3~8 分钟；翻页间 3~6s；CDP 页面被关自动重试（实测救回过）
- 断点续传：`notes/_scraped/scys/state.json`；中断重跑自动跳过已抓（关机/杀进程都不丢进度）
- 产物：原文 `notes/_scraped/scys/<topicId>.md`；队列 `pending_summaries.json`（总结后标 `summarized:true` 防重复落飞书）
- 总结落盘：子 Agent 读原文 → 按 `CONTENT_SUMMARY_PROMPT` 总结 → `python articles/_save_summary.py <md> --url ... --tags "生财有术,<项目>" --title ...`（默认飞书）
- 已知限制：飞书 **PDF 预览型**文档文字在 canvas 里抓不到（落盘文件头有页码碎片），此类需下载 PDF 另行处理；文字型 wiki 滚动方案有效
- python 环境：用 `D:\App\anaconda3\python.exe`（系统 python 无 playwright）

## 8. 与其他入口的关系（2026-08-20 更新：单篇 scys 已无感打通）

| 入口 | 用途 | 登录态 |
|---|---|---|
| `articles/skill_main` / `articles/run.py` | **任何文章链接（含 scys）**：`fetch_web_content` 检测到 `scys.com` 自动分流 CDP 登录态抓取，普通博客走 requests——**用户对模型说「总结这篇」即可，混合多篇也逐条自动分流** | scys 自动接管 Chrome；公开 URL 无 |
| `scripts/login_cdp_fetch.py`（本 SOP） | 显式单篇登录态抓取（诊断 / 非文章页） | 接管用户主 Chrome 继承 |
| `scripts/scys_batch_fetch.py`（§7） | 按项目领域批量抓 scys 帖子 | 同上 |
| `scripts/feishu_ext_refetch.py`（2026-08-21） | **飞书 wiki/docx 懒加载截断的全文重抓**：增量滚动 `.bear-web-x-container` 容器逐视口收集 innerText 按行去重合并。用法 `python scripts/feishu_ext_refetch.py "<飞书URL>" <out.md>`（用 anaconda python）。批量抓取的 ext 只有目录+开头（500 字级）时用它补；PDF 预览型（canvas 渲染）与 404 仍无解，跳过即可 | 同上 |
| `monitors/run.py --apply` | 公众号 / B站订阅 | 公众号 Cookie / `BILI_COOKIE` |
| `videos/run.py --url` | 视频（YouTube / B站） | 无（字幕 API → CDP → ASR 兜底） |

> 总结：**单篇（含 scys）一律走 `articles` 入口**，模型自动分流；批量按领域抓 scys 走 `scripts/scys_batch_fetch.py`；两者都依赖用户主 Chrome 的 CDP debug 端口（前提见 §1/§1.5）。回归测试钉死该行为：`tests/test_scys_routing.py`（含混合链接场景）。

## 9. 触发词语义：「补齐 scys」（2026-08-20 定义）

用户说**【补齐scys】**或**【补齐生财有术】** → 模型自动执行批量补齐闭环，不用再问流程。

**默认参数**（未提及即用 `scripts/scys_projects.json` 默认，当前=四领域全跑、一年半、仅精华、每批 30 篇，顺序 AI产品开发→小程序→出海→自媒体）。

**条件后缀**（自然语言，模型解析成 CLI 参数临时覆盖默认，可任意叠加）：

| 用户说法 | 模型执行 |
|---|---|
| 补齐scys / 补齐生财有术 | 按默认四领域逐个跑 |
| 补齐scys 出海 ｜ 出海+自媒体 | 只跑指定领域（可多个，+号或顿号连接） |
| 补齐scys 近一年 / 近半年 / 近两年 | `--since-days 365 / 182 / 730` |
| 补齐scys 全部时间 | `--since-days 0` |
| 补齐scys 含非精华 / 非精华也要 | `--no-digested-only` |
| 补齐scys 阅读过万 / 破万也抓 | `--no-digested-only --min-reading 10000` |
| 新领域（配置里没有） | 先按 §7 捕获 menuId → 写进 `scys_projects.json` → 再跑 |

**执行闭环（模型每批照做）**：
1. 后台跑 `D:\App\anaconda3\python.exe -u scripts/scys_batch_fetch.py --project <领域> --limit 30`（断点续传，重复执行幂等，已抓自动跳过）
2. 每批完成 → 派**子 Agent**（>3 篇必须拆子 Agent）总结落飞书（tags=`生财有术,<领域>`，入口 `articles/_save_summary.py`，详见 §7 总结落盘段）
3. 总结完把 `pending_summaries.json` 对应条目标 `summarized:true`（防重复落飞书）
4. 向用户汇报累计/剩余进度，然后启动下一批

**半年重抓**：直接说「补齐scys」即可——`state.json` 断点续传自动只抓上次之后的新帖/漏帖；要扩时间窗加时间后缀（如「补齐scys 近两年」）。
