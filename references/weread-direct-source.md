# 微信读书直连源（wewe-rss 代理的替代方案）

> 2026-09-18 实测确立，**2026-09-19 列表接口打通**（见 §2.2 实验 2）。**磁盘/实测证据优先于
> 任何推断**——下表每一行的状态都是用本项目自己的微信读书登录态打出来的真实响应，不是从文档抄的。
> 背景：`weread.111965.xyz`（wewe-rss 的公开 platform 实例）已死 21 天，见
> `monitors/PROXY_NOTES.md` §9。本文件是它的**接替方案**。
>
> 📚 **外部资料（博客 / 开源项目 / 六条抓取路线 / 注意事项 + 原链接）见
> `references/wechat-mp-sources.md`** —— 本文件只放实测，出处与二手信息放那边；
> 两边冲突时**以本文件（实测）为准**。

## 1. 为什么第三方代理没救了（关键结论）

wewe-rss 默认 `PLATFORM_URL=https://weread.111965.xyz`，README 给的备用域名是
`weread.965111.xyz`。实测（2026-09-18）：

| 域名 | 解析 | 结果 |
|---|---|---|
| `weread.111965.xyz` | 127.0.0.1（**本机被 Steam++ 劫持**，真身 Cloudflare 172.67.173.210） | 绕过劫持后 `/`、`/api/v2/login/platform` 全 502 |
| `weread.965111.xyz` | 34.120.54.55（GCP，未劫持） | `404: Not Found (DEPLOYMENT_NOT_FOUND)` —— **Deno Deploy Classic was sunset on July 20, 2026** |

→ 两个域名是**同一套 Deno Deploy Classic 服务**，平台已于 2026-07-20 下线。
**换镜像域名无用，自建是唯一出路。**（wewe-rss 的 platform 服务是闭源中转，无源码可自建。）

## 2. 微信读书官方接口实测结果（本项目登录态）

| 接口 | 状态 | 返回 |
|---|---|---|
| `GET https://weread.qq.com/api/mp/cover?bookId=MP_WXS_xxx` | ✅ **200 可用** | 该号**最新一篇**：`name` / `title` / `pic` / `reviewId` / `coverBoxInfo` |
| `GET https://weread.qq.com/web/mp/content?reviewId=<rid>` | ✅ 200 | 正文 HTML（实测 3.48 MB，含 `#js_content`） |
| `GET https://mp.weixin.qq.com/s/<token>` | ✅ 200 | 原文直链，正文 3.51 MB，`js_content` 完整（项目既有能力） |
| `GET /web/mp/articles?bookId=&offset=` | ✅ **登录后可用** | **2026-09-19 实测 200 + reviews 列表**（详见 §2.2 实验 2）；未登录/安全检测态返回 `-2041` |
| `GET /api/mp/articles?...` | ❌ 404 | 该路径不存在（不是废弃，是压根没有） |
| `GET /web/user/userinfo` | ❌ 404 | 探测登录态用不上，改看 cookie |
| `GET /web/shelf/bookIds` | ✅ 200 | 书架 bookId 列表；**本项目账号下 `MP_WXS_` 数量为 0**（从未在微信读书关注过公众号） |

### 2.1 证据等级（哪些是实测，哪些只是推断）

⚠️ **微信读书没有公开 API 文档**，本节全部是逆向/社区结论。**别把推断当事实。**

| 结论 | 证据 | 强度 |
|---|---|---|
| `/api/mp/cover` 可用；reviewId→原文直链映射正确 | 本项目登录态实测 2 个号（哥飞/生财有术），cover 的 `title` 与 mp 页面 `var msg_title` 逐字一致 | **硬** |
| `/web/mp/content` 可拿正文 | 实测 200，3.48 MB 含 `#js_content` | **硬** |
| `/api/mp/articles` 没有这条路径 | 实测 404（"Page not found"） | **硬** |
| `/web/mp/articles` **登录后可拉列表** | 2026-09-19 实测：扫码登录后返回 200 + reviews 列表（哥飞，offset=0） | **硬**（2026-09-18 的 -2041 是未登录态所致） |
| 「列表接口已被官方废弃」 | we-mp-rss 旧文档（2026-08-13） | **已证伪**：登录态下实测 200 |
| 博客（2026-09）称该接口可用、offset 翻页 50 步长 | 第三方博客 | **✅ 已被本项目实测确认** |

