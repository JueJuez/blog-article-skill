# PLAN-20260919 · weread 源模块（微信读书直连公众号源）+ 半自动过码

> **触发词：「开发weread源模块」** —— 用户说出此词即开工：通读本文档 → 按第 4 节任务清单开发。
> 本文档自包含：所有探索结论（2026-09-18/19 两日实测）已内联，**不需要重新探索、重新试错**。
> 深读补充：`references/weread-direct-source.md`（实测真源）+ `references/wechat-mp-sources.md`（外部资料索引）。

## 1. 背景与目标

原公众号源 = wewe-rss 公开代理实例，2026-07-20 随 Deno Deploy Classic 下线而死，
`monitors/run.py` 的公众号源因此被 `WECHAT_SOURCE_ENABLED=0` 停用（默认停用，见 run.py:157）。

2026-09-18/19 探索（CDP 抓包 + 登录态实测）已把「微信读书官方接口直连」全线打通：
**列表（含翻页）、正文直链、签名机制、防封纪律全部验证完毕**。本计划把它包成生产模块：

- **A. weread 源模块主流程**：替代死掉的代理源，接入 monitors 管线（发现 → 入总结队列）
- **B. 半自动过码模块**：weread 触发图像点选验证码时的「截图 → 模型识别 → 脚本点击」半自动通路

## 2. 已验证事实（硬结论，2026-09-19，勿再试错）

### 2.1 接口清单（本项目登录态实测）

| 接口 | 状态 | 说明 |
|---|---|---|
| `GET /web/mp/articles?bookId=<MP_WXS_x>&offset=N` | ✅ **登录态 200** | 列表主路径；未登录返回 `{"errCode":-2041}` |
| `GET /api/mp/cover?bookId=` | ✅ 200 | 免签名、免登录也可用（requests 直调），只返回最新 1 篇 |
| `GET /web/mp/content?reviewId=` | ✅ 200 | weread 转存正文；**生产不用**（用户定策：防高频，见 §2.4） |
| `GET /api/mp/articles` | ❌ 404 | 路径不存在 |
| `GET /web/shelf/bookIds?bookIds=` | ✅ 200 | 查订阅状态（onShelf 0/1） |

### 2.2 -2041 真相（三次错误假设全部排除）

- ❌ 不是缺签名：前端带实时生成的正确签名仍 -2041
- ❌ 不是未订阅：addToShelf 成功后仍 -2041
- ❌ 不是接口废弃：登录后同 URL 200
- ✅ **真凶 = 未登录**。安全检测/游客态 → -2041；扫码登录后 → 200
- 附加：`x-wrpa-0` 签名**非一次性**（同一值重放成功）；`x-wr-ticket`/`x-wr-randstr` **非必须**
  （localStorage 里找不到它们，登录 cookie 才是门槛）

### 2.3 已验证的页内调用代码（直接复用，勿改）

在 `weread.qq.com` 页面上下文内（SharedCdpSession 接管的 CDP Chrome）：

```js
// 签名：window.__WRPA__.sr(url) 返回数组，join(',') 即头值（本次实测 16 字符也通过，长度不用纠结）
const sig = window.__WRPA__.sr(api).join(',');
// 请求：
const r = await fetch('https://weread.qq.com' + api, {
  credentials: 'include',   // 自动带登录 cookie —— 这是 200 的关键
  headers: {Accept: 'application/json, text/plain, */*', 'x-wrpa-0': sig}
});
// 200 响应结构：
// {reviews: [{createTime, subCount, subReviews: [{review: {
//   reviewId: "MP_WXS_<bid>_<token>", createTime, belongBookId,
//   mpInfo: {title, originalId(=token), pic_url, mp_name, time, readNum, likeNum}}}}]}
```

**翻页规则（实测 offset 0/20/50/100 四页）**：每页固定 **20 条**，页间时间无缝衔接。
**正确翻页 = `offset += len(reviews)`，翻到空列表即到底**。
⚠️ issue #442 所说「50 步长」实测会**跳过每页之间约 30 条**，不可用。

### 2.4 正文路径（用户定策）：只用 mp 直链

- reviewId 后段 = 原文 token（`reviewId = MP_WXS_<bid>_<token>`，title 与 mp 页面逐字比对已验证）
- 正文一律走 `https://mp.weixin.qq.com/s/<token>` → 项目既有 `fetch_web_content` 管线
- **不用 `/web/mp/content` 兜底**：每篇都打 weread 同域会放大账号风控暴露面；
  宁可本轮跳过下轮重试，也不为单篇消耗 weread 配额

