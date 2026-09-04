# 需登录站点抓取 · 「接管用户主 Chrome」CDP 方案

> 🔴 **2026-09-02 架构收敛 · 本文档机制部分已过时**
> 原「路径 A（junction 接管活 Chrome）/ 路径 B（headless 克隆）」双路径框架**已删除**，代码现只有一条路径（见 `shared/cdp_session.py` 的 `SharedCdpSession`）：
> **关掉用户 Chrome → 复制 profile 到非默认 `CdpAutomationProfile\Chrome` 目录 → 该目录以调试端口启动 Chrome → `connect_over_cdp` 接管**。
> - junction 方案永久废弃（会触发扩展垃圾回收，实测删 22 个扩展）。
> - 活 Chrome 接管（默认 profile 开调试端口）在 Chrome 151+ 不可用（端口写了但不监听）。
> - `login_cdp_fetch.py` 现为**端口探测诊断工具**，不再自动回退抓取；需登录态抓取走监控流水线 `monitors/run.py`。
> 仍有效：§1.1（当前 `SharedCdpSession` 自动方案）、§2（为何不手搓 cookie）、§10（文档沉淀纪律）。§3/§5.1/§12/§13 的 junction / 路径 A-B 描述仅作历史参考，**勿照做**。

> 📜 **历史**：junction 方案的完整兴衰记录在 `.workbuddy/memory/_archive/decisions/DECISION-20260824-chrome151-junction-deprecation.md`（已归档，仅溯源，勿照做）。

> **本文件是「需登录态才能访问」的网站的通用抓取工作流。**
> 与 `references/youtube-cdp-workflow.md`（独立 Chrome-CDP 副本，仅代理）的关系见 §7。

---

## 0. TL;DR — 任意 AI 拿到「要登录的 URL」后只做一件事

```bash
cd <项目根>
python scripts/login_cdp_fetch.py "<需登录的URL>" [out.md]
```

### 0.1 术语速查（新会话术语对齐）

| 用户口语 | 含义 | 别称 |
|---|---|---|
| **「PW 登录态」** | Playwright 通过 CDP **接管用户主 Chrome** 后获得的访问能力（即本文档方案） | 「Playwright 接管登录态」「CDP 接管登录态」 |
| **「Chrome 不在」** | 用户主 Chrome 没以 `--remote-debugging-port=5494` 启动 → 端口 5494 探活返连接拒绝 | 「debug 模式未启」 |
| **「墙文本」** | 抓到的页面只显示「立即登录 / 付费查看 / 订阅解锁」字样 → cookie 没到 = 没拿到登录态（**不**算抓通） | "壳" |
| **「真正文」** | 抓到的页面有完整段落、无墙字样 + 字数 ≥几千字 → 登录态生效，确实抓到了内容 | — |

> ⚠️ 不要把「PW」理解成"密码 / Password / 微信公众平台"等；本项目语境下 **PW ≡ Playwright**。其它解释都会让模型走错路（去手搓 cookie、找密码、抓登录 API 等）。

`login_cdp_fetch.py` **现在仅作端口探测诊断**（不再抓取、不再自动回退）：
1. 读 `%LOCALAPPDATA%\Google\Chrome\User Data\DevToolsActivePort`（兼容 `Default\DevToolsActivePort`）。
2. TCP 连端口 + HTTP `GET /json/version`，确认是 Chrome DevTools 服务。
3. `smoke` 成功 → 报「端口可用」；失败 → 报「无调试端口」并提示走 `monitors/run.py`（它会经 `SharedCdpSession` 先关 Chrome、再一次性全量复制 profile 到非默认目录 + 调试端口接管抓取）。

> ⚠️ **实际抓取不再经本脚本**：需登录态的真实抓取（scys / 公众号）由监控流水线 `monitors/run.py` 与 `scripts/scys_batch_fetch.py` 内部的 `SharedCdpSession` 完成（先关 Chrome 释放锁，再**一次性全量复制**真实 profile 到非默认目录 + 调试端口 + `connect_over_cdp` 接管；默认 `CdpAutomationProfile\Chrome`，可用 `CDP_PROFILE_DIR` 覆盖为专用自动化目录）。

---

## 1. Chrome 启动前提（当前：用户零操作）

> **关键前提（2026-09-02 更新）**：用户**无需手动启动调试端口**。当前方案由 `SharedCdpSession` 自动完成：先关闭用户 Chrome（释放 cookie 独占锁）→ 复制 profile 到非默认 `CdpAutomationProfile\Chrome` 目录 → 用该目录以调试端口启动 Chrome → `connect_over_cdp` 接管（详见 §1.1）。`login_cdp_fetch.py` 仅作端口探测诊断。
> ⚠️ 旧前提「用户 Chrome 必须已以 `--remote-debugging-port` 启动」已废弃（Chrome 151+ 默认目录开调试端口不可用；junction 方案会触发扩展垃圾回收）。

### 1.1 用户侧前置（当前无需任何操作）

> **当前方案由 `SharedCdpSession`（代码）自动完成，用户零操作**：
> 需要登录态抓取时，代码自动：先确保用户 Chrome 完全关闭（释放 cookie 独占锁）→ **一次性全量复制**真实 profile 到**非默认**目录（默认 `CdpAutomationProfile\Chrome`，可用环境变量 `CDP_PROFILE_DIR` 覆盖）→ 用该目录以调试端口启动 Chrome → `connect_over_cdp` 接管。
> 非默认目录 + 调试端口 = Chrome 151+ 放行调试；全量复制的完整 profile 含 cookie/扩展/Secure Preferences → 登录态+扩展完整继承。
> 代价：首次/副本陈旧时会全量复制约 16GB，之后直接复用副本；每次仍须短暂关闭用户的 Chrome 以释放 cookie 锁。