**-2041 的成因（2026-09-19 已定位）**：未登录 / 安全检测态。**2026-09-18 的三条候选（未订阅 /
缺签名 / 接口下线）已全部排除**：
1. **未订阅 → 排除**：`addToShelf` 成功后仍 -2041（实验 1，见下）
2. **缺签名 → 排除**：前端页面内**带着实时生成的正确签名**调用仍 -2041（实验 2，见下）
3. **接口下线 → 排除**：扫码登录后同一 URL 返回 200 + reviews 列表

**可证伪实验（已全部完成）**
1. `POST /mp/shelf/addToShelf` 先关注目标号 → 重打 `/web/mp/articles`：若仍 -2041，
   即可排除「未订阅」这一假设。（2026-09-18 已做，哥飞，用户同意）
2. 在浏览器里真实打开某个公众号页面，抓**前端自己发出**的请求：若页面能渲染出文章列表，
   说明接口还在，只是我们没找对路径/参数。（2026-09-19 已做，哥飞 reader 页，
   结果见 §2.2 实验 2）

⚠️ 网上流传的「`/web/mp/articles` 主路径、offset 翻页、50 步长」说法与本次实测冲突，
在实验 1/2 做完前**两种说法都不可信**。

### 2.2 一手逆向文档：`rachelos/we-mp-rss` issue #442（比二手博客完整得多）

issue 原文给出的完整接口清单（含 curl、参数、请求头）：

| # | 接口 | 参数 / body |
|---|---|---|
| 1 | `POST https://weread.qq.com/mp/shelf/addToShelf` | body `{"bookIds":["<BOOK_ID>"]}`，`Content-Type: application/json;charset=UTF-8` |
| 2 | `GET /web/shelf/bookIds?bookIds=<BOOK_ID>` | 查订阅状态，返回 `{"data":[{"bookId":...,"onShelf":0\|1}]}` |
| 3 | `GET /web/mp/articles?bookId=<BOOK_ID>&offset=0` | 列表；`offset` 步长可用到 50（app 侧） |
| 4 | `GET /web/mp/content?reviewId=<REVIEW_ID>` | 正文 |
| 5 | `https://mp.weixin.qq.com/s/<ARTICLE_TOKEN>` | 原文直链（作者原话「测试了几个就是文章链接」） |

列表响应结构：`reviews[] → subReviews[] → review`，字段含 `reviewId` / `createTime` /
`mpInfo.title` / `mpInfo.pic_url` / `mpInfo.originalId`。

⚠️ 作者在文末**自述**「登录 cookie 刷新机制暂无测试、**风控也没测试**」→
他既没排除风控，也**从未说过「接口已废弃」**。「废弃」是 we-mp-rss 旧文档（2026-08-13）的
过时结论，其 2026-09-04 提交已翻转为「实测该列表接口可用，是增量补抓的主路径」。

**本项目实测补充（2026-09-18）**：`/web/shelf/bookIds?bookIds=MP_WXS_2399233620`
→ `{"data":[{"bookId":"MP_WXS_2399233620","onShelf":0}]}` = **未订阅**。
→ 我们的 -2041 **最可能是「未订阅」**（issue 建议流程：查订阅 → 未订阅先 `addToShelf` → 再拉列表），
其次才是风控，最后才是「接口废弃」。

**实验 1 已做（2026-09-18，哥飞，用户同意）→ 假设被排除**：

