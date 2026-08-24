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
2. **脚本自动选择抓取方式**（无需用户手动启 debug）：
   - **优先**：探测到 Chrome DevTools 端口 → `connect_over_cdp` 接管活 Chrome（不关浏览器）。
   - **回退**（Chrome 151+ 默认）：没有 debug 端口 → 自动回退到 `profile_clone_fetch`（复制真实 profile 到临时目录，用临时 dir 启 headless Chrome，非默认 dir → Chrome 151+ 放行）→ 需要先关闭 Chrome（脚本自动 kill 释放 cookie 锁），复制 ~16GB 需 ~1 分钟，抓完后用户重新打开 Chrome 即可。

> ⚠️ **Chrome 151+ 已废弃 junction 方案**（2026-08-24 实测）：
> 旧方案用 junction（`DebugUDD` → `User Data`）绕过 Chrome 151+「远程调试不能用默认 user-data-dir」的限制。
> 但 Chrome 151 能检测 junction 指向同一物理目录，触发安全清理：
> 1. 清空 `extensions.settings`（扩展注册表）
> 2. 调 `extension_garbage_collector` 删除扩展文件（实测 22 个扩展被删）
> 3. 清 Google 账号关联（`gaia_id` 变空）
>
> **新方案**：不再用 junction / 不再改 Chrome 快捷方式 / 不再需要 `--remote-debugging-port`。
> `login_cdp_fetch.py` 探测不到 debug 端口时自动回退到 `profile_clone_fetch.py`（持久化 ProfileClone 目录，
> 非默认 dir → Chrome 151+ 放行，不会删扩展）。首次全量复制 ~16GB，后续只同步 9 个 cookie 文件（秒级）。

> ⚠️ **登录态所在的浏览器是用户的 Chrome，不是 Edge** —— Edge 里没有 scys 登录态。2026-08-19 实测确认。

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
| `[FAIL] 本机没找到任何 Chrome DevTools 监听端口…` | Chrome 151+ 废弃了 junction 方案，不再有 debug 端口 | **正常现象**——`login_cdp_fetch.py` 会自动回退到 `profile_clone_fetch`。若未自动回退，手动跑：<br>`python scripts/profile_clone_fetch.py "<URL>"` |
| `[fallback] CDP 不可用，回退到 profile_clone_fetch` | 正常行为 | 脚本自动 kill Chrome → 同步 cookie 到 ProfileClone（首次全量复制，后续秒级）→ 启 headless Chrome → 抓取 → 用户重开 Chrome |
| `[FAIL] Chrome 还在跑，user-data-dir 被锁` | Chrome 没完全退出 | 脚本会自动 kill Chrome；如手动跑 profile_clone_fetch 则先 taskkill |
| `[3/3] body_chars 很小 + login_wall 命中` | 用户在 scys 没登录，或登录态过期 | 确认 Chrome 已登录 scys（打开 scys 看页面是否已登录），然后重跑 |
| 页面空白 / 长白雪 | SPA 还没渲染完 | 脚本默认等 8s；在 `fetch()` 调 `wait_ms=` 调大（如 15000） |
| `ModuleNotFoundError: playwright` | 缺包 | `D:\App\anaconda3\python.exe -m pip install playwright` |

---

## 5. 实证状态（诚实标记 · 遵循通用文档 §10 沉淀纪律）

- **机制**：L3（设计如此，通用文档 §3 描述）。
- **scys 案例**：
  - 2026-08-19 21:18 会话用本流程跑通 —— 落盘 `scys_article.md`（49 226 字节 / 正文 **21 264 字** / 无登录墙）。
  - **2026-08-24 最新验证**（L1）：Chrome 151+ junction 废弃 → profile_clone_fetch 持久化 ProfileClone → scys 抓取 **21265 字无登录墙**。首次全量复制 ~16GB，后续只同步 9 个 cookie 文件（18.9 秒含 Chrome 启动+抓取）。详见 `docs/decisions/DECISION-20260824-chrome151-junction-deprecation.md`。

---

## 6. 不要再做的事（防漂移）

- ❌ **不要再用项目根的临时脚本 `scys_fetch.py`** —— 它硬编码了 URL 和输出路径，已被 `scripts/login_cdp_fetch.py` 取代（2026-08-20 已删除）。
- ❌ 不要手搓 cookie 导出 / 抓登录 API / 重新登录。
- ❌ 不要把「PW」理解成密码 —— 本项目语境 **PW ≡ Playwright**。
- ❌ 不要以为 Edge 能代替 Chrome 抓 scys（Edge 无该站登录态）。

---

## 7. 批量抓取「项目标签」全部帖子（2026-08-20 新增 · 已验证 · 参数化）

**入口**：`python scripts/scys_batch_fetch.py --project <项目名> [--limit M]`

