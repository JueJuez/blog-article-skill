# 微信公众号抓取 · 外部资料索引

> **本文件只做「外部资料归档 + 出处留存」，不替代本项目实测。**
> 本项目自己的实测结论在 **`references/weread-direct-source.md`**（硬事实真源）。
> 两者冲突时，**以实测文档为准**——本索引里多处资料已被实测证伪或发现过期（见 §7 可信度提示）。
>
> 所有链接均为本次调研真实访问/引用过的地址，未访问的会显式标注「未亲自验证」。

---

## 1. 博客（同一作者 · 2026-09，内容最新，参考价值最高）

| 链接 | 主题 | 对本项目的价值 | 备注 |
|---|---|---|---|
| https://bingqiangzhou.github.io/posts/wewe-rss-weread-mp-api | wewe-rss 死因解剖：半开源架构拆解、闭源中转 502 实测，与微信读书公众号接口的存续验证 | 给出「微信读书公众号接口全景」表（articles / cover / content / addToShelf / bookIds）、reviewId→原文直链映射、中转 502 时间线、**官方 Agent Gateway 线索** | ⚠️ 作者**自己没做带 cookie 的实测**，只做了无凭证探测（-2010）；「列表接口可用」是引用 we-mp-rss issue #442 |
| https://bingqiangzhou.github.io/posts/wechat-mp-article-anti-crawl/ | 突破微信公众号文章反爬：拿到文章链接后的六条获取路线（附实测） | **「公众号抓取路径」的总纲**（见 §6 六条路线）；mp 侧反爬三板斧、UA 对照实测、路线三（公众平台后台）可拿全量历史 | 讲的是 `mp.weixin.qq.com` 侧反爬，**未覆盖 weread 的 `-2041` / `x-wrpa-0`**；文中无 -2041 |

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
- weread：we-mp-rss 建议正文请求间隔 ≥2s
- wewe-rss 中转（已死，仅供参考量级）：单账号 50 次/天、单 IP 24h 300 次
- 🔴 本项目事故：连续 ~10 次探索性调用即触发图形验证码 → 探索也要慢（≥10s 间隔、单日十几请求封顶）

**mp 正文抓取**
- 正文是**服务端渲染**的，不需要执行 JS；HTML 里就有 `#js_content`、`var msg_title`、`window.cgiDataNew`
- 第一道闸不是「必须微信 UA」，而是「**长得像工具的 UA 直接进验证页**」；高频下浏览器 UA 也会先于微信 UA 触发风控
- 图片 `mmbiz.qpic.cn` 防盗链不严（不带 Referer 也能 200），保险起见带上
- 直链是**永久**的（`mp.weixin.qq.com/s/xxx` 或 `__biz/mid/sn` 形式）；搜狗返回的带时效 key

## 8. ⚠️ 资料可信度提示（重要）

| 说法 | 出处 | 本项目判定 |
|---|---|---|
| `/web/mp/articles`「offset 翻页、50 步长、实测可用」 | 博客（引用 issue #442） | **未复现**：我们调用恒 -2041（缺签名）；前端页面内调用确实成功（抓包实锤） |
| `/web/mp/articles`「已废弃」 | we-mp-rss 旧文档（2026-08-13） | **过期**；该仓库 2026-09-04 已翻转结论 |
| 「前端 JS 中也不再有文章列表接口」 | we-mp-rss 文档 | **与本项目抓包冲突**——mp/reader 页面明确调用了 articles 并拿到 reviews |
| 「换新鲜 Cookie 往往就通」 | 博客 | **未复现**（扫码后 30 分钟内的新 cookie 仍 -2041） |
| `/api/mp/cover` 可用（最新 1 篇，无需签名） | 多份资料一致 | ✅ **本项目实测确认**（requests 直调 200，两号验证） |
| reviewId 后段 = 原文直链 token | 博客 + issue #442 | ✅ **本项目实测确认**（cover 的 title 与 mp 页面 `var msg_title` 逐字一致） |