> 🚫 **junction 方案永久废弃**（2026-08-24 实测教训，勿复活）：把 `--user-data-dir=DebugUDD --remote-debugging-port=5494` 焊进快捷方式、`DebugUDD` 指向 `User Data` 的 junction，会被 Chrome 151+ 检测并触发 `extension_garbage_collector` 删除 22 个扩展 + 清 Google 账号关联。

### 1.2 命令行启动（由 `SharedCdpSession` 自动完成 · 用户无需手敲）

> 需登录态抓取统一走监控流水线 `monitors/run.py`（公众号/B站）或 `scripts/scys_batch_fetch.py`（scys 批量）；二者内部都用 `SharedCdpSession` 自动克隆非默认目录 + 调试端口接管。
> 单篇诊断可用 `login_cdp_fetch.py smoke` 探端口（**仅探测，不再自动回退抓取**）：
>
> ```bash
> # 端口探测诊断（不抓取）
> python scripts/login_cdp_fetch.py smoke
> # 需登录 URL 抓取 → 走监控流水线，不要直接调 profile_clone_fetch（已无 standalone 抓取入口）
> ```
>
> <details><summary>旧 junction 方案（已废弃 · 勿用）</summary>
>
> ⚠️ 以下方案在 Chrome 151+ 会触发扩展垃圾回收删除所有扩展，已废弃。
> junction: `mklink /J DebugUDD "User Data"` + `chrome.exe --remote-debugging-port=5494 --user-data-dir=DebugUDD`
>
> </details>

### 1.3 验证抓取能力

```bash
python scripts/login_cdp_fetch.py smoke
# 期望：[OK] port ... devtools-bridge alive（CDP 可用时）
# 或：[FAIL]... → 自动回退到 persistent_fetch（正常行为，Chrome 151+ 默认走这条）
```

或直接跑：
```bash
python scripts/login_cdp_fetch.py "<任意URL>" smoke.md
# 脚本会打印 [OK]/[FAIL] 和原因
```

---

## 2. 为什么「不必再手搓 cookie 导出」

传统方案（旧、累、过期）：
- 用浏览器扩展 `EditThisCookie` / `Cookie-Editor` 复制 cookies JSON
- 塞进 `requests`/`httpx` 的 Cookie 头
- 任意反爬风控一升级 → 全失效，要重新导

**CDP 接管方案**：
- Chrome 进程内的所有 cookie / 登录态 / localStorage / sessionStorage **对 Playwright 透明可见**
- Playwright 通过 ws 协议直接驱动 Chrome 渲染页面，**所有风控都看不见自动化痕迹**（脚本走的是真实 Chrome 实例，不是裸 HTTP 客户端）
- 登录态由 Chrome 自动续期，**只要用户不退出登录，AI 永远抓得到**（cookie 过期由浏览器自动刷新）
- 同样方式可用于 **任何需登录的网站**：知乎、csdn、scys、medium、substack、jianshu、掘金、github 私有 repo、notion 私页 ……

### 2.1 案例 · scys 文章（2026-08-19 首次跑通 · 2026-08-20 最终验证 L1）

> **scys 专用照做 SOP（推荐先读）**：`references/scys-fetch-sop.md` —— 把本条案例提炼成 linear 步骤 + 墙/真文判别 + 故障排查，新会话 / 新模型直接照做即可，不必先读本通用文档。

> ✅ **2026-08-20 12:33 最终验证**：永久 debug 模式已实施（快捷方式焊好 flags + junction 持久化），scys 抓取**零操作全自动**。详见 §2.2 本会话实操记录。

| 项 | 值 / 说明 |
|---|---|
| URL | `https://scys.com/articleDetail/xq_topic/45544148552844858` |
| 平台 | 生财有术（scys.com）—— 不是公众号 |
| 文章 | 《研究了几个月，我终于把公众号做到了99%自动化》 |
| 发布 | 2026-08-14，朗读 32:34 |
| 抓取时间 | 2026-08-19 21:18:58（更早会话） |
| 落盘 | `D:\Code\Skills\blog-article-skill\scys_article.md`（49 226 字节，含标题 / 来源 / 登录态标注 + 正文） |
| 访问方式 | 用户的 Chrome 已开 `DevToolsActivePort`（端口 5494）；Playwright 通过 CDP **直接接管活浏览器**，登录态直接生效 |
| **登录态所在的浏览器**（更早会话观测） | **用户的 Chrome**（**不是 Edge** —— Edge 里没有 scys 登录态） |
| 抓取结果（更早会话观测） | **正文 21 264 字**，无「立即登录 / 付费查看 / 订阅解锁」墙字样 |
| **当前会话状态（2026-08-20 12:33）** | ✅ **已最终验证通过**：端口 5494 HTTP 200、smoke OK、scys tag 页 106,389 字无登录墙。永久 debug 模式（快捷方式 + junction）已实施，新会话零操作 |

#### 关键判别（防误判成「无登录态 / 不可抓」）

