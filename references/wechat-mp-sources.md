# 微信公众号抓取 · 外部资料索引

> **本文件只做「外部资料归档 + 出处留存」，不替代本项目实测。**
> 本项目自己的实测结论在 **`references/weread-direct-source.md`**（硬事实真源）。
> 两者冲突时，**以实测文档为准**——本索引里多处资料已被实测证伪或发现过期（见 §7 可信度提示）。
>
> 所有链接均为本次调研真实访问/引用过的地址，未访问的会显式标注「未亲自验证」。

---

## 1. 博客（同一作者 · 2026-09，内容最新，参考价值最高）

> ⚠️ 链接必须带**尾部斜杠**（2026-09-19 实测：不带斜杠 404）。

| 链接 | 主题 | 对本项目的价值 | 备注 |
|---|---|---|---|
| https://bingqiangzhou.github.io/posts/wewe-rss-weread-mp-api/ | wewe-rss 死因解剖：半开源架构拆解、闭源中转 502 实测，与微信读书公众号接口的存续验证 | 给出「微信读书公众号接口全景」表（articles / cover / content / addToShelf / bookIds）、reviewId→原文直链映射、中转 502 时间线、**官方 Agent Gateway 线索**；**防封要点**：「新版已不需要 x-wr-ticket 签名，只要浏览器登录 Cookie（wr_vid/wr_skey/wr_rt）」「-2041 是认证/风控错误——换新鲜 Cookie 往往就通了」、频控数字（单账号 50 次/天、单 IP 24h 300 次，放宽到 300/天「没两天接口就 gg」） | ⚠️ 作者**自己没做带 cookie 的实测**，只做了无凭证探测（-2010）；「列表接口可用」是引用 we-mp-rss issue #442。其「-2041 换新鲜 Cookie 就通」判断已被本项目实测互证（2026-09-19） |
| https://bingqiangzhou.github.io/posts/wechat-mp-article-anti-crawl/ | 突破微信公众号文章反爬：拿到文章链接后的六条获取路线（附实测） | **「公众号抓取路径」的总纲**（见 §6 六条路线）；mp 侧反爬三板斧、UA 对照实测、路线三（公众平台后台）可拿全量历史；**检测机制**：工具 UA → 302 进验证页（`wappoc_appmsgcaptcha` 腾讯防水墙滑块）、高频 → 验证码 → 封 IP | 讲的是 `mp.weixin.qq.com` 侧反爬（weread 反爬强度**远低于** mp 主站）；weread 路线划界：「**个人单账号自用是社区普遍验证过的安全档位；账号池轮换灰色且连累他人账号，别碰**」 |
| https://bingqiangzhou.github.io/posts/learningnotes-wechatandxsubscriptionsurvey/ | 订阅公众号文章与 X 推文的开源方案调研：曲线救国、账号池与接口封锁（2026-08-17） | weread 账号风控：「**加号太快太频繁会被微信读书关『小黑屋』24 小时**」（账号状态含 今日小黑屋/禁用/失效）；WeWe RSS 频控默认：每分钟最大 60 请求、更新间隔 60s、定时每天两次；接口封锁史（公众平台编辑器接口 2026-07 关闭且大概率不再开放、搜狗验证码地狱） | 「全部违反平台服务条款，封号风险真实存在」——本项目只取其频控/风控事实，**不做号池** |

## 2. wewe-rss（**已死，我们原来的依赖**）

- 仓库 https://github.com/cooderl/wewe-rss
- 它只是**客户端**：真正的接口封装在作者私有的**闭源中转**（Deno Deploy Classic，平台 2026-07-20 sunset）
- 关键源码：
  - `apps/server/src/trpc/trpc.service.ts` — 调用 `/api/v2/platform/mps/{mpId}/articles`、`/api/v2/platform/wxs2mp`、`/api/v2/login/platform`
  - `apps/server/src/configuration.ts` — `PLATFORM_URL` 默认 `https://weread.111965.xyz`；备用 `https://weread.965111.xyz`
- 相关 issue：`#11`（是否假开源）、`#396`（限流规则：单账号 50 次/天、单 IP 24h 300 次）、`#463`（是否还更新）
- ⚠️ **两个域名已全部死亡**（实测 502 / 404 DEPLOYMENT_NOT_FOUND），换镜像域名无用

## 3. we-mp-rss（**当前最活跃的实现，两份副本**）

