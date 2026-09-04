# 术语表（Glossary）

> 新 Agent 上手时遇到的不自解释概念，集中定义于此。以代码与 `monitors/`、`articles/`、`videos/` 实际实现为准；如与代码冲突，以代码为真相源。

## 核心概念

- **系列课（series）**：某个 B站UP主发布的成套视频（如课程、专栏）。监控时按 `monitors/subscriptions.json` 的 `series_patterns` 识别，增量只抓未总结的集、已总结的旧集不重复。
- **系列容器（series container）**：飞书知识库里承载某个系列课所有集笔记的节点，挂在 `【监控】/<平台>/<UP>/<系列名>/` 下（不是根）。由 `shared/routing.py:resolve_folder` 统一算路径；`apply_pending_series.py` 传给 `_save_series_note` 的 `folder` 只到账号层，系列容器由 `ensure_series_node` 单建，避免嵌套。
- **【待归类】收件箱**：非系列内容首次落盘的统一中转节点，用户后续手动拖到分类。单篇默认落此；系列课自带子目录不进。飞书分类节点与 Obsidian 分类文件夹一一对应。
- **总览文档（overview doc）**：每个账号容器下按发布时间倒序、幂等去重的有序索引文档，弥补飞书 wiki 无 `sort_order` 的缺陷。存量重建见 `scripts/rebuild_overviews.py` / `promote_existing.py`。
- **日更节点**：非系列内容落 `【监控】/<平台>/<账号>/日更/`（纯名无【】）。

## 队列与索引

- **三 pending 队列**：
  - `monitors/pending_summaries.json`：待总结的单篇（文章/视频）。
  - `monitors/pending_series.json`：待总结的系列课（原始分片已落 `notes/<系列名>/*_raw.md`）。
  - `monitors/pending_refetch.json`：抓取失败待跨轮重抓的项；失败项进此队列、下轮开头重载优先重抓，连续 `WECHAT_MAX_REFETCH=3` 次失败才显式 drop 上报。
- **dedup 索引**：按规范化 URL / 正文 hash 持久化的去重索引，重复链接/原文自动跳过，避免重复消耗 token。
- **状态 Ledger（status ledger）**：并行模式下 `monitors/run_status/` 记录每次运行各源状态（整体 ok / 部分失败），用于故障复盘与去重。

## 外部源与机制

- **scys（生财有术）**：付费社群，按领域标签（项目）抓取。领域→menuId 映射在 `scripts/scys_projects.json`，抓取窗口以该 JSON 的 `defaults.since_days` / `monitors/subscriptions.json` 的 scys 条目覆盖为准（不写死）。触发词「补齐 scys / 补齐生财有术」启动全流程。
- **SharedCdpSession**：`shared/cdp_session.py` 的登录态抓取唯一路径——关 Chrome → 全量复制 profile 到非默认 `CdpAutomationProfile\Chrome` 目录（经 `ensure_cdp_profile.py` 管理，每天首跑全量、当天复用）→ 调试端口启动克隆体 → `connect_over_cdp` 接管。取代已废弃的 junction / 活 Chrome 调试端口方案（Chrome 151+ 默认目录开调试端口会被拒）。
- **bvid**：B站视频 ID。**raw 头与 body 的 bvid 可能复用错误**（小猪仔系曾全串成同一 bvid）。集号↔bvid 唯一可信来源是 `scripts/reconcile_series_bvid.py:fetch_season`（取 `ugc_season` 真源有序列表），绝盲信 raw 头或任何本地 collection json。
- **ASR 兜底**：视频无 CC 字幕时自动下载音频 + 本地 Whisper 转写；CUDA cublas 缺包自动回退 CPU，并发由 `detect_asr_max_concurrency()` 决定（有卡=2 / 无卡=1）。

## 状态字段（系列课 manifest）

- `notes/<系列>/_manifest.json` 的 `episodes` 是 **dict**（key=集号，非 list）；每集 `state` 取值：
  - `raw_ready`：仅有原始分片，未总结。
  - `landed`：body 已落盘但未读回校验。
  - `verified`：已校验完整。
- 查缺口用 `state` 字段（**不是 `status`**）；队列空（`pending_series.json` 为空）不等于无缺口——未 verified 的集根本没进队列，须实读 manifest。