```
POST /mp/shelf/addToShelf {"bookIds":["MP_WXS_2399233620"]}  → {"status":200,"succ":1,"errCode":0}
GET  /web/shelf/bookIds?bookIds=MP_WXS_2399233620   → onShelf: 0 → 1（已订阅生效）
GET  /web/mp/articles?bookId=MP_WXS_2399233620&offset=0 → 仍然 {"errCode":-2041}
```

→ **「未订阅」不是 -2041 的原因**（当日结论）。

**实验 2 已做（2026-09-19，哥飞 reader 页，用户扫码登录）→ 决定性证据**：

```
# 场景 A：未登录（游客态 / 安全检测态）
GET /web/mp/articles?bookId=MP_WXS_2399233620&offset=0
  → {"errCode":-2041}          ← 前端自己发的请求，带页面实时生成的 x-wrpa-0 签名
  （同时 /web/shelf/bookIds → onShelf:1，/api/mp/cover → 200 正常）

# 场景 B：扫码登录后（同一页面、同一 URL、同一前端代码）
GET /web/mp/articles?bookId=MP_WXS_2399233620&offset=0
  → {"reviews":[{...}, ...]}   ← 200 + 完整文章列表！
```

对比场景 A/B 请求头：**登录成功的那次请求额外带 `x-wr-ticket` + `x-wr-randstr` 两个头**，
`x-wrpa-0` 签名两次**完全相同**。

→ **结论（推翻 §2.4 旧判断）**：
1. **-2041 的真凶是「未登录」，不是缺签名、不是未订阅、不是接口废弃**
2. **`x-wrpa-0` 不是一次性签名**（同一值重放成功），此前「重放即 -2041」的观察
   也是因为那两次都在未登录态
3. 登录后浏览器自动附加的 `x-wr-ticket`（长凭证）+ `x-wr-randstr`（随机串）才是
   列表接口的通行证——它们是**登录态附带**的，不是手工算的签名

→ 接入方案改为「**登录态 + 列表接口可用**」，不再按 cover-only 设计（但 cover 仍作
低频兜底和正文直链获取）。列表响应结构即 we-mp-rss issue #442 所述：
`reviews[] → subReviews[] → review`，字段含 `reviewId` / `createTime` /
`mpInfo.title` / `mpInfo.pic_url` / `mpInfo.originalId`。

### 2.3 另一条路：官方 Agent Gateway（**唯一有正式文档的通道**）

- 官方开源仓库 **`Tencent/WeChatReading`**（Apache-2.0，每个能力一份 md），2026-05-17 上线
- 端点 `POST https://i.weread.qq.com/api/agent/gateway`，鉴权 `Authorization: Bearer wrk-…`
  （Key 在 `weread.qq.com/r/weread-skills` 登录后领取，绑定个人 vid）
- 覆盖：搜书（`scope=2` 搜公众号 / `scope=4` 搜公众号文章）、书籍信息、书架、笔记划线、
  阅读统计、点评、推荐
- 公众号书籍的**章节目录**带 `isMPChapter` 标记（章节即文章，含标题/字数/更新时间/`chapterUid`）
  → **可能**用来替代打不通的 `/web/mp/articles` 拿文章列表
- ❌ 已知缺口：**无文章正文接口、无 reviewId→原文直链映射** → 单靠它做不了全文 RSS，
  只能与 cover/content 组合使用（待实测）

### 2.4 签名机制 `x-wrpa-0` 与 `x-wr-ticket`（2026-09-19 修正）

**机制**：受保护接口要求请求头

```http
x-wrpa-0: b89a20c8…a921a,ZGIjIyFLaTVlVsOzRMKow      # 两段，逗号连接，共约 200 字符
```

由页面加载的 WASM 签名器生成：`cdn.weread.qq.com/web/wrpa/wasm/wpa-1.0.1.min.wasm`，
JS 入口为全局 **`window.__WRPA__ = {version:"1.0.5", sr: fn, decode: fn}`**；
`sr(url)` 返回数组，`Array.join(',')` 即为头值。
**函数体被 JS 虚拟机混淆**（自定义字节码解释器），逆向源码不划算，只能黑盒用。

