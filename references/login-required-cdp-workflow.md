# 需登录站点抓取 · 「接管用户主 Chrome」CDP 方案

> **本文件是「需登录态才能访问」的网站的通用抓取工作流。**
> 与 `references/youtube-cdp-workflow.md`（独立 Chrome-CDP 副本，仅代理）的关系见 §7。
> 本方案直接接管用户的**主 Chrome**（同一 user-data-dir），自动继承全部登录态 —— **不再导 cookie、不再抓登录态、有效期内复用**。

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

脚本会自动：
1. 读 `%LOCALAPPDATA%\Google\Chrome\User Data\DevToolsActivePort`（若不存在则找 `Default\DevToolsActivePort` 兼容路径）。
2. TCP 连端口 + HTTP `GET /json/version`，**确认是 Chrome DevTools 服务**（不是被占着的随机服务）。
3. 用 Playwright `chromium.connect_over_cdp(ws_endpoint)` 接管。
4. 新开标签 → `page.goto(URL)` → 等待 SPA 渲染 → 读取正文 → 写入 `out.md`。
5. 抓完**不关**用户 Chrome，**不重启**浏览器，登录态保留。

若步骤 1/2 失败（端口被占 / Chrome 没在 debug mode）→ **清晰报错**「请先以 debug 模式启动 Chrome（详见 §1）」并停止；**不会**自作主张启新 Chrome。

---

## 1. 一次性的 Chrome 启动：用户必须做什么

> **关键前提**：用户的 Chrome 必须**已经**以 `--remote-debugging-port=XXXX` 启动。
> 不这么做 → 我方任何代码都连不上 Chrome。
> 不需要重启 Chrome —— 一次性加 flag 即可（保留所有标签 + cookies + 登录态）。

### 1.1 Windows：快捷方式改属性（推荐 · 已实施 ✅）

> **2026-08-20 更新**：用户已授权把调试 flags **焊进 Chrome 快捷方式**（见下方详情）。新会话/新模型**不需要再让用户手动改快捷方式**——直接 `smoke` 验证即可。若 smoke 失败，再按 §1.2 的 junction 命令恢复。

**已实施的永久方案（2026-08-20 用户授权）**：

三处用户级快捷方式均已植入 flags `--user-data-dir=C:\Users\O1830\AppData\Local\Google\Chrome\DebugUDD --remote-debugging-port=5494`：

| 快捷方式 | 路径 | 状态 |
|---|---|---|
| 任务栏 pin | `...\Quick Launch\User Pinned\TaskBar\Google Chrome.lnk` | ✅ |
| 快速启动栏 | `...\Quick Launch\Google Chrome.lnk` | ✅ |
| 桌面（新建兜底） | `C:\Users\O1830\Desktop\Google Chrome.lnk` | ✅ |

