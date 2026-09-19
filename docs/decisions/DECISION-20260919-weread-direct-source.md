# DECISION-20260919 · 公众号源接替定为「微信读书直连」，列表已打通待开发

**背景**：wewe-rss 代理（Deno Deploy 中转）2026-07-20 随平台下线，公众号源停用（`WECHAT_SOURCE_ENABLED=0`）。

**决策**（2026-09-18/19 两天实测确立）：
1. 接替方案 = 微信读书官方接口直连（自己的登录态 cookie，不经任何第三方）。
2. -2041 真凶 = **未登录**（签名/未订阅/接口废弃三假设全排除）；登录后 `/web/mp/articles`
   返回 200 + reviews，每页 20 条、`offset += len(reviews)` 翻页可拿全历史。
3. **正文只走 mp 原文直链**（`reviewId` 后段即 token）——不用 `/web/mp/content` 兜底，
   避免每篇都打 weread 同域放大账号风控暴露面（用户定策）。
4. cookie 有效期**不猜**：失败原样观测上报、等用户反馈，不自动硬闯。
5. 验证码为图像点选类 → **半自动过码**（截图 → 执行模型识别坐标 → 脚本点击）；不做全自动、不用 OCR。

**执行**：生产模块待开发，触发词「开发weread源模块」→ `docs/plans/PLAN-20260919-weread-source-module.md`。
实测真源 `references/weread-direct-source.md`；防封纪律 `references/wechat-mp-sources.md` §9。