- **缺失或无效签名 → `{"errCode":-2041}`** —— 这是**未登录态**的表象之一，**不是 -2041 的真凶**。
- **真凶（2026-09-19 实验 2 定位）**：**未登录**。登录后同一 URL、甚至**同一个 `x-wrpa-0` 值**
  也能 200（实验 2 中两次请求签名完全相同）。「签名一次性、重放即 -2041」是**错误结论**——
  当时两次都在未登录态，观察被登录态变量污染。
- 登录成功那次请求**额外带两个头**（未登录时没有）：
  - `x-wr-ticket: t03tserver…`（长凭证，登录后由客户端维护，非手工计算）
  - `x-wr-randstr: @QX9`（短随机串）
  - 这两个头 + 登录 cookie 才是列表接口的通行证；`x-wrpa-0` 是页面环境自动附加的签名。

**页面内 fetch 复现已验证成功（2026-09-19）**：

在已登录页面上下文内：
```js
fetch('/web/mp/articles?bookId=MP_WXS_2399233620&offset=0',
      {credentials: 'include',
       headers: {'x-wrpa-0': window.__WRPA__.sr(url).join(',')}})
  → 200 + {"reviews":[...]}
```

🔴 **2026-09-19 生产化时实测修正（签名三硬约束，违反即 -2041，表象与未登录无法区分）**：

1. **`sr()` 必须签完整 URL（含 `https://weread.qq.com` origin）**——用相对路径签名只返回
   一段 hex，服务端判签名无效 → -2041。当日排障实锤：cookie 体检正常（wr_skey/wr_vid/wr_rt
   齐全、书架页登录态渲染正常），相对路径签名打列表稳定 -2041；改完整 URL 签名 → 200。
2. **`sr()` 返回 `Promise<Array>`**，需 `await` 后再 `.join(',')`（计划文档示例漏了 await）。
3. **`__WRPA__` 只在 `/web` 等 SPA 路由加载**（如 `/web/shelf`）——首页 `/` **不加载**（36s
   轮询无果实测）。生产导航目标用 `https://weread.qq.com/web/shelf`，并轮询
   `typeof window.__WRPA__ === 'object'` 就绪后再发请求（`monitors/weread.py:_ensure_wrpa_ready`）。

辅助判别（2026-09-19 抓包）：前端自己发的 `/web/mp/articles` 请求头**只有 x-wrpa-0**，
无 x-wr-ticket/x-wr-randstr（Playwright `page.on("request")` 全量捕获）——再次证实这两个头
非必须。前端 x-wrpa-0 为两段式（hex~128 + base64~180），与完整 URL 签名输出一致。

- 请求只带 `x-wrpa-0`（页内 `sr()` 现算）+ 自动 cookie——**`x-wr-ticket`/`x-wr-randstr`
  非必须**（localStorage 里也找不到它们，应为前端在特定场景才附加的增强头）
- `sr(url)` 本次生成的签名仅 16 字符（抓包所见为 ~200 字符），仍通过——签名算法细节待考，
  但「**登录 cookie + 页内现算签名**」组合确定可行
- 列表接口的真正门槛 = **登录态**（wr_skey/wr_vid/wr_rt cookie），签名是次要条件

🔴 **风控事故（2026-09-18 23:57–00:04）**：短时间内连续约 10 次签名试探 + 列表/翻页请求，
**触发微信读书图形验证码（识图解锁）**，由用户手动解了一次。
2026-09-19 上午克隆 profile（未登录态）探索时又触发「安全检测中」遮罩，同样由用户手动通过。
→ **铁律**：weread 接口有频控。探索性调用必须慢（每接口 ≥10s 间隔、单日十几请求封顶），
  否则会污染正常账号，且风控期的 -2041 与「签名不对」无法区分，白白增加排查难度。