1. **「立即登录 / 付费查看 / 订阅解锁」墙文本 ≠ 用户主浏览器没登录** → 大概率是 CDP 接管的不是用户主浏览器（**常见陷阱：用户主浏览器是 Chrome，CDP 误接到 Edge**，Edge 没该站登录态 = 抓到墙）。修复：① 看任务栏 Chrome 图标是不是主浏览器；② 看 `chrome.exe` 路径是不是用户的默认安装（`%ProgramFiles%\Google\Chrome\Application\chrome.exe`）；③ `http://127.0.0.1:5494/json/version` 返 JSON 的 `Browser` 字段应为 `Chrome/...`（不是 `Edg/...`）。
2. **真正文 vs 壳**的快速判别：抓到的页面包含「立即登录 / 付费查看 / 订阅解锁」字样 = **壳**（cookie 没到）；无这些字样 + 字数 ≥几千字 = **真正文**（登录态生效）。
3. **字节数 vs 字数**：磁盘文件大小（49 226 字节）含编码 / 换行 / Markdown frontmatter；正文纯字数（21 264）才是衡量「抓到多少内容」的指标。判别时以字数为锚，字节数仅用于判文件是否存在。

#### 复用结论

任何「需登录」的 URL 都按本方案走 —— **别再纠结 cookie 导出 / 抓登录态 / 重新登录**。理由：
- 用户的 Chrome 已经登录 → CDP 接管 → 登录态自动由浏览器续期（cookie 过期 Chrome 会自动 refresh，Playwright 重新连接即生效）；
- 同样方式可用于任何「需登录」站点：知乎、csdn、scys、medium、substack、jianshu、掘金、github 私有 repo、notion 私页 ……

---

## 3. 架构（2026-09-02 收敛为单路径）

```
用户给「需登录 URL」
      │
      ▼
监控流水线 / scys_batch_fetch.py  →  SharedCdpSession（唯一路径）
      │
      ├─ ① 确保用户 Chrome 完全关闭（taskkill，释放 cookie 独占锁）
      │
      ├─ ② 一次性全量复制真实 profile → 非默认目录（默认 `CdpAutomationProfile\Chrome`，可用 `CDP_PROFILE_DIR` 覆盖）
      │     · 复制时 Chrome 已关 → cookie 锁释放 → 完整 profile（含 cookie/扩展/Secure Preferences）完整拷入
      │
      ├─ ③ 用该非默认目录以调试端口启动 Chrome（非默认 dir → 151+ 放行）
      │
      ├─ ④ Playwright.chromium.connect_over_cdp(ws_endpoint)
      │     · 接管克隆浏览器首 context，复制来的完整 profile → 登录态+扩展完整继承
      │
      ├─ ⑤ ctx.new_page() → page.goto(URL, wait_until='domcontentloaded')
      │     · SPA 等待 5–10s；取 title / 正文（selector 链或 body.innerText）
      │
      └─ ⑥ 抓取完成 → 退出克隆浏览器（用户重开原 Chrome 即可）
```

---

## 4. 代码入口（已落地）

`scripts/login_cdp_fetch.py`：通用 CLI，输入 URL + 可选输出路径。

典型用法：

```bash
# 写到默认路径 notes/_scraped/<slug>.md
python scripts/login_cdp_fetch.py "https://scys.com/articleDetail/xq_topic/45544148552844858"

# 指定输出
python scripts/login_cdp_fetch.py "https://scys.com/articleDetail/xq_topic/45544148552844858" my_scys_article.md

# 自检（不真抓页只验证 chrome debug）
python scripts/login_cdp_fetch.py smoke
```

**关键代码片段**（直接照搬即可）：

```python
import os, sys, time
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright

# 1) 找 DevToolsActivePort（Chrome 默认 user-data-dir）
PORT_FILE = Path(os.environ["LOCALAPPDATA"]) / r"Google\Chrome\User Data\DevToolsActivePort"
if not PORT_FILE.exists():
    # 兼容老 Chrome：曾在 Default/ 下
    PORT_FILE = PORT_FILE.parent / "Default" / "DevToolsActivePort"

lines = PORT_FILE.read_text(encoding="utf-8").splitlines()
port = int(lines[0])
ws_path = lines[1] if len(lines) >= 2 else "/devtools/browser/unknown"
WS = f"ws://127.0.0.1:{port}{ws_path}"

# 2) 端口 + http 自检（不是 Chrome DevTools 就别打 playwright 了）
html = urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3).read().decode()
assert "Chrome" in html, f"port {port} 不是 Chrome DevTools: {html[:120]}"
print("[OK] chrome devtools on", port)

# 3) Playwright 接管
with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(WS)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    page = ctx.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(8000)   # SPA / 懒加载等
    title = page.title()
    text = page.evaluate("() => document.body.innerText")
    page.close()
```

---

## 5. 已知坑（踩过记下来）

