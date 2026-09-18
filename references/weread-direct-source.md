# 微信读书直连源（wewe-rss 代理的替代方案）

> 2026-09-18 实测确立。**磁盘/实测证据优先于任何推断**——下表每一行的状态都是用本项目
> 自己的微信读书登录态打出来的真实响应，不是从文档抄的。
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
| `GET /web/mp/articles?bookId=&offset=` | ⚠️ **需签名** | 缺 `x-wrpa-0` → `-2041`；**前端页面内调用确实返回 reviews**（抓包实锤，见 §2.4）。本项目自建调用尚未打通 |
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
| `/web/mp/articles` 打不通 | 实测 -2041（**仅 1 次、1 个号、1 组参数**） | 现象硬 / **成因是推断** |
| 「列表接口已被官方废弃」 | 只有第三方项目 we-mp-rss 的文档这么写 | **软·未证实** |
| 反证：某博客（2026-09）称该接口可用、offset 翻页 50 步长 | 第三方博客，与本次实测**直接冲突** | **软·冲突未解释** |

**-2041 的其他候选解释（一条都没排除）**：未加书架/未订阅；参数不全（缺 count / maxIdx / 翻页游标）；
需要特定请求头或签名；账号风控；接口真的下线。

**可证伪实验（尚未做）**
1. `POST /mp/shelf/addToShelf` 先关注目标号 → 重打 `/web/mp/articles`：若仍 -2041，
   即可排除「未订阅」这一假设。（**写操作，会改你账号的关注关系，需你点头**）
2. 在浏览器里真实打开某个公众号页面，抓**前端自己发出**的请求：若页面能渲染出文章列表，
   说明接口还在，只是我们没找对路径/参数——这是**决定性证据**，且比继续猜路径高效。

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

→ **「未订阅」不是 -2041 的原因**。剩余候选只剩：① 接口确实已下线 ② 账号/接口级风控
③ 需要未知参数或签名。其中 ② 可信度较低——**同一时刻 `/api/mp/cover` 正常返回**（账号未被整体风控）。
故接入方案按「**列表接口不可用**」设计（cover-only），但不把门堵死：若日后拿到新证据再改。
补充观察：二手博客称「换新鲜 Cookie 往往就通」，本次 cookie 为扫码后 30 分钟内取得，未能复现该说法。

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

### 2.4 签名机制 `x-wrpa-0`（**-2041 的真正成因**）与风控事故

**机制**：受保护接口要求请求头

```http
x-wrpa-0: b89a20c8…a921a,ZGIjIyFLaTVlVsOzRMKow      # 两段，逗号连接，共约 200 字符
```

由页面加载的 WASM 签名器生成：`cdn.weread.qq.com/web/wrpa/wasm/wpa-1.0.1.min.wasm`，
JS 入口为全局 **`window.__WRPA__ = {version:"1.0.5", sr: fn, decode: fn}`**；
`sr(url)` 返回数组，`Array.join(',')` 即为头值。
**函数体被 JS 虚拟机混淆**（自定义字节码解释器），逆向源码不划算，只能黑盒用。

- **缺失或无效签名 → `{"errCode":-2041}`** —— 这才是当初「打不通」的根因，**不是接口废弃**。
- 已排除的其它假设：未订阅（`addToShelf` 成功后仍 -2041）、cookie 过期（同期 `cover` 正常 200）。

**本项目自建调用尚未打通（2026-09-18）**：
用 `sr(url)` / `sr(url,'GET')` / `sr('GET',url)` 现生成的签名去 fetch articles，**仍 -2041**。
可能原因：① 真实入参含 method/body/时间戳等我们没猜到的组合；② 当时已触发风控。
已知：**签名一次性**（重放抓包里拿到的旧签名，即使对同一 URL 也 -2041），必须页内实时生成。

🔴 **风控事故（2026-09-18 23:57–00:04）**：短时间内连续约 10 次签名试探 + 列表/翻页请求，
**触发微信读书图形验证码（识图解锁）**，由用户手动解了一次。
→ **铁律**：weread 接口有频控。探索性调用必须慢（每接口 ≥10s 间隔、单日十几请求封顶），
  否则会污染正常账号，且风控期的 -2041 与「签名不对」无法区分，白白增加排查难度。

