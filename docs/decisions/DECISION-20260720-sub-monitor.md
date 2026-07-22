# DECISION-20260720-sub-monitor
## 背景
用户要"持续订阅某账号→自动总结"。落地 B站UP主监控 + 公众号监控，发现新内容→AI 总结→落 Obsidian + 飞书。

## 决策
- 范围：B站UP主监控 + **公众号监控（路线 B）**。公众号经 weread 代理（weread.111965.xyz）发现新文。
- AI 引擎：用 WorkBuddy 内置 AI，由 automation 定时驱动；run.py 降级时顶层 AI 兜底总结。
- 输出：Obsidian（OBSIDIAN_VAULT_PATH 已配）+ 飞书（FEISHU_WIKI_* 已配），双写。
- 抓取策略：**按内容发布时间做「时间窗口」过滤**（首跑 `BILI_FIRST_WINDOW_DAYS`=7 天 / 每日 `BILI_DAILY_WINDOW_DAYS`=1 天），单页拉满 `BILI_PAGE_SIZE`=50；不在首跑硬取"最近 N 条"。详见 `monitors/README.md`。
- 调度：每日 **10:00 与 17:00** 各跑一次（automation `FREQ=DAILY;BYHOUR=10,17;BYMINUTE=0`）。
- 去重：本地 `monitors/state.json` 记录已处理 文章id / BV / 专栏 id / dyn:id，per-source 裁剪（`STATE_KEEP` 默认 1000）防膨胀。
- 路由：公众号文章→`articles.skill_main`；B站视频→`videos.summarize_video`；B站专栏(cv)→`articles.skill_main`；B站动态→`articles.skill_main`（短动态走轻量「速览」）。

## 不做什么
- 不自己逆向微信读书私有API（复用 weread.111965.xyz 代理）。
- 不接外部 AI key（本轮用内置 AI）。
- 不回溯全部历史：默认只处理时间窗口内的内容（首跑 7 天）；如需更大窗口调 `BILI_FIRST_WINDOW_DAYS`。

## 实现状态（2026-07-22，已落地并经本机验证）
- `monitors/` 包已落地：state.py（去重+裁剪）/ wechat.py（WereadClient+WechatSource+token 自愈）/ bilibili.py（**B站官方 API + WBI 签名，不依赖 RSSHub**）/ ad_filter.py（广告过滤）/ run.py（CLI）/ _auth.py（扫码登录）。
- `monitors/run.py` 默认输出新内容 JSON；`--apply` 直接调总结管线。
- 认证：B站用根目录 `.env` 的 `BILI_COOKIE`（登录态 Cookie，动态接口硬性要求）；公众号用 `monitors/.wechat_auth.json` 里的 JWT（转发服务器签发，数小时失效，`run.py` 自动检测失效并弹码续期）。
- 订阅配置：monitors/subscriptions.json（参考 subscriptions.example.json）。
- 单测 tests/test_sub_monitor.py（mock 网络层，19 用例全绿）。
- 运营细节与已知坑见 `monitors/README.md`（时间窗口、降频、新鲜度标签、短动态轻量、state 裁剪、断跑丢内容、公众号 token 不稳定等）。