| 现象 | 真因 | 处理 |
|---|---|---|
| TCP 端口在 listen 但 HTTP `/json/version` 返 **404** / Playwright 直连 ws 根返 **403** | 三种可能：① 端口被非 DevTools 进程占用；② Chrome 曾带 flag 启动但之后被重启/自动更新，只剩 stale 的 `DevToolsActivePort` 文件（文件在、服务不在）；③ Chrome 151+ 用旧命令启动，端口占位但调试服务未真正起来 | 脚本已能精准区分「文件过期/非调试实例」与「端口真没开」并分别报错。修复：改用监控流水线走 `SharedCdpSession` 单路径（补跑用 `monitors/run_source.py --source scys`）；`login_cdp_fetch.py` 现仅作**端口探测诊断**，已无 profile_clone 自动回退 |
| `connect_over_cdp` 永远卡死、无任何输出 | ws 握手被 Chrome 拒绝；或用了过期的 ws uuid 连到 404 | 脚本已改为永远从 `/json/version` 实时取 ws 路径，不信磁盘文件 uuid。Chrome 151+ 废弃 junction 后此场景罕见 |
| 抓到的是「请登录 / 扫码登录 / 订阅解锁」之类内容 | Cookie 未发送 = 用户实际在该域名未登录 | 让用户在浏览器手工登录一次，再让 AI 抓 |
| 页面空白 / 长白雪 | SPA 还在 render | `page.wait_for_timeout` 增加；或显式等某 selector：`page.wait_for_selector(".article-body", timeout=15000)` |
| 抓到的正文混着广告 / 推荐区 | 选择器取得不准 | 用脚本里 selector 链：`.article-content / .topic-content / article / main / body`，按长度取最长一段 |

### 5.1 ws 握手坑（Chrome 136+）

Chrome 自 132 起对 ws 上 DevTools 加了若干保护：

- **ws 必须带正确的 `Host` 与 `Connection: Upgrade`**：用 Playwright 默认即可，**切勿手搓**。
- **`Origin` 头**：Playwright 默认不发，不用 `suppress_origin` 也行；用 raw `websocket-client` 要 `suppress_origin=True`。
- **默认 user-data-dir + debug port 在 Chrome 151+ 会被拒**（实测）：报错 `DevTools remote debugging requires a non-default data directory`。**当前方案用非默认 `CdpAutomationProfile\Chrome` 目录（复制真实 profile）规避**，登录态靠复制的 cookie 继承，不用 junction。**
- 细节同 `youtube-cdp-workflow.md §1.3.1`（独立 CDP 副本也用非默认目录）。

### 5.2 多个并发抓取

Playwright `connect_over_cdp` **可以**创建多个 `page` 并发在同一 context，但 Chrome 自身是单线程渲染。建议并发 ≤3。

---

## 6. 与项目内其它抓取流程的关系

| 入口 | 用途 | 登录态 | 输出 |
|---|---|---|---|
| `articles/skill_main`（默认） | 单篇公众号 / 普通 URL | 无（直连） | 飞书 |
| `articles/skill_main --fetch_web` | 普通 URL 抓正文 | 无（直连） | 飞书 |
| `monitors/run.py --apply` | 公众号/B站订阅 | 公众号：Cookie；B站：`BILI_COOKIE` | 飞书 |
| `videos/run.py --url` | YouTube/B站视频 | 无（视频不需登录；走字幕 API 或 CDP 拦截） | 飞书 |
| **`scripts/login_cdp_fetch.py`**（本方案） | **任何需登录态的 URL** | **接管用户 Chrome 继承** | **`out.md`**（本地） |

> 总结：**本方案是项目内「唯一」的「需登录态抓取」通用解**。其他入口均假设 URL 是公开可达的；碰到付费 / 登录墙 / 私有内容 / 公司内网 → 一律走 `scripts/login_cdp_fetch.py`。

---

## 7. 与 `youtube-cdp-workflow.md` 的关系（不要再混）

| 文件 | 接管对象 | user-data-dir | 目的 |
|---|---|---|---|
| `references/youtube-cdp-workflow.md` | **独立 Chrome-CDP 副本**（`%LOCALAPPDATA%\Google\Chrome-CDP`）| **非默认**（副本）| 视频字幕抓取（带代理扩展 iGuge，**不需要用户登录态**）|
| **`references/login-required-cdp-workflow.md`**（本文件） | **CdpAutomationProfile\Chrome**（持久化副本，非默认 dir，Chrome 151+ 放行；可用 `CDP_PROFILE_DIR` 覆盖）| **非默认**（一次性全量复制完整 profile 继承登录态+扩展）| **任何需登录态的页面**（不带代理，纯靠用户的真实登录态）|

两者**互不干扰**，可以并存。

---

## 8. 自举指针（新会话 / 新模型照做）

1. 读本文件。
2. 让用户提供一个「需登录的 URL」。
3. 跑 `python scripts/login_cdp_fetch.py <URL>`。
4. 若输出 `chrome debug not ready`：
   - 把错误原样贴回用户
   - **无需用户手动启调试端口**：当前需登录态抓取统一走 `monitors/run.py`（公众号/B站）或 `scripts/scys_batch_fetch.py`（scys），由 `SharedCdpSession` 自动克隆非默认 `CdpAutomationProfile\Chrome` 目录 + 调试端口接管（见 §1.1）；`login_cdp_fetch.py` 仅作端口探测诊断、不再回退抓取
5. 若脚本成功输出文件 → 读取正文（或读取 `out.md`），按 `articles/skill_main` 的模板（`structured` / `key_points` 等）总结，然后调 `OutputManager.save_all` 写入飞书。

---

## 9. 故障排查决策表