**不需要签名的接口**：`/api/mp/cover` **无需 `x-wrpa-0`**（requests 直调即 200，已验证）
→ 低频订阅方案应优先用它，别去碰需要签名的接口。

## 3. 可用链路（最小闭环）

```
/api/mp/cover?bookId=MP_WXS_xxx        ← 需 cookie，每号每天 1 次
        ↓ reviewId = MP_WXS_<bid>_<token>
https://mp.weixin.qq.com/s/<token>     ← 原文直链，项目既有 fetch_web_content
   （备选）/web/mp/content?reviewId=…  ← 同域带 cookie，无需过 mp 风控
```

**token 映射已验证**：cover 返回的 `title` 与 mp 页面 `var msg_title` **逐字一致**
（哥飞《网站已经有排名，新功能该放首页还是内页？》、生财有术《赶上户外运动风口…》）。
即：**拿到 reviewId 就等于拿到永久原文直链**，不需要再查一次。

## 4. 凭据（cookie）

- 落盘位置：`monitors/.weread_cookie`（已加入 `.gitignore`，绝不入库）
- 获取：`python scripts/weread_cookie_export.py --test MP_WXS_2399233620`
  （连现有 CDP Chrome 取 weread 域 cookie 并落盘；`--test` 会额外打 1 个请求自测）
- **可脱离浏览器**：`urllib/requests` 直调已验证通过（HTTP 200），请求头需
  `User-Agent` + `Referer: https://weread.qq.com/` + `Cookie: <文件内容>`
- 刷新：cookie 失效后重新在 CDP 浏览器里扫码登录，再跑一次导出脚本
- 安全：脚本只打印 cookie **名与长度**，值永不进对话上下文

## 5. 已知限制（接入前必须接受）

1. **目前打通的只有「每个号最新 1 篇」**——`/web/mp/articles` 实测 -2041，但**成因未证实**
   （见 §2.1：也可能是未订阅/缺参数/风控）。在做完实验 1/2 之前，只能说「我们还没打通列表」，
   **不能断言「官方没有列表接口」**；规划接入时按 cover-only 设计，但别把门堵死。
2. **cover 不返回 `publishTime`** → 现有 `discover` 的「时间窗口过滤」失效，
   去重只能靠 `reviewId`/原文 token（进 `seen`），首次运行抓到的那篇就算「已见过」
3. **一天多更会漏**（只拿最新一篇；一天两更只抓到后一篇）
4. cookie 有效期**未观测**（待长期观察；失效表现为接口返回 -2012/-2010/-2041 类鉴权码）
5. 请求量：4 个号 × 1 次/天 = **4 请求/天**，远低于原代理
6. 🔴 **低频铁律**（2026-09-18 事故换来的）：探索性调用每接口间隔 ≥10s、单日总量十几请求封顶；
   生产阶段只用 **不需要签名的 `/api/mp/cover`**，不要去碰需要 `x-wrpa-0` 的列表接口——
   频繁调用会触发图形验证码，且风控期的 -2041 与「签名不对」无法区分。
   一旦出现识图验证，**立即停手、冷却数小时**，别硬试。

## 6. bookId 对照表（来源 `monitors/.mp_cache.json`）

| 公众号 | bookId |
|---|---|
| 中金点睛 | `MP_WXS_3270332840` |
| DeepVan的逃生地牢 | `MP_WXS_3905719449` |
| 哥飞 | `MP_WXS_2399233620` |
| 生财有术 | `MP_WXS_3891906515` |

新增公众号如何拿 bookId：原有 `wxs2mp` 解析随代理一起死了，**目前无在线解析途径**；
可手工在微信读书搜索该号，从页面请求里抄 `MP_WXS_` id。

## 7. 相关脚本

| 脚本 | 作用 |
|---|---|
| `scripts/weread_cookie_export.py` | CDP 取 cookie 落盘 + 可选直调自测 |
| `scripts/weread_api_probe.py` | 在已登录页面上下文内 fetch 探测（同源带 cookie，凭据不落盘） |
| `scripts/weread_probe.py` | CDP 被动抓包（不发额外请求，记录浏览器自然请求到 JSONL） |
| `scripts/launch_weread_probe.py` | 上者的 DETACHED 启动器（抗会话回收） |
| `scripts/weread_state.py` | 登录态体检（页面文本/cookie 名，不含凭据值） |