原快捷方式备份在 `C:\Users\O1830\AppData\Local\Temp\chrome_lnk_backup\`。

**效果**：用户正常双击/点任务栏开 Chrome → 自动带调试端口 + 继承登录态。Chrome 自动更新/重启后再开也自动恢复。

> ⚠️ 若某次 scys 抓不通且 smoke 报端口失败：优先检查是否从**公共开始菜单**（`ProgramData`，未改的那枚）或**旧缓存 AppID** 启动的 Chrome。从桌面那枚新建快捷方式重新 pin 任务栏即可。**不要让用户手动敲命令**——除非快捷方式被破坏或用户主动要求。

<details><summary>旧方法（仅 Chrome <151 或参考）</summary>

⚠️ **以下旧命令在 Chrome 151+ 会报错** `DevTools remote debugging requires a non-default data directory`。仅作历史参考，实际请用 §1.2 的 junction 方案：

1. 右键任务栏/桌面的 Google Chrome 图标 → **属性**
2. 「快捷方式」标签页 → 「目标」行加 `--remote-debugging-port=5494`
3. 先关闭所有 Chrome 窗口 → 再点改后的快捷方式

</details>

### 1.2 命令行启动（适合脚本场景）

> ⚠️ **Chrome 151+ 已改规则**：直接在**默认** user-data-dir 上开远程调试会被拒，报错
> `DevTools remote debugging requires a non-default data directory`。
> 所以**不能**再用「`chrome.exe --remote-debugging-port=5494`（不带 user-data-dir）」这种旧命令。
> 解法：建一个指向真实 profile 的**目录联接（junction）**，再拿这个「非默认路径」启动——
> 实际读写同一份 profile，登录态原样保留，但 Chrome 认为它是非默认目录而放行调试。

```bash
:: ① 建联接（一次性，无需管理员，重启后仍在）
mklink /J "%LOCALAPPDATA%\Google\Chrome\DebugUDD" "%LOCALAPPDATA%\Google\Chrome\User Data"
:: ② 用非默认目录启动调试 Chrome（仍指向你的真实 profile）
"C:/Program Files/Google/Chrome/Application/chrome.exe" --remote-debugging-port=5494 --user-data-dir="%LOCALAPPDATA%\Google\Chrome\DebugUDD"
```

> 联接建好后，以后只需跑第 ② 行即可。若 `DebugUDD` 联接已存在，`mklink` 会报「文件已存在」，忽略即可。

### 1.3 验证 Chrome 真的启用了 Debug

```bash
curl -s http://127.0.0.1:5494/json/version
# 期望回 JSON: {"Browser":"Chrome/...", ...}
# 若回 404 / 连不上 = 不是 DevTools 服务 / Chrome 没启 debug
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

## 3. 架构