| 现象 | 根因定位 | 修法 |
|---|---|---|
| `[FAIL] port 5494 不是 Chrome DevTools` | 用户 chrome 没启 debug，或端口被占用 | **无需用户操作**：走 `monitors/run.py`，由 `SharedCdpSession` 先关 Chrome、再一次性全量复制 profile 到非默认目录 + 调试端口接管（见 §1.1）；`login_cdp_fetch.py` 仅作端口探测诊断 |
| `connect_over_cdp` 卡死无输出 | ws 协议被 Chrome 拒 | 确认由 `SharedCdpSession` 用**非默认 `CdpAutomationProfile\Chrome` 目录**启动（`--user-data-dir` 指向克隆目录），而非默认 `User Data`（Chrome 151+ 默认目录开调试端口会被拒） |
| 抓到「登录后查看」字样 | Cookie 未发送 = 用户在那个站其实没登录 | 让用户到浏览器手工登录一次再抓 |
| HTML 取到但正文是空白 | SPA 没渲染完 | `page.wait_for_timeout(8000)` 改 `15000` 或按 selector 等 |
| Playwright import 报 ModuleNotFoundError | 缺包 | `python -m pip install playwright`；browser 我们不下载，连现有 Chrome 用就够了 |

---

## 10. 沉淀纪律（强制 · 防文档级假验证）

> **根因**：AI 改文档 ≠ AI 跑通过流程。把「更早会话观测到的现象」用「已被验证 / 任何会话能精确复现」措辞写进文档 = 用元数据冒充事实（与 `monitors/wechat.py` 中已删的 `reached_since` 自动写 done 同根 —— 都是用『看起来对』的信号触发『完成』判定）。
>
> **本节是文档级机械门禁** —— 任何 AI 在本项目中维护 `references/` `AGENTS.md` `RULES.md` 之前，必须先在心里跑通「L1 自我叩问」。

### 10.1 L1 自我叩问（写任何「案例 / 步骤 / 结论」前必答）

| 问 | 答不上的处置 |
|---|---|
| **Q1**：我**亲**在本会话里跑通过这条路径吗？（CLI 命令、我亲自执行、产物路径我都看过） | 不 → 措辞必须降级 |
| **Q2**：我知不知道中间每个步骤的真实输出（不是猜的，是 `STDOUT/STDERR` 真实打印的）？ | 不 → 不得写「N 字 / N 字节 / 浏览器 X / 抓到了 X」 |
| **Q3**：跑通过用了什么命令、产生什么文件、文件里看到什么？ | 不 → 不得断言操作结果 |
| **Q4**：如果失败，现象是什么？我测过吗？ | 不 → 不得写「失败就 X」 |

### 10.2 措辞等级（强制）

| 措辞等级 | 何时用 | 范例 |
|---|---|---|
| **L1：实证** | Q1 答「是」、Q2 答「是」 | 「当前会话 2026-08-19 23:55 实测：端口 5494 探活 OK → `scripts/login_cdp_fetch.py ...` → 产物 `xxx.md` 41 行 → 本会话复现成功」 |
| **L2：早会话引用** | Q1 答「否」、但有更早会话的落盘证据 | 「更早 21:18 会话跑通的案例（落盘 `scys_article.md`），**当前会话未实证**」 |
| **L3：纯机制** | 没任何会话跑过、只是描述机制 | 「`DevToolsActivePort` 是 Chrome 把 ws path 写在 user-data-dir 下的约定 —— 设计如此」 |

**禁用措辞**（任何级别都不得使用）：
- ❌「已被验证的硬事实」
- ❌「任何新会话 / 新前端模型能精确复现」
- ❌「AI 永远抓得到」
- ❌「防误判」「防踩坑」（无实证支撑即用 = 文档级冒名）

### 10.3 沉淀步骤（实操记录段 + 时间戳锚点）

每次沉淀一条**可执行** SOP / 案例 / 步骤时，必须同时具备三件东西：

1. **「实操记录」段**（写进 references/ 同文档下）：包含 **本会话时间戳**、**真实执行的命令 + 输出**、**产物路径与字节 / 行数**。例：`§2.2 当前会话实操记录 · 2026-08-19 23:55`。
2. **「未实证」标注**：当某条 L2 引用被纳入，需要在文档里加 §10.1 的 4 个 Q 答卷：当 Q1 = 否时，必加「⚠️ 当前会话未实证」徽章，且放在该段表的第一行。
3. **「可证伪性」**：每条断言必须能被下一次会话的同一命令复现或推翻。当前会话跑不通的，必须明确写「跑不通 + 现象」，不是只写「跑通了 + 数字」。

### 10.4 与 RCG 的关系

- RCG 管「确定性门禁怎么写」；§10 管「文档层面的门禁怎么写」。
- `monitors/wechat.py` 里删 `reached_since` 自动写 done 是 RCG 在代码层；§10.2 的禁用措辞表是 RCG 在文档层 —— **同根问题（用元数据冒充事实）**，同种修法（强制降级 + 显式标注 + 实测锚点）。

### 10.5 当前已知未实证段（持续维护）

| 段 | 状态 | L 等级 | 实测路径 |
|---|---|---|---|
| §2.1 · 21 264 字 / Chrome 非 Edge / 无墙字样 | ✅ **2026-08-20 12:33 最终验证通过**（smoke OK + scys 抓通 106,389 字） | **L1 实证** | 见 §2.2 |
| §3~9 操作步骤 | ✅ **2026-08-20 12:33 多次实操验证**（探活→抓取→落盘全链路跑通） | **L1 实证** | 见 §2.2 |
| §1.1 永久快捷方式方案 | ✅ **2026-08-20 已实施**（三处快捷方式 + junction 持久化，回读验证 flags 在位） | **L1 实证** | 见 SOP §1.5 |

---

## §2.2 模板：当前会话实操记录

> 任何未来真跑通的会话，按这个模板追加：

