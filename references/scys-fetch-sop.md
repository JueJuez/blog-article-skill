# 抓取 scys.com（生财有术）付费文章 · 实操 SOP

状态：现行 scys 操作说明。入口与零件以 [PIPELINES](../docs/PIPELINES.md) §2 / §7 为准，浏览器机制见 [CDP 工作流](login-required-cdp-workflow.md)。

## 0. 选择入口

```powershell
# 单篇总结归档（自动按 scys URL 分流）
python articles/run.py "<scys文章URL>"
# 单领域抓取，入待总结队列
python scripts/scys_batch_fetch.py --project <领域>
# 多领域或数小时补齐
python scripts/launch_scys_backfill.py
```

`FORCE_AGENT_MODE=1` 下抓取后仍需会话模型总结并通过门禁落盘，默认本地 Obsidian。队列写稿与保存走 PIPELINES §4 的 `save_pending.py`，成功后统一清队列；不要直接调用 `OutputManager.save_all` 绕过门禁。`login_cdp_fetch.py` 仅用于诊断，不作为单篇或监控生产入口。

## 1. 登录态与浏览器生命周期

用户须已登录 scys。生产入口经 `SharedCdpSession` 委托用户级 `cdp-automation-profile`：先探测健康的克隆调试实例并复用；端口失效先自愈，克隆缺失或过期才按技能内核重建。连接关闭只断开，不杀常驻实例；不承诺每次运行都关闭用户 Chrome 或重新复制 profile。

全量复制、marker、进程清理和端口编排以用户级技能内核为准。禁止增量复制和 junction；操作与故障表见 CDP 工作流 §1.1。scys 抓取和 weread 等源共享实例，批量任务串行安排浏览器使用。

---

## 2. 生产执行与诊断

生产总结按 §0 选择入口，读取返回的真实正文与 prompt，消费后通过门禁保存。批量抓取与队列消费见 §7–§9；确认成品路径、登记和队列清理后才报告完成。

诊断时可运行 `python scripts/login_cdp_fetch.py smoke` 查看调试端口，或传 URL 只抓原文；这是历史诊断工具，输出不代表已完成总结，也不保证自动启动共享实例。生产浏览器编排以 `SharedCdpSession` 为准。

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

- 连接失败：先看共享实例探测与端口自愈结果，再按 [CDP 故障表](login-required-cdp-workflow.md) 处理；不要每次手动强杀 Chrome。
- 登录墙：确认当前克隆实例是否已登录目标站点；正文壳不能进入总结。原始文件存在不证明抓取成功。
- SPA 空白或正文截断：检查渲染等待与正文提取结果；外链正文补抓见 §8，PDF 预览文本须单独处理。
- 配额、限流或浏览器占用：按入口的冷却、断点与串行约束暂停或续跑，不绕过保护。

---

## 5. 实证状态（诚实标记 · 遵循通用文档 §10 沉淀纪律）

- **机制**：L3（设计如此，通用文档 §3 描述）。
- **scys 案例**：
  - 2026-08-19 21:18 会话用本流程跑通 —— 落盘 `scys_article.md`（49 226 字节 / 正文 **21 264 字** / 无登录墙）。
  - **2026-09-02 最新验证**（L1，现行机制）：走 `shared/cdp_session.py` 的 `SharedCdpSession` **单路径**——关 Chrome → 复制 profile 到非默认 `CdpAutomationProfile\Chrome` → 该目录开调试端口启动 → `connect_over_cdp` 接管（登录态 + CDP 控制）→ 实测「出海」域捕获 30 篇（精华 25）无登录墙。机制细节与故障表见 `references/login-required-cdp-workflow.md`，本文档不重复叙述。

---

## 6. 不要再做的事（防漂移）

- ❌ **不要再用项目根的临时脚本 `scys_fetch.py`** —— 它硬编码了 URL 和输出路径，生产能力已由统一入口承接（2026-08-20 已删除）。
- ❌ 不要手搓 cookie 导出 / 抓登录 API / 重新登录。
- ❌ 不要把「PW」理解成密码 —— 本项目语境 **PW ≡ Playwright**。
- ❌ 不要以为 Edge 能代替 Chrome 抓 scys（Edge 无该站登录态）。

---

## 7. 批量抓取「项目标签」全部帖子（2026-08-20 新增 · 已验证 · 参数化）

**入口**：`python scripts/scys_batch_fetch.py --project <项目名> [--limit M]`