```
用户给「需登录 URL」
      │
      ▼
scripts/login_cdp_fetch.py <URL>
      │
      ├─ ① 读 DevToolsActivePort → 拿端口 + ws path
      │
      ├─ ② 端口探活 + GET /json/version
      │     ├─ 失败 → 报「请按 §1 启动 Chrome」
      │     └─ 通过 → 继续
      │
      ├─ ③ Playwright.chromium.connect_over_cdp(ws)
      │     · ws 连接时 suppress_origin=True（Chrome 安全防）
      │     · 不创建新 context，直接接管首 context，登录态继承
      │
      ├─ ④ ctx.new_page() → page.goto(URL, wait_until='domcontentloaded')
      │     · SPA 等待 5–10s（按页面可能更长）
      │     · 取 title / 取正文（按 selector 链或直接 page.evaluate body.innerText）
      │
      └─ ⑤ 写到 out.md（标准 frontmatter + 正文）
            · 不动用户 Chrome（不关 browser 不关 page）
            · 退出 Playwright context（但不杀浏览器实例）
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
| TCP 端口在 listen 但 HTTP `/json/version` 返 **404** / Playwright 直连 ws 根返 **403** | 三种可能：① 端口被非 DevTools 进程占用；② Chrome 曾带 flag 启动但之后被重启/自动更新（Chrome 151+ 默认目录被拒后更常见），只剩 stale 的 `DevToolsActivePort` 文件（文件在、服务不在）；③ Chrome 151+ 用旧命令（不带 `--user-data-dir`）启动，端口占位但调试服务未真正起来 | 脚本已能精准区分「文件过期/非调试实例」与「端口真没开」并分别报错。修复：彻底关闭所有 Chrome → 按 §1.2 junction 命令重启 |
| `connect_over_cdp` 永远卡死、无任何输出 | ws 握手被 Chrome 因默认 user-data-dir + 同 Origin 拒绝；或用了过期的 ws uuid 连到 404 | 确认 Chrome 用了 `--remote-debugging-port=XXXX --user-data-dir=...DebugUDD` 启动（带这两个 flag 时 Chrome 151+ 才放行 ws）。脚本已改为永远从 `/json/version` 实时取 ws 路径，不信磁盘文件 uuid |
| 抓到的是「请登录 / 扫码登录 / 订阅解锁」之类内容 | Cookie 未发送 = 用户实际在该域名未登录 | 让用户在浏览器手工登录一次，再让 AI 抓 |
| 页面空白 / 长白雪 | SPA 还在 render | `page.wait_for_timeout` 增加；或显式等某 selector：`page.wait_for_selector(".article-body", timeout=15000)` |
| 抓到的正文混着广告 / 推荐区 | 选择器取得不准 | 用脚本里 selector 链：`.article-content / .topic-content / article / main / body`，按长度取最长一段 |

### 5.1 ws 握手坑（Chrome 136+）

Chrome 自 132 起对 ws 上 DevTools 加了若干保护：

- **ws 必须带正确的 `Host` 与 `Connection: Upgrade`**：用 Playwright 默认即可，**切勿手搓**。
- **`Origin` 头**：Playwright 默认不发，不用 `suppress_origin` 也行；用 raw `websocket-client` 要 `suppress_origin=True`。
- **默认 user-data-dir + debug port 在 Chrome 151+ 会被拒**（实测 2026-08-20，`Chrome/151.0.7922.138`）：报错 `DevTools remote debugging requires a non-default data directory`。与 `youtube-cdp-workflow.md §1.3.1` 中「起独立 CDP 副本不能用默认 user-data-dir」**现在本方案也必须用非默认目录**，差异只剩：
  - 独立副本 → 非默认 user-data-dir 且是**全新空目录**（无登录态，仅用于字幕抓取）
  - 本方案 → 非默认 user-data-dir 但用**指向真实 profile 的 junction**（继承登录态）
  - 👉 具体起法见 §1.2（junction 命令），旧「直接默认目录 + 端口」命令已失效。

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
| **`references/login-required-cdp-workflow.md`**（本文件） | **用户主 Chrome**（通过 junction `DebugUDD` 指向真实 profile）| **非默认**（junction，继承登录态）| **任何需登录态的页面**（不带代理，纯靠用户的真实登录态）|

两者**互不干扰**，可以并存。

---

## 8. 自举指针（新会话 / 新模型照做）

1. 读本文件。
2. 让用户提供一个「需登录的 URL」。
3. 跑 `python scripts/login_cdp_fetch.py <URL>`。
4. 若输出 `chrome debug not ready`：
   - 把错误原样贴回用户
   - 让用户按 §1 给 Chrome 加 `--remote-debugging-port=5494` 后再跑（一次性）
5. 若脚本成功输出文件 → 读取正文（或读取 `out.md`），按 `articles/skill_main` 的模板（`structured` / `key_points` 等）总结，然后调 `OutputManager.save_all` 写入飞书。

---

## 9. 故障排查决策表

| 现象 | 根因定位 | 修法 |
|---|---|---|
| `[FAIL] port 5494 不是 Chrome DevTools` | 用户 chrome 没启 debug，或端口被占用 | 让用户按 §1 启 Chrome（一次性加 flag） |
| `connect_over_cdp` 卡死无输出 | ws 协议被 Chrome 拒 | 确认 Chrome 启动命令行里有 `--remote-debugging-port` |
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

## 12. 路径 B · profile-clone 抓取（用户主 Chrome 未启 debug 时自动 fallback）

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

## 13. 结构性约束 · Chrome 136+ 锁 cookie（**实测**）

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

关键事实（2026-08-20 实测固化）：
  • Chrome 151+ 禁止默认目录开调试 → 必须用 junction（§1.2）
  • ws 路径永远以 /json/version 实时返回为准，不信磁盘文件 uuid
  • 快捷方式已焊好 flags → 新会话正常 smoke 即可，不需让用户手动敲命令
  • b.close() 会杀浏览器 → 接管用户浏览器时绝不能调 browser.close()
```

> **2026-08-12 前的旧结论已作废**：「用户主 Chrome 当前没启 debug = 路径 A 用不了」「本次会话做不到访问 scys.com」——这些在 2026-08-20 已全部解决（Chrome 151 根因定位 + junction 解法 + 快捷方式永久化）。