**免签名的接口**：`/api/mp/cover` **无需 `x-wrpa-0`**（requests 直调即 200，已验证）
→ cover 仍是最省事的低频兜底；列表接口（`/web/mp/articles`）登录态下可用，生产路径见 §3。

## 3. 可用链路

### 3.1 列表链路（2026-09-19 打通 · 主路径，需登录态）

```
CDP Chrome（登录态）页面内 fetch
GET /web/mp/articles?bookId=MP_WXS_xxx&offset=N     ← 需登录态；间隔 ≥2s
        ↓ reviews[] → reviewId = MP_WXS_<bid>_<token>（originalId 即 token）
https://mp.weixin.qq.com/s/<token>                   ← 原文直链（正文主路径，见 §3.3）
```

- 列表项含 `mpInfo.title` / `createTime` / `mpInfo.pic_url` / `mpInfo.originalId`
- **翻页实测（2026-09-19，哥飞，offset 0/20/50/100 四页）**：
  - 每页固定 **20 条**，`offset` 按条数精确偏移，页与页时间范围**无缝衔接**
    （0 页 09-18~08-30 → 20 页 08-30~08-11）
  - **翻页规则：`offset += len(reviews)`，翻到空列表即到底**——不要用 issue #442
    所说的「50 步长」，实测 50 会**跳过每页之间约 30 条**（漏文章）
  - 前端 UI 的「无限下滚」底层就是本接口（用户在页面上看不到翻页按钮是正常的）
- ⚠️ **~ token 条目 mp 直链不可达（2026-09-19 实测）**：约 1/10 条目的
  `originalId`（= reviewId 末段）含 `~`（如 `s6xNpVp1TsNmz7E~qKD9Ug`），拼
  `mp.weixin.qq.com/s/<token>` 必返回「参数错误」（原文与 %7E 转义均试过）。
  标准 `/s/` token 字符集不含 `~`，疑似非 /s/ 型 ID，映射规则未知（未再探索，
  防 weread 请求超配额）。生产处理（`monitors/weread.py`）：不入队、不标 seen，
  健康度行持续暴露 `直链不可达 N 篇`，等用户反馈处置。
- **需要登录态**：页面内 fetch（`credentials:'include'` + `__WRPA__.sr()` 签名）已实测 200（§2.4）；
  requests 直调是否可行待测（需导出登录 cookie + 解决签名来源，签名可重放是已知线索）

### 3.2 cover 链路（免签名兜底 · requests 直调）

```
/api/mp/cover?bookId=MP_WXS_xxx        ← 需 cookie，每号每天 1 次
        ↓ reviewId = MP_WXS_<bid>_<token>
https://mp.weixin.qq.com/s/<token>     ← 原文直链，项目既有 fetch_web_content
   （备选）/web/mp/content?reviewId=…  ← 同域带 cookie，无需过 mp 风控
```

**token 映射已验证**：cover 返回的 `title` 与 mp 页面 `var msg_title` **逐字一致**
（哥飞《网站已经有排名，新功能该放首页还是内页？》、生财有术《赶上户外运动风口…》）。
即：**拿到 reviewId 就等于拿到永久原文直链**，不需要再查一次。

### 3.3 正文路径选型（2026-09-19 定）：只用 mp 直链

**正文一律走 `mp.weixin.qq.com/s/<token>`**（项目既有 `fetch_web_content` 管线），理由：
① **风险隔离**：mp 直链是公开页面、无需登录态，weread 账号每天只发 1 次列表请求，
   把 weread 登录态资产（扫码获取成本高、有小黑屋风险）的暴露面压到最小；
② **复用既有能力**：UA/解析/重试全部现成，与项目其他公众号文章同源同管线；③ 直链永久有效。