### 2.5 bookId 对照表（来源 `monitors/.mp_cache.json`）

| 公众号 | bookId |
|---|---|
| 中金点睛 | `MP_WXS_3270332840` |
| DeepVan的逃生地牢 | `MP_WXS_3905719449` |
| 哥飞 | `MP_WXS_2399233620` |
| 生财有术 | `MP_WXS_3891906515` |

新增号拿 bookId：无在线解析途径，手工在微信读书搜索后从页面请求抄 `MP_WXS_` id
（或用 `/api/mp/cover` + 页面探索，开发时定）。

### 2.6 登录态现状

- 克隆 profile（`%LOCALAPPDATA%\CdpAutomationProfile\Chrome`）**已含 2026-09-19 扫码登录态**
  （wr_skey/wr_vid/wr_rt 等在 weread 域 cookie）
- cookie 有效期**不猜**（无官方文档），策略见 §3.3

### 2.7 实操坑（本会话踩过）

1. **`SharedCdpSession` 单持有者关灯**：每次 `with` 退出且本进程是唯一持有者 → 杀 Chrome。
   多步操作（开页→导航→fetch×N）必须放**同一个 with 块**内完成
2. **`s.page` 不一定是 weread 页**：可能是新标签页。用
   `next((p for p in s.context.pages if 'weread.qq.com' in p.url), None)` 定位，
   或 `s.context.new_page()` 后自行导航
3. 探针类脚本（`scripts/weread_probe.py` 等）是**探索工具**，不是生产路径；
   生产模块不要依赖它们

## 3. 纪律红线（开发时必须内置，非可选）

### 3.1 防封七条（详见 wechat-mp-sources.md §9）

1. 个人单账号、低频、自用；**不做号池/轮换**
2. 频率：列表/正文间隔 ≥2s；探索性 ≥10s；**单日十几请求封顶**；订阅场景 = 每号每天 1 次列表
3. 加号/关注操作放慢（加号太快 → 小黑屋 24h）
4. 请求头全套模拟浏览器（页面内 fetch 天然满足）
5. **页面内 fetch 优先**（同源带 cookie + 页面签名）；requests 直调仅限免签名 cover
6. 遇验证码/「安全检测中」→ 立即停手 + 截图 + 报告，**不硬闯**
7. 提量前先小批试探（平台收紧很快）

### 3.2 cookie 策略（用户定策，2026-09-19）：不猜、观测上报、等反馈

- 无官方文档 → **不做任何时效假设、不自动刷新、不猜着换 cookie**
- 每次运行先打 1 次列表请求：成功 → 走管线；
  失败（-2010/-2012/-2041 或任何异常）→ **原样记录错误码与页面现象**（是否「安全检测中」
  /二维码），标记本轮跳过，**把现场报给用户**（健康度行 + 日志），等用户反馈后人工处置

### 3.3 边界（明确不做）

- ❌ 全自动后台过码（无人监督乱点风险；等半自动跑稳再议）
- ❌ tesseract OCR（验证码是**图像点选类**，用户确认；文字 OCR 无解）
- ❌ weread content 正文兜底（§2.4）
- ❌ 号池/多账号轮换

## 4. 开发任务清单

### A. weread 源模块（主流程）

**建议落点**：`monitors/weread.py`（新文件），由 `monitors/run.py: discover_all` 调用，
**替代**被停用的旧代理公众号源（`monitors/wechat.py` 走的是死掉的 wewe-rss 代理，保留不动，
新开关或复用 `WECHAT_SOURCE_ENABLED` 语义——开发时定，见 §5 决策点）。

流程（每号）：

1. 经 `shared/cdp_session.SharedCdpSession` 拿登录态页面（**一次会话处理全部号**，见 §2.7 坑 1）
2. 页内 fetch 列表（§2.3 代码），每号从 `offset=0` 起
3. **增量判断**：`reviewId` 进 `monitors/state.json` 的 `seen`（或独立键），`createTime` 做日志展示
4. 新文章：reviewId → token → `https://mp.weixin.qq.com/s/<token>` →
   入 `monitors/pending_summaries.json` 队列。**入队必须走既有 `_queue_pending_summary`**
   （三队列统一 prompt 预计算：模板分类 + 篇幅目标块 + QUALITY_GATE_SELFCHECK，见 AGENTS.md 能力 2）
   ——队列路径有落盘门禁 retry 契约（VERIFIER_FAILED 重写 ≤2 次）