| 位置 | 链接 | 内容 |
|---|---|---|
| GitHub | https://github.com/rachelos/we-mp-rss | 主仓库。核心实现 `core/wx/model/weread_mp.py` |
| **issue #442**《新方案路径》 | https://github.com/rachelos/we-mp-rss/issues/442 | **一手逆向文档**：bookId/reviewId 标识说明、`addToShelf` 请求体、`bookIds` 查订阅、`articles?bookId=&offset=`（app 侧 50 步长）、`content?reviewId=`、原文直链补充。59 条评论 |
| Gitee 镜像文档 | https://gitee.com/qiu-qi77/we-mp-rss/blob/main/docs/weread-mp.md | 《微信读书公众号采集》：cover 增量采集、`WEREAD_COOKIE` 环境变量、`WEREAD_CONTENT_INTERVAL>=2`、错误码说明、限制说明 |

issue #442 里值得记住的两句：
- 作者文末自述「登录 cookie 刷新机制暂无测试、**风控也没测试**」→ 他从没说过接口废弃
- 仓库 **2026-09-04 提交**已把结论从「已废弃恒返 -2041」翻转为「实测该列表接口可用，是增量补抓的主路径」
  （Gitee 那份文档是 2026-08-13 的**旧结论**，已过时）

## 4. 官方通道（**唯一有正式文档的路**）

- 仓库 https://github.com/Tencent/WeChatReading （Apache-2.0，每能力一份 md；**未亲自验证，来自博客引用**）
- 端点 `POST https://i.weread.qq.com/api/agent/gateway`，鉴权 `Authorization: Bearer wrk-…`
- API Key 领取页 https://weread.qq.com/r/weread-skills （登录领取，绑定个人 vid）
- 能力：搜书（`scope=2` 搜公众号 / `scope=4` 搜公众号文章）、书籍信息、书架、笔记划线、阅读统计、点评、推荐；
  公众号书籍的**章节目录**带 `isMPChapter` 标记（章节即文章）
- ❌ 缺口：**无文章正文接口、无 reviewId→原文直链映射** → 单靠它做不了全文 RSS

## 5. 其它可复用的现成工具

| 项目 | 链接 | 说明 |
|---|---|---|
| weread-mp | https://github.com/steptian/weread-mp | `browser-cookie3` 自动解密 Chrome 里的 `wr_skey`+`wr_vid` → 调 `/web/mp/articles` → 拿原文直链。**思路与我们一致，可参考其对 cookie 的处理** |
| weread-omni | https://npm.io/package/weread-omni | CLI：`public-accounts search/subscribe/articles/feed/export`，支持 RSS 导出；正文只从 `mp.weixin.qq.com/s` 下载 |
| wechat-article-exporter | 博客提及，**未给链接** | 把「路线三（公众平台后台接口）」产品化；用约 96 个 Cloudflare Worker 组成代理池抓正文 |
| wechat2rss | 博客提及，**未给链接** | 同源 mp 平台 Cookie + token；提供验证码续期 API |
| mitmproxy 插件（抓短期凭证） | 博客提及，**未给链接** | 路线五：抓手机微信端 `pass_ticket` 类短期票据，重放窗口很短 |

## 6. 「公众号抓取路径」六条路线（摘自 anti-crawl 博客）

> 这是我们选型时的全局地图，成本从低到高：

1. **单篇正文**：伪装浏览器/微信 UA 直连 `mp.weixin.qq.com/s/...`，一行 curl 即可
   （实测：`curl/8.x`、`python-requests/2.x` 默认 UA → 302 到验证码页；Chrome UA / 微信 UA → 200）
2. **微信读书中转**（我们现在的路）：真人 cookie + 低频，长期订阅成本最低
3. **公众平台后台接口** `searchbiz`（拿 fakeid）+ `appmsgpublish`（拿文章列表）
   → **能拿任意公众号的全量历史文章**。条件：自己注册公众号 + 扫码登录后台；
   session 约 **4 天**过期、有频控；目标号若关闭「允许搜索」则无效；付费/关注可见文章拿不到
4. **RSS 中转服务**（wewe-rss / RSSHub 类）：第三方，已死则不可用
5. **客户端抓包**（mitmproxy + 短期凭证）：适合单次批量导出，不适合长期
6. **搜狗微信** `weixin.sogou.com`：有 SNUID Cookie 校验、易弹验证码，且返回的是**带时效 key 的临时链接**（几小时失效）——不推荐

**作者给出的组合拳**：路线四（微信读书）打底做订阅 + 路线一（直连）做单篇抓取 + 遇到「某号全部历史导出」的一次性任务再上路线三。

## 7. 注意事项汇总（各资料摘录，⚠️ 混杂二手信息）

**错误码（weread 侧）**
- `-2010` 用户不存在（没带 cookie / 会话没建立）
- `-2012` 登录超时（cookie 过期，重新从浏览器复制）
- `-2041` 认证或风控错误 —— **本项目实测补充：缺/无效 `x-wrpa-0` 签名也返回它**
  （详见 `weread-direct-source.md` §2.4）

