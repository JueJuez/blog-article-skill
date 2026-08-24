# DECISION-20260824: Chrome 151+ 废弃 junction 方案，改用 ProfileClone

## 背景
Chrome 151 能检测 junction（DebugUDD→User Data）指向同一物理目录，触发安全清理：
extension_garbage_collector 删扩展文件 + 清 Google 账号关联。实测 22 个扩展被删。

## 决策
- 废弃 junction + 焊快捷方式 flags 方案
- 新方案：profile_clone_fetch.py 用持久化 ProfileClone 目录（非默认 dir → Chrome 151+ 放行）
- ensure_profile_clone()：首次全量复制 ~16GB，后续只同步 9 个 cookie 文件（秒级）
- login_cdp_fetch.py CDP 探测失败自动回退到 profile_clone_fetch
- scys_batch_fetch.py 同样自动回退

## 排除的方案
- login_persistent_fetch.py：launch_persistent_context 在默认 dir 上被 Chrome 151+ 拒（--remote-debugging-pipe 同样受限）
- junction 重新创建：Chrome 151 会再次删除扩展

## 验证
- 单篇 scys 抓取：21265 字，无登录墙 ✓
- 批量 scys 列表：30 篇，25 精华 ✓
- 批量 scys 单篇：1322 字，无登录墙，外部飞书文档也抓到 ✓
- 增量同步：第二次运行 18.9 秒（vs 首次 ~110 秒）✓
