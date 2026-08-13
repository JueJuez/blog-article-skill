# DECISION-20260813-series-resilience

## 背景
系列课「股市系统教学」(B站 ugc_season, 38 集) 全自动落盘过程中，会话重置把后台 drain / ASR 任务杀掉，流程停在**半中断态**：25 集已落飞书、10 集(29–38)本地未落、3 集(5/15/18 无字幕)从未产出。根因不是单点 bug，而是整套流程假设「一次性跑完」，**缺少幂等续跑的状态机 + 本地源保护 + 单一救回入口**，任何中断都留下需要人工对账的脏状态。

## 根因（8 项）
1. 无字幕集(5/15/18)缺兜底 → `fetch.py` 的 yt-dlp 分支失败即 `return None`，ASR 死代码未接通；依赖未装。
2. 后台任务被会话重置杀掉 → 半中断态，只能手动对账飞书 vs 磁盘。
3. drain 落盘后**立即 `os.remove` 本地 raw/body** → 本地源成为「已落盘」唯一信号，删后无法续跑/重推。
4. `pending_series.json` 被清成 `[]` → 队列是隐形契约，覆盖无痕迹、不自愈。
5. 16/17 集出现 `.body.md` 与 `_body.md` 重名 → drain 只认 `.body.md`，更优内容被忽略。
6. 救回脚本引用不存在的 `segments_to_text` → 临时脚本与真实 API 漂移会崩。
7. ASR 首跑卡 CPU 1h5m → 沙箱未暴露 CUDA / 进程掉 CPU；shell `timeout` 未生效。
8. 后台任务 ID 丢失 → 无法判断完成否。

## 决策（全套 8 项，代码门禁非记忆）
- **A. 持久化 manifest 状态机** `shared/series_manifest.py`：`pending→raw_ready→summarized→landed→verified`，单向不倒退。`load_or_init(..., reconcile=True)` 做**磁盘+飞书双对账自愈**——扫磁盘 raw/body 推进状态、读飞书容器把已存在节点标 verified。取代「文件存在即续跑信号」。
- **B. 本地源不提前删** `monitors/apply_pending_series.py`：落盘后只更新 manifest，不删本地；删除挪到 `--cleanup`，且仅当 manifest=verified 且 `_verify_node_present` 回读确认才删。
- **C. 单一救回入口** `videos/rescue_episode.py`：给定(系列, 集号, BV/url) → 走 `fetch.fetch_bilibili_transcript`(含 3 层 ASR 兜底) → 写**与降级路径同格式**的 raw(前 7 行 `>` 元数据 + `---` + 正文，子 Agent 合约不变) → 登记 manifest=raw_ready。取代临时 `retry_missing.py`。
- **D. ASR 强制 CUDA + 硬超时看门狗** `videos/asr.py`：`transcribe_video` 检测 `ctranslate2.get_cuda_device_count()>0` 即锁 `device="cuda"`（绝不在无提示下掉 CPU）；`_start_watchdog(ASR_WALL_TIMEOUT, 默认 1800s)` 超时置位 Event，生成器每出一段检查即抛 `TimeoutError` → 单集超时中止，不再无限卡死。
- **E. 命名唯一来源 + 校验** `shared/series_naming.py`：`body_path/raw_path` 单一推导；`_fix_stray_naming` 扫描纠正 `*_body.md` 误命名。**坑：`.body.md` 是双后缀，`os.path.splitext` 只会切掉 `.md` 留下 `.body` 污染 base** → drain 必须用 `f[:-len('.body.md')]`。已踩中并修：29–38 集首轮落盘节点标题带 `.body`，已删脏节点重落。
- **F. 启动期依赖检查** `videos/asr.py:check_asr_deps()` + `fetch.py` ASR 兜底前预检：缺依赖打印一行 `pip install ...` 而非静默崩。另修 `fetch._bili_build_cookies_from_env` 只读 `BILIBILI_COOKIES`，补读本项目实际变量 `BILI_COOKIE`（否则 ASR 下音频拿不到登录态）。
- **G. 前台分批 drain + 进度日志** `apply_pending_series.py --batch 5`：每批串行落盘、单集异常不中断整轮，写 `notes/<系列>/.series_progress.log`，中断只丢当前批、进度可见。
- **H. 状态持久化替代后台追踪**：manifest 即续跑契约，不再依赖后台任务 ID。

## 不做什么
- 不破坏 monitors 的 `pending_series.json` / `series_state.json` 增量去重契约（manifest 与之并存，各管各的：去重 vs 续跑）。
- 不改 `FORCE_AGENT_MODE` 下「总结由执行模型完成」的分工。

## 验证
- manifest reconcile 正确识别：verified=25(已在飞书) / candidates=29–38(待落) / 5·15·18 不在 manifest(需救回)。
- 29–38 经修复后 drain 落盘并 `verified`，飞书节点标题与 1–28 一致（无 `.body`）。
- 5/15/18 走 `rescue_episode.py` → ASR(CUDA) → 总结 → drain 闭环（进行中）。