⚠️ **不使用 `/web/mp/content` 做兜底（2026-09-19 用户定策）**：每篇正文都打 weread 同域请求
会显著抬高频次、放大账号风控暴露面——宁可本轮跳过、下轮经 mp 直链重试，
也不为单篇正文去消耗 weread 配额。（该接口实测可用的事实保留在 §2 表格，仅作备用知识。）

## 4. 凭据（cookie）

> ⚠️ **生产路径（monitors/weread.py）不使用本节导出文件**：页内 fetch 的登录 cookie
> 由浏览器实时携带，续期 = CDP Chrome 里重新扫码（自动弹码流程见 §5.3），
> 无任何落盘凭据要更换。下方导出工具仅供探针/requests 直调实验用。

- 落盘位置：`monitors/.weread_cookie`（已加入 `.gitignore`，绝不入库）
- 获取：`python scripts/weread_cookie_export.py --test MP_WXS_2399233620`
  （连现有 CDP Chrome 取 weread 域 cookie 并落盘；`--test` 会额外打 1 个请求自测）
- **可脱离浏览器**：`urllib/requests` 直调已验证通过（HTTP 200），请求头需
  `User-Agent` + `Referer: https://weread.qq.com/` + `Cookie: <文件内容>`
- 刷新：cookie 失效后重新在 CDP 浏览器里扫码登录，再跑一次导出脚本
- 安全：脚本只打印 cookie **名与长度**，值永不进对话上下文

## 5. 已知限制（接入前必须接受）

1. **列表接口需要登录态**：生产路径 = CDP Chrome（登录态）页面内 fetch（2026-09-19 已验证 200）；
   requests 直调（无登录 cookie）→ -2041，能否用导出 cookie + 重放签名绕开待测
2. **cover 不返回 `publishTime`** → cover 兜底路径的时间窗口过滤失效，
   去重靠 `reviewId`/原文 token（进 `seen`）；**列表接口的 `createTime` 有时间戳**，
   主路径无此问题
3. **cookie 有效期：不猜（2026-09-19 用户定策）**——无官方文档，不做任何时效假设。
   策略已演进为**自动处置**（当日下半年落地，取代最初的「纯观测上报」）：
   - 登录失效（-2041 且 wr_skey 缺失）→ **自动截登录二维码等扫码**（扫到 cookie
     浏览器内自动生效、自动续抓；`WEREAD_AUTO_RELOGIN`/`WEREAD_RELOGIN_WAIT`）；
   - wr_skey 仍在的 -2041 → 大概率人机检测，**不白等扫码**，转过码流程
     （`scripts/weread_captcha.py`，识别需模型在场）；
   - 连环码（同日第 2 次提交）→ 高危熔断 12 小时（见 §8）。
   仍然成立的红线：绝不自动重试硬闯、绝不猜测性换 cookie。
4. 请求量：现役监控名单 2 号（中金点睛、哥飞；生财有术/DeepVan 已于 2026-09-19 移除），时间窗语义下日常 ≈ **2-4 请求/天**（正文走 mp 直链不耗 weread 配额），远低于原代理；**机械熔断**：双层配额 日 `WEREAD_DAILY_QUOTA=25` / 小时 `WEREAD_HOURLY_QUOTA=6`（`monitors/.weread_quota.json` 按天/小时计数），到线自动跳过公众号源或停止补全续批——2026-09-19 开发验收期超量（~25-30 次）连触 4 轮验证码的事故已用机械闸门封死
5. 🔴 **低频铁律**（2026-09-18 事故 + 博客佐证，详见 `wechat-mp-sources.md` §9）：
   探索性调用每接口间隔 ≥10s、单日总量十几请求封顶；生产列表/正文间隔 ≥2s（we-mp-rss 默认）；
   **个人单账号低频自用是社区验证过的安全档位，不做号池/轮换**；
   **加号/关注操作放慢**——加号太快会被关「小黑屋」24h；
   一旦出现识图验证 /「安全检测中」遮罩，**立即停手、冷却数小时**，别硬试。

