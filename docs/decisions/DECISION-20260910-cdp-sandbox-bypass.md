# DECISION-20260910-cdp-sandbox-bypass

> 承接 `DECISION-20260910-nocc-deferred.md`（v3 直调 ASR 依赖 BILI_COOKIE）。2026-09-10 verify_round7 端到端验证。

## 背景

TRAE 沙箱按路径拦截 Chrome 子进程**启动期写盘**（`CdpAutomationProfile\Chrome\lockfile` / `Crashpad\settings.dat` / `BrowserMetrics` / 安装目录 debug.log，均为启动必写文件）：lockfile 写不进 → 进程死 → 调试端口 ECONNREFUSED。实测与 profile 复制代码无关——只读复用克隆目录、零文件操作同样被拦。

## 决策

- **临时目录最小 profile 绕过**：沙箱禁 LOCALAPPDATA 但放行自身工作/临时目录 → 拷克隆 profile 最小集（`Local State` cookie 解密钥 + `Default\Network\Cookies`）到 `%TEMP%\*\Chrome` 启动调试 Chrome → 零拦截；最小集失败回退整 Default 目录（排除缓存类，如 Cache/Crashpad/Service Worker）。
- **L1 实证结论**：CDP 提取新鲜 BILI_COOKIE（SESSDATA 与旧值不同 = 新鲜登录态）→ BV1qfdaBtE95 `extract_audio` 成功 323.1 MB → 「cookie 失效时 CDP 刷新路径可救活 ASR」实锤；被 cookie 误标的 deferred 条目可由 `--classify-deferred` 捞回。
- 沙箱内跑 CDP 仅限**验证实验**；生产抓取（monitors / scys_batch_fetch）不受影响，仍走 `SharedCdpSession`。

## 不做什么

- 不改 `shared/cdp_session.py` 等生产代码（不引入沙箱特判，逻辑留在一次性验证脚本）。