- **可抓领域 / menuId 在 `scripts/scys_projects.json` 配置**（不在代码里）：projects（领域名→menuId）+ defaults（since_days=548 一年半 / digested_only=false 含非精华 / batch_limit=30 / min_reading）。**换领域、每半年调整时间窗、改批量大小都只改这个 JSON，脚本零改动。**
- 新领域 menuId 捕获方法：tags 页真实点击该标签 → DevTools Network 里 `searchTopic` 请求的 `menuId` 参数（或 `--list-projects` 查看已配置项）
- 命令行参数可临时覆盖配置：`--since-days` / `--digested-only`（反向：仅精华）/ `--min-reading` / `--limit`
- 机制：tags 页真实点击翻页捕获 `searchTopic` 响应（不手搓 API 请求）→ 按 `isDigested`+阅读数排序 → 逐篇 goto 正文页 DOM 抓取
- 自动处理：站内 `articleDetail` 引用（前情提要）递归抓一层；外部知识库（飞书/语雀等）滚动到底抓全文；登录墙检测
- **限速（模拟人类）**：篇间随机 15~40s；每 10~15 篇歇 3~8 分钟；翻页间 3~6s；CDP 页面被关自动重试（实测救回过）
- 断点续传：`notes/_scraped/scys/state.json`；中断重跑自动跳过已抓（关机/杀进程都不丢进度）
- 产物：原文 `notes/_scraped/scys/<topicId>.md`；队列 `pending_summaries.json`（总结后标 `summarized:true` 防重复落飞书）
- 总结落盘：子 Agent 读原文 -> **走正规入口获取分类器选定的模板**（`python get_note_prompt.py <raw> <title> --ext <ext_files>` 输出 note_type + prompt_file）-> 按 prompt（含 `QUALITY_GATE_SELFCHECK` 质量自检闸门）总结 -> `python articles/_save_summary.py <md> --url ... --tags "生财有术,<项目>" --title ...`（默认飞书）。⚠️ **不要全部用 structured 模板**：分类器会按内容自动选 structured/interview/opinion/case/roundup/key_points/reading 七种模板。
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

**默认参数**（未提及即用 `scripts/scys_projects.json` 默认，当前=四领域全跑、一年半、精华+高互动非精华、每批 30 篇，顺序 AI产品开发→小程序→出海→自媒体）。

**条件后缀**（自然语言，模型解析成 CLI 参数临时覆盖默认，可任意叠加）：

| 用户说法 | 模型执行 |
|---|---|
| 补齐scys / 补齐生财有术 | 按默认四领域逐个跑 |
| 补齐scys 出海 ｜ 出海+自媒体 | 只跑指定领域（可多个，+号或顿号连接） |
| 补齐scys 近一年 / 近半年 / 近两年 | `--since-days 365 / 182 / 730` |
| 补齐scys 全部时间 | `--since-days 0` |
| 补齐scys 仅精华 / 只要精华 | `--digested-only`（反向开关：非精华全不抓） |
| 补齐scys 阅读过万 / 破万也抓 | `--min-reading 10000`（阅读门槛叠加在互动门槛之上） |
| 新领域（配置里没有） | 先按 §7 捕获 menuId → 写进 `scys_projects.json` → 再跑 |

**非精华高价值判定（2026-08-21 用户决策，同日改为默认）**：阅读数和点赞都会被官方指南/运营帖污染（全站推送 → 阅读/赞虚高，如「航海报名倒计时」「课程上线通知」赞均过百），**投锚 coinCount 是真金白银的价值投票，判别力最强**。默认模式（`digested_only=false`）下：精华帖直通；非精华帖需 **锚 ≥ 30，或 赞 ≥ 80 且 锚 ≥ 10**（`coin_floor` 锚下限防官方帖：实测招募/报名/倒计时帖赞 288~343 但锚仅 0~6，没人抛锚的「高赞」就是推送灌出来的）。阈值在 `scys_projects.json` 的 `nondigested_min_coin/min_like/coin_floor`，校准依据：精华锚 P50=61/赞 P50=169，阈值≈中位一半。觉得抓多/抓少改配置即可，无需动代码。

**执行闭环（模型每批照做）**：
1. 后台跑 `D:\App\anaconda3\python.exe -u scripts/scys_batch_fetch.py --project <领域> --limit 30`（断点续传，重复执行幂等，已抓自动跳过）
2. 每批完成 -> 派**子 Agent**（>3 篇必须拆子 Agent）总结落飞书（tags=`生财有术,<领域>`，入口 `articles/_save_summary.py`，详见 §7 总结落盘段）。⚠️ 子 Agent **必须走正规入口**：先调 `get_note_prompt.py` 获取分类器自动选定的模板 prompt（含 `QUALITY_GATE_SELFCHECK`），按该模板总结。**不要全部用 structured 模板**。
3. 总结完把 `pending_summaries.json` 对应条目标 `summarized:true`（防重复落飞书）
4. 向用户汇报累计/剩余进度，然后启动下一批

**半年重抓**：直接说「补齐scys」即可——`state.json` 断点续传自动只抓上次之后的新帖/漏帖；要扩时间窗加时间后缀（如「补齐scys 近两年」）。

**首轮补齐完成记录（2026-08-21）**：四领域 180 篇全部抓取+总结落飞书（生财有术/<领域> 容器），队列 180/180 出队。飞书外链问题帖的用户决策（勿再追问）：**PDF 预览型 1 篇与 404 一篇不抓**；截断 wiki 中仅 2 篇用 `feishu_ext_refetch.py` 补全（小红书虚拟店铺 SOP、YouTube 150万订阅复盘，飞书存「（完整版）」笔记），其余截断篇用户已看过/不需要。

**分类修复记录（2026-08-22）**：发现 scys boilerplate「AI问答」导致分类器将全部 309 篇误判为 interview（子 Agent 首轮全用了 structured 模板）。修复方案：从 INTERVIEW_KEYWORDS 移除「问答」、从 KEY_POINTS_KEYWORDS 移除「分享」（均因过于泛化）。修正后 65 篇应为非 structured 模板（case 17/opinion 16/key_points 15/interview 10/roundup 6/reading 1），已删除飞书旧节点并按正确模板重新总结落盘。子 Agent 改为走正规入口（`get_note_prompt.py` 获取分类器选定的模板 + `QUALITY_GATE_SELFCHECK` 质量自检闸门）。回归测试：`tests/test_scys_classification.py`（8 新 + 38 旧全通过）。详见 `docs/decisions/DECISION-20260821-scys-classification-fix.md`。