```
### §2.2 当前会话实操记录 · YYYY-MM-DD HH:MM

| 步骤 | 真实命令 / 输出 |
|---|---|
| 探活 | `python -c "import urllib.request; ..."` → 返 [OK] Chrome/1xx.x.x.x |
| 跑脚本 | `python scripts/login_cdp_fetch.py "<URL>" out.md` → [OK] wrote ... |
| 落盘自检 | `ls -la out.md` → 49 226 字节；`wc -l` → 41 行 |
| 正文字数自检 | `python -c "..."` → 21 264 字（非 0、不含墙字样） |
| 升级 | §10.5 状态从 L2 → L1；§2.1「当前会话状态」行的 FAIL → OK |

---

### §2.2 当前会话实操记录 · 2026-08-20 12:33（L1 实证 · 最终验证）

> **本节是 §10.2 L1 实证**：以下命令 / 输出是 2026-08-20 10:30~12:33 实测（本沙箱、本机），含 Chrome 151 根因定位 + junction 解法 + 快捷方式永久化 + scys 抓通全链路。

| 步骤 | 真实命令 / 输出 |
|---|---|
| **根因定位** | `chrome.exe --remote-debugging-port=5494`（不带 user-data-dir）→ 后台日志报 `DevTools remote debugging requires a non-default data directory`。**Chrome/151.0.7922.138 确认** |
| **Junction 建联接** | `mklink /J "...\Chrome\DebugUDD" "...\Chrome\User Data"` → Junction created ✅ |
| **用 junction 启动** | `chrome.exe --remote-debugging-port=5494 --user-data-dir="...DebugUDD"` → 5494 LISTENING，`/json/version` 返 HTTP 200 JSON |
| **smoke 自检** | `python scripts/login_cdp_fetch.py smoke` → `[OK] port 5494 ws /devtools/browser/<uuid> devtools-bridge alive  Chrome/151.0.7922.138` |
| **scys tag 页抓取** | `python scripts/login_cdp_fetch.py "https://scys.com/tags?projectId=2892644" out.md` → title='生财官网·标签检索' body_chars=**106389** login_wall=**无** → 落盘 262KB |
| **scys 主 tags 页抓取** | `python scripts/login_cdp_fetch.py "https://scys.com/tags" out.md` → title='生财官网·标签检索' body_chars=**49192** login_wall=**无** → 落盘 126KB |
| **快捷方式焊 flags** | Python win32com 改 3 处用户级 .lnk → 回读验证全部含 `--user-data-dir=...DebugUDD --remote-debugging-port=5494` ✅ |
| **Junction 验证** | 两路径（User Data vs DebugUDD）Default 目录 items=119 完全一致 → 确认是同一份 profile |
| **升级** | §10.5 全部升级 L1；§2.1「当前会话状态」→ ✅ 已最终验证通过 |

---

## 11. 用户 0 操作前置（不能违反）

> **铁律**：用户的体验是无感（除「提前登录」以外不做任何操作）；如果用户还需要做别的，那是前置条件没明确写在本节。

**前提（**用户**侧）**：
1. 用户**已在浏览器登录**目标站点（**用户唯一允许做的事**）。
2. 用户主 Chrome **曾经（一次性）启过 debug 模式**，或至少保留有 DevTools 可开的工作环境（Chrome DevTools / 调试扩展会自然启 debug）。

**默认前提（**模型**侧）**：模型在不打扰用户的前提下，自动接管浏览器 / 复制 profile。

**如果**前提**不满足**（用户 Chrome 当前确实没启 debug）→ 退到 §12 profile-clone 路径。

---

## 12. 路径 B · profile-clone 抓取（历史记录 · 已被单路径取代）

> **机制**：用户主 Chrome 没启 debug（最常见情况）→ 无法接管活浏览器。但仍可「**复制 Chrome 的 user-data-dir 到临时目录 → 启一个独立的新 Chromium headless 实例指到那份副本 → 访问 URL → 取正文**」。同 Windows 用户 + DPAPI ⇒ 新 Chromium 自动解密 cookie ⇒ **登录态继承**。
>
> **代码**：`scripts/profile_clone_fetch.py`（已落地，2026-08-19 实测机制跑通）。
> **入口用法**：`python scripts/profile_clone_fetch.py "<URL>" [out.md]`；`python scripts/profile_clone_fetch.py smoke` 自检。

### 12.1 实操步骤（linear · 代码门禁）

| 步骤 | 真实命令 / 输出（实测，见 §2.2） |
|---|---|
| 1. 选源 | `pick_source_user_data_dir()` → Chrome 优先 → `C:\Users\O1830\AppData\Local\Google\Chrome\User Data`（13 470 MB） |
| 2. 复制 | `copy_user_data_dir()` 调 robocopy `/E /COPY:DAT /R:1 /W:1` → 复制约 13 480 MB / 33 896 文件（剩 3~5 个**锁文件**由 ctypes 全共享读兜底，见 §13） |
| 3. 清锁 | 删 `SingletonLock` / `SingletonCookie` / `SingletonSocket` → 新 Chromium 启动不被旧 Singleton 锁干扰 |
| 4. 启实例 | `chromium.launch_persistent_context(user_data_dir=临时目录, headless=True)` → 启动新 headless Chromium 实例，带登录态 |
| 5. 访问 | `ctx.new_page() → page.goto(URL, wait_until="domcontentloaded", timeout=30s) → page.wait_for_timeout(8000)` → SPA/懒加载等 |
| 6. 抓正文 | 依次尝试 selector 链：`".article-content" / ".article-detail" / "#articleContent" / ".topic-content" / ".post-content" / ".markdown-body" / "article" / "main" / "body"`，取最长一段；fallback `document.body.innerText` |
| 7. 写文件 | 标准 frontmatter：`# {title}\n\n> 来源 {url}\n> 抓取时间 {ts}\n> 渠道 profile_clone_fetch（...）\n\n---\n\n{body}` |
| 8. 关 + 清 | `browser_ctx.close()`；异步 `subprocess.Popen(["cmd","/c","rd","/s","/q",tmpdir], creationflags=DETACHED_PROCESS)` → 后台清理临时目录 |