- **可抓领域 / menuId 在 `scripts/scys_projects.json` 配置**（不在代码里）：projects（领域名→menuId）+ defaults（since_days 默认见该 JSON，当前约 182 天 / digested_only=false 含非精华 / batch_limit=30 / min_reading）。**换领域、每半年调整时间窗、改批量大小都只改这个 JSON，脚本零改动。**
- 新领域 menuId 捕获方法：tags 页真实点击该标签 → DevTools Network 里 `searchTopic` 请求的 `menuId` 参数（或 `--list-projects` 查看已配置项）
- 命令行参数可临时覆盖配置：`--since-days` / `--digested-only`（反向：仅精华）/ `--min-reading` / `--limit`
- 机制：tags 页真实点击翻页捕获 `searchTopic` 响应（不手搓 API 请求）→ 按 `isDigested`+阅读数排序 → 逐篇 goto 正文页 DOM 抓取
- 自动处理：站内 `articleDetail` 引用（前情提要）递归抓一层；外部知识库（飞书/语雀等）滚动到底抓全文；登录墙检测
- **限速（模拟人类）**：篇间随机 15~40s；每 10~15 篇歇 3~8 分钟；翻页间 3~6s；CDP 页面被关自动重试（实测救回过）
- 断点续传：`notes/_scraped/scys/state.json`；中断重跑自动跳过已抓（关机/杀进程都不丢进度）
- 产物：原文 `notes/_scraped/scys/<topicId>.md`；队列 `pending_summaries.json`（成功登记后由 `filter_pending.py` 清队列）
- 总结落盘：子 Agent 读原文并消费队列中的预计算 prompt，写稿后运行 `python scripts/save_pending.py --queue scys --key <topicId> --file <稿件.md>`；成功保存不改队列，编排方统一跑 `filter_pending.py` 出队。默认本地 Obsidian，失败按门禁反馈重写。不要全部使用 structured：类型和分类优先级以 `prompts/templates.py`、`prompts/classify.py` 为准。
- 已知限制：飞书 **PDF 预览型**文档文字在 canvas 里抓不到（落盘文件头有页码碎片），此类需下载 PDF 另行处理；文字型 wiki 滚动方案有效
- python 环境：用 managed venv `C:\Users\O1830\.workbuddy\binaries\python\versions\3.13.12\python`（已带 playwright）；anaconda python 未必有

## 8. 与其他入口的关系（2026-08-20 更新：单篇 scys 已无感打通）

| 入口 | 用途 | 登录态 |
|---|---|---|
| `articles/skill_main` / `articles/run.py` | **任何文章链接（含 scys）**：`fetch_web_content` 检测到 `scys.com` 自动分流 CDP 登录态抓取，普通博客走 requests——**用户对模型说「总结这篇」即可，混合多篇也逐条自动分流** | scys 自动接管 Chrome；公开 URL 无 |
| `scripts/login_cdp_fetch.py`（本 SOP） | 显式单篇登录态抓取（诊断 / 非文章页） | 接管用户主 Chrome 继承 |
| `scripts/scys_batch_fetch.py`（§7） | 按项目领域批量抓 scys 帖子 | 同上 |
| `scripts/feishu_ext_refetch.py`（2026-08-21） | **飞书 wiki/docx 懒加载截断的全文重抓**：增量滚动 `.bear-web-x-container` 容器逐视口收集 innerText 按行去重合并。用法 `python scripts/feishu_ext_refetch.py "<飞书URL>" <out.md>`（用 anaconda python）。批量抓取的 ext 只有目录+开头（500 字级）时用它补；PDF 预览型（canvas 渲染）与 404 仍无解，跳过即可 | 同上 |
| `scripts/refetch_feishu_ext_batch.py`（2026-09-17） | **批量版**：复用上面 `collect_full_text` 一次重抓 N 条外链全文（实测 0.5–3 万字/篇，**推翻「飞书分享链接可见上限 ~2000 字」的旧误判**）。⚠️ URL 必须从 `<a href>` 或 `_ext_*.md` 文件头 `> 来源：` 取完整版——从正文显示文本正则提取会带 `...` 截断 → 飞书 404 | 同上 |
| `scripts/fetch_scys_by_ids.py`（2026-09-17） | **按 topicId 列表逐条补抓源**（绕过 dedup 闸门：已总结的帖子标准 `scys_batch_fetch.py` 会跳过）。`--list-md <清单md>` 正则提取 ID、`--no-external` 只抓主文；只写 `notes/_scraped/scys/<topicId>.md`，不入队不总结。**⚠️ 补源场景不要加 `--no-external`**（会把飞书外链正文丢掉） | 同上 |
| `monitors/run.py --apply` | 公众号 / B站订阅 | 公众号 Cookie / `BILI_COOKIE` |
| `videos/run.py --url` | 视频（YouTube / B站） | 无（字幕 API → CDP → ASR 兜底） |

> 总结：**单篇（含 scys）一律走 `articles` 入口**，模型自动分流；批量按领域抓 scys 走 `scripts/scys_batch_fetch.py`；两者都依赖用户主 Chrome 的 CDP debug 端口（前提见 §1/§1.5）。回归测试钉死该行为：`tests/test_scys_routing.py`（含混合链接场景）。

## 9. 触发词语义：「补齐 scys」（2026-08-20 定义）

用户说**【补齐scys】**或**【补齐生财有术】** → 模型自动执行批量补齐闭环，不用再问流程。

**默认参数**（未提及即用 `scripts/scys_projects.json` 默认，当前=七域全跑、一年半、精华+高互动非精华、每批 30 篇，领域与顺序见 `subscriptions.json` 的 `scys` 列表，当前：AI产品开发→小程序→出海→自媒体→AI自媒体→虚拟产品→垂直小号）。

