# DECISION-20260909 监控轮次接入 B站 cookie 失效检测

**日期**：2026-09-09

**问题**：B站监控的 cookie 失效表现是 **-101/空数据**而非 412，补齐管线那套「命中 412 后被动轮换」钩子（`videos/fetch.py`）在监控场景永不触发 → cookie 死后监控每轮空转，动态接口大概率 -352，用户只看到莫名异常。

**决策**：在 `monitors/bilibili.py` 新增 `refresh_cookie_if_dead()`（主动探测）+ `cookie_health_label()`（健康度展示），`monitors/run.py:discover_all` 在有 B站订阅时轮次开始调用：

- 复用 `videos.fetch.rotate_bili_cookie_if_dead`（trigger="monitors"）：nav isLogin 探测，失效自动 CDP 从本机 Chrome 轮换并写回 `.env`/`.cache`；额外开销 ≈1 次 nav/轮；
- 轮换成功后刷新模块 `_COOKIE` + `os.environ["BILI_COOKIE"]` + 置空 `_SESSION`（否则进程内缓存让整轮继续用死 cookie；环境变量保证子进程继承新 cookie）；
- 未配置 `BILI_COOKIE` → 跳过检测（轮换会 kill 用户 Chrome，游客态用户不应被每轮惊动）；
- 异常一律吞掉只告警，绝不阻断监控主流程；挂在 `discover_all` 而非 `main`，串行/并行模式天然两态覆盖。

**展示**：健康度行仅异常态出现 `cookie已轮换`（♻️ 已自动修复）/ `cookie检测失败`（⚠️ 需登录本机 Chrome 后重跑），正常态静默。

**测试**：`tests/test_monitors_cookie_check.py`（8 用例：valid/rotated/failed/缺 cookie/异常吞掉/健康度映射/discover_all 接线两态）。