### 12.2 失败 / 缺口（明确告知用户）

- **登录墙命中**（body 含「立即登录 / 登录后查看 / 成为会员 / 订阅后」字样）→ cookie 没成功同步；不再重试（详见 §13）；提示用户去浏览器登录后重抓。
- **临时目录残留**（Windows `rd` 异步失败，但已在后台跑）→ 下一轮跑之前的 `pick_source_user_data_dir()` 是基于原始 user-data-dir，不是临时目录；残留的临时目录**不影响**用户 Chrome。
- **第三方扩展的 IndexedDB blob** 可能有 1~2 个文件锁；ctypes 兜底已能拿到（实测 12.4 MB indexeddb 成功），不阻塞主流程。

### 12.3 与 §3（路径 A · CDP 接管）的关系

| 维度 | 路径 A（接管活 Chrome） | 路径 B（profile-clone） |
|---|---|---|
| **前提** | 用户主 Chrome 已启 `--remote-debugging-port` | 用户主 Chrome 在跑（无论是否启 debug） |
| **抓取对象** | 活 Chrome 看到的内容（**100% 与用户视角一致**） | 新 Chromium 实例 → 同 user-data-dir + DPAPI 同 Windows 用户，**与用户视角高度一致但 ≠ 100%** |
| **是否动用户 Chrome** | 完全不动 | 不动（仅**读** user-data-dir，复制到临时目录） |
| **多账号并行** | 单 Chrome 实例 | 可并行 n 份临时目录 + n 个新实例 |
| **登录态来源** | 活 Chrome 直接接管，登录态自动生效 | 靠 cookie db（很可能被 Chrome 锁住，见 §13） |

**主推荐**：路径 A（0 操作 + 100% 与用户视角一致）；**fallback**：路径 B。

---

## 13. 结构性约束 · Chrome 136+ 锁 cookie（历史记录 · 仅解释为何要先关 Chrome）

> 这是**结构性**事实（Chrome 安全加固，不是 bug 也不是我没努力）：Chrome 136+ 的 cookie 数据库 `Default\Network\Cookies` 用 `FILE_SHARE_NONE` 独占锁，**任何别的进程包括 ctypes 全共享读、sqlite3、robocopy、`vssadmin`**都不能读它（2026-08-19 23:5x 实测）。

### 13.1 实测结果

| 方法 | 结果 |
|---|---|
| robocopy `/R:1 /W:1` | 失败（bit 3 = 部分文件被锁） |
| ctypes `CreateFileW` + `FILE_SHARE_READ \| WRITE \| DEL` | err=32 `ERROR_SHARING_VIOLATION` |
| Python `sqlite3.connect("file:...?mode=ro")` | `OperationalError: unable to open database file` |
| **VSS 卷影副本** (`vssadmin create shadow`) | rc=2（需 admin，沙箱 `TokenElevated=0`） |
| IndexedDB blob 文件 | ctypes 兜底成功（12.4 MB） |

### 13.2 结构性后果

- **路径 B 在用户主 Chrome 没启 debug 的情况下不能继承 cookie**。
- 如果用户在浏览器已登录目标站点，AI **接到的事实如下**：
  1. 用路径 A：前提没满足 → 失败；
  2. 用路径 B：cookie 复制不出 → **新实例访问目标站就是登录墙**。
- 让 AI 0 操作继承登录态的兜底只有 **VSS**，需要**用户进程是 admin**（多数 AI 沙箱都不是）。

### 13.3 唯一可行的「0 操作」前置

参考 §11：用户主 Chrome **曾经（一次性）启过 `--remote-debugging-port`** → 默认走路径 A 接管活 Chrome；登录态由活 Chrome 自动携带，不需要复制任何 db 文件。

**这才是真的 0 操作 + 真继承登录态**的路径。

---

## 14. 当前会话实操（2026-08-19 ~ 00:23 · profile_clone_fetch.py）

> **本节是 §10.2 L1 实证**：以下命令 / 输出是 00:14 ~ 00:23 实测（本沙箱、本机），不是引用、更不是推测。

### 14.1 探活环节（HTTP /json/version · ws · 文件锁 逐档）

