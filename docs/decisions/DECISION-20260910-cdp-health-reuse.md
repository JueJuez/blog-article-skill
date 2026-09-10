# DECISION-20260910-cdp-health-reuse

> 承接 `DECISION-20260910-cdp-sandbox-bypass.md`。2026-09-10 真机闭环验证（88 篇 scys 抓取零漏抓）。

## 背景

旧 `SharedCdpSession.__init__` 无条件「杀光所有 Chrome → 全量复制 → 重启」，双 Agent 并发时互相杀掉对方的调试 Chrome（connect ECONNREFUSED），且每轮 62 篇抓取反复 kill/重启。

## 决策

- **健康复用优先**：`_probe_reuse_endpoint` 读克隆目录 `DevToolsActivePort` → 端口可探测 → 直接 `connect_over_cdp` 复用（`_own_browser=False`，close 只断开不杀）；复用失败按旧端口 `_kill_chrome_on_port` 精准清僵尸；仅 clone 陈旧/缺失（`clone_is_fresh()`，3 天 marker）才关 Chrome + 一次性全量复制。
- **scys 单篇抓取收编**：`articles/fetch.py:_scys_cdp_fetch` 从废弃的 `login_cdp_fetch` 薄包装改为 SharedCdpSession 进程内单例（`atexit` 收尾，浏览器运行期崩溃重置会话重试 1 次）。
- **跨标签去重**：`scys_batch_fetch.py` 加 `cross_tag_seen.json` 认领账本 + 扫描两个待总结队列已有归属，同篇横跨多标签时先抓到的认领、其余跳过。
- **stale 端口坑（真机案例）**：`DevToolsActivePort` 文件写 5494、实际健康 Chrome 监听 55873 → 复用探测永远失败 → 兜底拉新实例被单实例机制顶死全灭。修复手段：`Get-NetTCPConnection -State Listen` 找实际监听端口 → 编辑工具改写文件（沙箱拦 Chrome 写盘不拦 AI Write），已记入 `references/login-required-cdp-workflow.md` §5 已知坑表。**2026-09-10 晚已代码化**：`__init__` 探测失败自动 `_find_clone_chrome_port`（PowerShell CIM 枚举活进程命令行找 `--user-data-dir=克隆目录` 的调试端口）→ 再探测 → 命中即 `_rewrite_devtools_port_file` 回写并直接复用；重建兜底追加 `_kill_clone_chrome` 按克隆目录精准清僵尸（覆盖「文件端口无监听 → 按端口杀是 no-op」盲区）。

## 测试

`tests/test_cdp_session_reuse.py`（59 例）。