5. 正文抓取不在本轮 weread 模块内做：队列消费（子 Agent 总结）时由既有管线按 mp 直链抓正文
   （与普通公众号文章同路径；**确认** `fetch_web_content` 对 mp 直链的既有处理即可，勿重写）
6. 健康度行追加 weread 段（成功 N 号 / 新文章 M / 失效/异常号清单）

### B. 半自动过码模块

**建议落点**：`scripts/weread_captcha.py`（检测+截图+点击执行），识别由会话内执行模型完成
（FORCE_AGENT_MODE 架构：脚本出产物，模型做语义）。

1. **检测**（weread 源模块每轮开头做一次）：页面出现「安全检测中」遮罩 / 验证码 iframe /
   或列表接口返回 -2041 → 判定触发
2. **截图存档**：`_tmp/weread_probe/captcha-<ts>.png` + 日志记录
3. **停手**：本轮该号跳过，健康度行报「weread: N 号正常，M 号触发安全检测（已截图）」
4. **半自动过码**（用户或下一会话在场时）：
   - 会话内模型 Read 截图 → 识别点选目标与顺序 → 输出**坐标序列**（相对截图的像素坐标）
   - 脚本按坐标依次点击（Playwright `page.mouse.click`，注意截图分辨率与视口缩放比换算）→ 提交
   - 重试 ≤2 次（识别错误时换新码重来）；仍失败 → 保持停手，转人工
   - 过码成功后重跑一轮 weread 源即可
5. 骨架参照（只抄流程，不抄 OCR）：
   `D:/Code/remote_zk/qin/qin-ai/skills/qin-modules/dxm-0.1.0/skills/dxm-login/login.js`
   的重试循环 + 成功判定写法
6. 自适应限流可选增强（值得抄骨架）：
   `D:/Code/remote_zk/qin/qin-ai/skills/qin-modules/dxm-0.1.0/skills/dxm-core/dxm-crawl-guard.server.ts`
   ——连续拦截→冷却+间隔翻倍+配额减半，成功→缓慢恢复，状态持久化。Python 版同样思路

### 测试

- 单测（mock，不发真请求）：翻页累加逻辑（offset += len(reviews)、空停止）、
  seen 去重、错误码分类（-2010/-2012/-2041 → 观测上报路径）
- 真环境验收（低频，全程 ≤ 十几请求）：哥飞单号跑通「列表 → 入队 → 总结 → 落 Obsidian」闭环；
  健康度行正确；**不触发风控**

## 5. 决策点（开发会话按推荐执行，用户可否决）

| 决策点 | 推荐 |
|---|---|
| 首跑语义 | 首跑只把最新 1 页（20 条）的 reviewId 写进 seen（建立基线，**不回填总结历史**）；历史回填另起任务（走低频翻页，多天分摊） |
| 开关 | 新增 `WEREAD_SOURCE_ENABLED`（默认 0），与旧 `WECHAT_SOURCE_ENABLED` 并存——旧开关语义已绑定死代理，复用会混淆 |
| 订阅配置 | `subscriptions.json` 公众号条目**不改**；weread 模块独立维护 bookId 映射（首发 4 号硬编码自 §2.5 表 + 读 `.mp_cache.json`），后续再决定是否并入 subscriptions |
| 每号请求量 | 每号每天 1 次列表（offset=0 一页，20 条足够日增量的检测）；翻页只在手动补齐任务时用 |

## 6. 参考文件索引

| 文件 | 用途 |
|---|---|
| `references/weread-direct-source.md` | **实测真源**（本计划的事实依据，含接口表/签名/事故记录） |
| `references/wechat-mp-sources.md` | 外部资料索引 + §9 防封纪律 |
| `monitors/run.py`（discover_all / _queue_pending_summary） | 挂载点与入队契约 |
| `monitors/README.md` | 监控运营细节 |
| `shared/cdp_session.py` | CDP 会话（§2.7 两个坑必读） |
| `scripts/weread_api_probe.py` | 页内 fetch 参照实现 |
| `scripts/weread_cookie_export.py` | 登录态导出/自测（失效后重新扫码时用） |
| `D:/Code/remote_zk/qin/.../dxm-crawl-guard.server.ts` | 自适应限流骨架（可选抄） |
| `D:/Code/remote_zk/qin/.../dxm-login/login.js` | 过码重试循环骨架（只抄流程不抄 OCR） |