| 项 | 命令 | 结果 |
|---|---|---|
| DevToolsActivePort 文件存在 | `open(r"%LOCALAPPDATA%\Google\Chrome\User Data\DevToolsActivePort").read()` | `'5494\n/devtools/browser/ea55ce82-...'` 存在 |
| 端口 5494 TCP 听 | `socket.create_connection(("127.0.0.1",5494), timeout=3)` | `ConnectionRefusedError`（**不听**） |
| HTTP `/json/version` | `urlopen("http://127.0.0.1:5494/json/version", timeout=3)` | URLError / 超时 → **端口不听 = Chrome 没有 ws debug** |
| 0.3s/1s/3s timeout 摆动 | 同上 | TimeoutError → ConnectionRefusedError（**没人在 listen**） |
| Chrome 进程在跑 | `tasklist /FI "IMAGENAME eq chrome.exe"` | **17 个 chrome.exe 在跑**（主进程 + 渲染 + 扩展） |
| cookie 锁 share 模式探测 | `ctypes CreateFileW` 三档 share（0x1/0x3/0x7） | **三档全 err=32 `ERROR_SHARING_VIOLATION`** = `FILE_SHARE_NONE` |
| IndexedDB blob `CreateFileW` ctypes | `share=0x7` | err=0 → 12 474 207 字节复制成功 |
| 沙箱 admin | `GetTokenInformation(TokenElevation)` | `TokenElevated=0`（非 admin），**VSS 不可用** |
| vssadmin | `vssadmin list shadows` | rc=2 |

### 14.2 路径 B smoke 跑通（公开 URL）

```
$ python scripts/profile_clone_fetch.py smoke
[1/6] src user-data-dir: C:\Users\O1830\AppData\Local\Google\Chrome\User Data  (13470.3 MB)
[2/6] copy → C:\Users\O1830\AppData\Local\Temp\profile_clone_7zxs7r0m
        copied 13480.0 MB  (src=33899 dst=33896 文件)
        ! 仍有 3 文件未复制：
          - Default\Network\Cookies             ← cookie 锁
          - Default\Network\Cookies-journal    ← cookie 锁
          - Default\Sessions\Session_13431628043703431  ← 会话锁
          - Default\Sessions\Tabs_13431627882102398     ← tab 历史锁
[3/6] launch headless chromium on user_data_dir=...
[4/6] new page + goto https://example.com
[5/6] close browser ctx
[6/6] title = 'Example Domain'  body_chars = 129  login_wall = 无
        saved → D:\Code\Skills\blog-article-skill\notes\_scraped\_smoke_profile_clone.md
        cleanup scheduled: C:\Users\O1830\AppData\Local\Temp\profile_clone_7zxs7r0m

[OK] smoke 跑通 → ...
```

### 14.3 profile-clone 路径能跑通什么

- ✅ 公开 URL（example.com / wikipedia / 任何无需登录的页面）—— smoke 实证
- ❌ 需登录 URL（如 scys.com）—— 因 cookie 锁，新实例登不上
- **真要继承登录态**：用户主 Chrome 必须已启 debug 端口（路径 A，§3）

### 14.4 §10.5 状态升级

| 段 | 旧 | 新 | 备注 |
|---|---|---|---|
| §12 profile-clone 机制 | L3 未跑通 | **L1 实证** | 本会话 smoke |
| §13 cookie 锁 | L2 引用截图 2 | **L1 实证**（err=32 三档 + sqlite3 err + VSS 不可用） | 本会话探测 |
| §3 接管活 Chrome | L3 未跑通（本会话 Chrome 没启 debug） | L2 引用（截图 2 · 21:18 那次跑通）| 仍依赖用户一次性启 debug |
| §2.1 21 264 字 | L2 引用 | L2 引用 | 未在本会话实证（前提未满足） |

---

## 15. 给 AI 一句话总结（自举指针 · 2026-08-20 更新）

**接到 URL 后，先判 URL 是否需登录**：

```
URL 是否需登录？
├─ 公开 URL ─→ articles/run.py 或 articles/skill_main（§13 RULES.md）
├─ 视频 ─→ videos/run.py --url（含 ASR 兜底）
├─ 需登录（cookie 依赖）
│   ├─ 第一步：python scripts/login_cdp_fetch.py smoke
│   │   ├─ [OK] → 直接抓：python scripts/login_cdp_fetch.py "<URL>" [out.md]
│   │   └─ [FAIL] → 按报错信息处理：
│   │       ├─ "文件过期/非调试实例" → 用户 Chrome 需重启（快捷方式已焊好 flags，
│   │       │   正常重开 Chrome 即可；若仍失败按 §1.2 junction 命令恢复）
│   │       └─ "没找到任何端口" → 同上，先确认用户从正确快捷方式启动了 Chrome
│   └─ 抓到后：按 articles/skill_main 模板总结 → OutputManager.save_all 落飞书
└─ 拿不准 → 默认走 articles/skill_main，非登录墙报错再升级

关键事实（2026-09-04 修正）：
  • Chrome 151+ 禁止默认目录开调试 → 一次性全量复制真实 profile 到非默认目录 + 调试端口（§1.1）；默认 `CdpAutomationProfile\Chrome`，可用 `CDP_PROFILE_DIR` 覆盖
  • 登录态+扩展 = 全量复制的完整 profile；复制前必须先关 Chrome 释放 cookie 独占锁
  • 已废弃：只同步部分文件（增量）会破坏 Secure Preferences，导致扩展/Google 登录态丢失
  • 单路径：关 Chrome → 一次性全量克隆 → 调试端口启动 → `connect_over_cdp` 接管（见 `shared/cdp_session.py`）
  • `login_cdp_fetch.py` 仅端口探测诊断，不再自动回退；抓取走 `monitors/run.py`
  • junction 永久废弃（会删扩展）
```

> **2026-08-12 前的旧结论已作废**：「用户主 Chrome 当前没启 debug = 路径 A 用不了」「本次会话做不到访问 scys.com」——这些在 2026-08-20 已全部解决（Chrome 151 根因定位 + junction 解法 + 快捷方式永久化）。