## 6. bookId 对照表（来源 `monitors/.mp_cache.json`）

| 公众号 | bookId |
|---|---|
| 中金点睛 | `MP_WXS_3270332840` |（名单 2026-09-19 收缩为中金点睛 + 哥飞两号；下两行映射保留作参考）
| DeepVan的逃生地牢 | `MP_WXS_3905719449` |
| 哥飞 | `MP_WXS_2399233620` |
| 生财有术 | `MP_WXS_3891906515` |

新增公众号如何拿 bookId：原有 `wxs2mp` 解析随代理一起死了，**目前无在线解析途径**；
可手工在微信读书搜索该号，从页面请求里抄 `MP_WXS_` id。

## 8. 验证码实测（2026-09-19 · 会话内自动过码已跑通）

**形态**：腾讯验证码组件（`captcha.gtimg.com/static/template/drag_ele…`），**图像点选类**——
「选择最符合描述的图片“一辆棕色的车”」，3×2 拼图六选一（混元AI生成图），点错可「换一组」。

**出现位置（重要）**：-2041 风险态下 **shelf 页面无任何提示**（正常渲染），码只在
**mp reader 页**由前端渲染（前端自己调 articles 接口收到 -2041 后弹码组件）。
→ 过码入口：导航到 `weread.qq.com/web/mp/reader/<infoId>`（书架点开公众号书即得 URL）。

**DOM 结构（决定点击方式）**：
- 码整体在**跨源 iframe** 里；六张图是**一张合成大图** `.tc-bg-img`（元素局部 340×243，
  内部被父页 CSS 缩放到可视 ~590px 宽）；
- **裸 `page.mouse.click` 视口坐标会错位选错格**（缩放映射陷阱，实测踩过）——必须用
  Playwright 在 frame 内按**元素局部坐标**点击（`locator.click(position=…)`，自动映射）；
- 提交/换一组按钮按可见文本「确定」/「换一组」定位。

**过码流程（已实测通过，2026-09-19；识别由会话内执行模型完成）**：`scripts/weread_captcha.py`——
`--shot`（整页截图，模型读图出格子）→ `--grid "行,列" --confirm`（frame 内点选+提交）
→ 自动核验；点错 `--refresh` 换新码重来（≤2 次）。**实测：一次点选提交即过码，
过码后列表接口立即恢复 200**（风险态解除，无需换 cookie/重新登录）。

**检测要点**：`detect_captcha` 必须**遍历全部 iframe frames**（只查主 frame 漏检），
标记词需含「最符合描述的图片」。

**连环码 = 高危风控信号（2026-09-19 用户定策）**：正常状态只出 **1 次**人机验证；同日第 2 次提交（`scripts/weread_captcha.py --confirm` 每次提交记账）说明行为像人机/请求过多，自动熔断 12 小时不发任何 weread 请求（`WEREAD_CAPTCHA_SERIAL_LIMIT=2`/`WEREAD_RISK_COOLDOWN_HOURS=12`，台账 `.weread_quota.json` 的 `captcha_events`/`risk_until`，跨天清零）。2026-09-19 事故实录：超量后连续 4 轮码（溪流→花园→瀑布→礁石），正是该规则的实证。

## 7. 相关脚本

| 脚本 | 作用 |
|---|---|
| `scripts/weread_cookie_export.py` | CDP 取 cookie 落盘 + 可选直调自测 |
| `scripts/weread_api_probe.py` | 在已登录页面上下文内 fetch 探测（同源带 cookie，凭据不落盘） |
| `scripts/weread_probe.py` | CDP 被动抓包（不发额外请求，记录浏览器自然请求到 JSONL） |
| `scripts/launch_weread_probe.py` | 上者的 DETACHED 启动器（抗会话回收） |
| `scripts/weread_state.py` | 登录态体检（页面文本/cookie 名，不含凭据值） |