**条件后缀**（自然语言，模型解析成 CLI 参数临时覆盖默认，可任意叠加）：

| 用户说法 | 模型执行 |
|---|---|
| 补齐scys / 补齐生财有术 | 按默认七域逐个跑 |
| 补齐scys 出海 ｜ 出海+自媒体 | 只跑指定领域（可多个，+号或顿号连接） |
| 补齐scys 近一年 / 近半年 / 近两年 | `--since-days 365 / 182 / 730` |
| 补齐scys 全部时间 | `--since-days 0` |
| 补齐scys 仅精华 / 只要精华 | `--digested-only`（反向开关：非精华全不抓） |
| 补齐scys 阅读过万 / 破万也抓 | `--min-reading 10000`（阅读门槛叠加在互动门槛之上） |
| 新领域（配置里没有） | 先按 §7 捕获 menuId → 写进 `scys_projects.json` → 再跑 |

**非精华高价值判定（2026-08-21 用户决策，同日改为默认）**：阅读数和点赞都会被官方指南/运营帖污染（全站推送 → 阅读/赞虚高，如「航海报名倒计时」「课程上线通知」赞均过百），**投锚 coinCount 是真金白银的价值投票，判别力最强**。默认模式（`digested_only=false`）下：精华帖直通；非精华帖需 **锚 ≥ 30，或 赞 ≥ 80 且 锚 ≥ 10**（`coin_floor` 锚下限防官方帖：实测招募/报名/倒计时帖赞 288~343 但锚仅 0~6，没人抛锚的「高赞」就是推送灌出来的）。阈值在 `scys_projects.json` 的 `nondigested_min_coin/min_like/coin_floor`，校准依据：精华锚 P50=61/赞 P50=169，阈值≈中位一半。觉得抓多/抓少改配置即可，无需动代码。

**执行闭环（模型每批照做）**：
1. **用 launcher 跑（每域独立 DETACHED 子，抗轮次回收）**（不要用 agent 的 `run_in_background` 跑数小时任务——它只是工具层后台，进程仍在会话进程树里，**轮次结束被回收**，见 `RULES.md` §4.7）：`python scripts/launch_scys_backfill.py [--project <领域>]`（**调度器自身非 DETACHED**，每域 spawn 独立 DETACHED 子进程、串行 wait；缺省补 `scys_projects.json` 全部领域，逐域串行）。单域调试也可直接 `python scripts/scys_batch_fetch.py --project <领域> --limit 30`——断点续传、重复执行幂等、已抓自动跳过。
2. 每批完成 -> 派**子 Agent**（>3 篇必须拆子 Agent）总结落盘（tags=`生财有术,<领域>`，入口 `articles/_save_summary.py`，详见 §7 总结落盘段）。⚠️ 子 Agent **消费队列中已算好的 prompt**：入队写点 `scripts/scys_batch_fetch.py:build_pending_entry` 已按分类器选定模板 + `QUALITY_GATE_SELFCHECK` 把 prompt 算好塞进队列条目，子 Agent 直接按该 prompt 总结，**无需自调任何 CLI**。**不要全部用 structured 模板**。
3. 总结完把 `pending_summaries.json` 对应条目标 `summarized:true`（防重复落盘）
4. 向用户汇报累计/剩余进度，然后启动下一批

**半年重抓**：直接说「补齐scys」即可——`state.json` 断点续传自动只抓上次之后的新帖/漏帖；要扩时间窗加时间后缀（如「补齐scys 近两年」）。

**首轮补齐完成记录（2026-08-21）**：四领域 180 篇全部抓取+总结落飞书（生财有术/<领域> 容器），队列 180/180 出队。飞书外链问题帖的用户决策（勿再追问）：**PDF 预览型 1 篇与 404 一篇不抓**；截断 wiki 中仅 2 篇用 `feishu_ext_refetch.py` 补全（小红书虚拟店铺 SOP、YouTube 150万订阅复盘，飞书存「（完整版）」笔记），其余截断篇用户已看过/不需要。

**分类修复记录（2026-08-22）**：发现 scys boilerplate「AI问答」导致分类器将全部 309 篇误判为 interview（子 Agent 首轮全用了 structured 模板）。修复方案：从 INTERVIEW_KEYWORDS 移除「问答」、从 KEY_POINTS_KEYWORDS 移除「分享」（均因过于泛化）。修正后 65 篇应为非 structured 模板（case 17/opinion 16/key_points 15/interview 10/roundup 6/reading 1），已删除飞书旧节点并按正确模板重新总结落盘。子 Agent **消费队列中已算好的 prompt**（入队写点 `scripts/scys_batch_fetch.py` 按分类器选定模板 + `QUALITY_GATE_SELFCHECK` 算好随条目投递，无需自调 CLI；2026-09-05 起三队列统一，见 `docs/decisions/DECISION-20260905-prompt-precompute-three-queues.md`）。回归测试：`tests/test_scys_classification.py`（8 新 + 38 旧全通过）。详见 `docs/decisions/DECISION-20260821-scys-classification-fix.md`。