**频率**
- weread：we-mp-rss 建议正文请求间隔 ≥2s（翻页间隔 1s）
- wewe-rss 中转（已死，仅供参考量级）：单账号 50 次/天、单 IP 24h 300 次；
  作者放宽到 300 次/天「没两天接口就 gg 了」→ 平台收紧很快
- 🔴 本项目事故：连续 ~10 次探索性调用即触发图形验证码 → 探索也要慢（≥10s 间隔、单日十几请求封顶）

**账号风控（weread 侧）**
- 「加号太快太频繁会被微信读书关**小黑屋** 24 小时」（账号状态含 今日小黑屋/禁用/失效）
- 读书路线「登录易失效、频控严格、有封号风险」；量大需要「号池 + 严格限频」
- 社区共识：**个人轻量订阅（几个到几十个号、低频刷新）没问题**；
  「个人单账号自用是社区普遍验证过的安全档位；账号池轮换灰色且连累他人账号，别碰」
- weread 接口反爬强度**远低于** mp 主站（anti-crawl 博客）

**mp 正文抓取**
- 正文是**服务端渲染**的，不需要执行 JS；HTML 里就有 `#js_content`、`var msg_title`、`window.cgiDataNew`
- 第一道闸不是「必须微信 UA」，而是「**长得像工具的 UA 直接进验证页**」；高频下浏览器 UA 也会先于微信 UA 触发风控
- 图片 `mmbiz.qpic.cn` 防盗链不严（不带 Referer 也能 200），保险起见带上
- 直链是**永久**的（`mp.weixin.qq.com/s/xxx` 或 `__biz/mid/sn` 形式）；搜狗返回的带时效 key

## 8. ⚠️ 资料可信度提示（重要）

| 说法 | 出处 | 本项目判定 |
|---|---|---|
| `/web/mp/articles`「offset 翻页、50 步长、实测可用」 | 博客（引用 issue #442） | ✅ **已复现**（2026-09-19 登录态实测 200 + reviews；此前 -2041 是未登录态所致，见 `weread-direct-source.md` §2.2 实验 2） |
| `/web/mp/articles`「已废弃」 | we-mp-rss 旧文档（2026-08-13） | **过期**；该仓库 2026-09-04 已翻转结论；本项目登录态实测 200 彻底证伪 |
| 「前端 JS 中也不再有文章列表接口」 | we-mp-rss 文档 | **与本项目抓包冲突**——mp/reader 页面明确调用了 articles 并拿到 reviews |
| 「换新鲜 Cookie 往往就通」 | 博客 | ✅ **已复现（本质 = 登录态就通）**：2026-09-19 扫码登录后同一 URL 立即 200；2026-09-18 的「新鲜 cookie 仍 -2041」是因为那 cookie 是游客/安全检测态 |
| 「新版已不需要 x-wr-ticket 签名，只要登录 Cookie」 | wewe-rss 死因博客 | ✅ **与实测吻合**：登录后请求自动带 `x-wr-ticket`+`x-wr-randstr`（客户端维护的凭证，非手工签名）；真正的门槛是登录 cookie 本身 |
| `/api/mp/cover` 可用（最新 1 篇，无需签名） | 多份资料一致 | ✅ **本项目实测确认**（requests 直调 200，两号验证） |
| reviewId 后段 = 原文直链 token | 博客 + issue #442 | ✅ **本项目实测确认**（cover 的 title 与 mp 页面 `var msg_title` 逐字一致） |
| 「`x-wrpa-0` 签名一次性、重放即 -2041」 | 本项目旧结论（2026-09-18） | ❌ **已被推翻**：登录后同一签名值重放 200；当时两次都在未登录态，观察被污染 |

## 9. 防封纪律（2026-09-19 从三篇博客 + 本项目事故沉淀）

1. **个人单账号、低频、自用**——这是社区验证过的安全档位；**不做号池/轮换**（灰色且连累他人账号）
2. **频率**：生产列表/正文间隔 ≥2s（we-mp-rss 默认）、探索性调用 ≥10s 且单日十几请求封顶；
   订阅场景 = 每号每天 1 次列表 + cover 兜底
3. **加号/关注操作放慢**——加号太快会被关「小黑屋」24 小时
4. **请求头全套模拟浏览器**：桌面 Chrome UA + `Referer: https://weread.qq.com/` +
   `Accept: application/json, text/plain, */*`（对齐 we-mp-rss；工具 UA 在 mp 主站直接 302 进验证页）
5. **页面内 fetch 优先**（同源自动带 cookie + 页面签名，风控风险最低）；requests 直调仅用于
   免签名接口（cover）和验证参照
6. **遇到图形验证码 /「安全检测中」遮罩立即停手、冷却数小时**，不硬试；
   风控期的 -2041 与「参数/签名不对」无法区分，硬试只会污染排查
7. 平台收紧很快（wewe-rss 中转放宽到 300/天「没两天就 gg」）→ 任何提量动作前先小批试探
