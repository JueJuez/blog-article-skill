# 术语表（Glossary）

状态：现行术语。命令见 [PIPELINES](../docs/PIPELINES.md)，动态阈值与运行状态以代码和现场为准。

## 内容与归档

- **系列课（series）**：账号发布的成套视频。按订阅规则或官方合集映射识别；单集逐 URL 走普通管线，防重靠登记表。点名整季补齐走 `backfill_series.py`。
- **系列容器**：承载系列单集笔记的目录或飞书节点；路径由 `shared/routing.py` 统一计算。飞书双写时注意账号层与系列层不能重复嵌套。
- **【待归类】**：无法识别作者与分类时使用的收件箱。可识别归属的单篇直接走作者或监控路由，不是所有单篇的默认中转站。
- **日更**：监控账号下非系列内容目录 `【监控】/<平台>/<账号>/日更/`。
- **总览**：系列或账号的索引笔记。旧飞书总览维护脚本已冻结，不能当现行生产范例；按当前系列入口维护。
- **scys（生财有术）**：按领域抓取的社群来源；领域映射与窗口配置在 `scripts/scys_projects.json`，订阅覆盖在 `monitors/subscriptions.json`。

## 队列与状态

- **monitors 待总结队列**：`monitors/pending_summaries.json`，含普通文章、视频和系列单集；条目含原文位置、prompt、模板及目录。
- **scys 待总结队列**：`notes/_scraped/scys/pending_summaries.json`，稳定键为 topicId。与 monitors 队列统一经 `save_pending.py` 保存，再由编排方清队列。
- **待重抓队列**：`monitors/pending_refetch.json`，抓取失败项跨轮重试；入口 `monitors/run.py --refetch-only`。和“有原文待总结”不同。
- **登记表 / dedup**：`articles/dedup.py` 按 URL 或正文键判重；本机登记表在 `notes/_meta/summary_registry.json`。seen 表示发现过，不能证明已总结落盘。
- **状态 Ledger**：`monitors/run_status/` 的运行状态记录，用于观察与复盘；不是成品登记表，旧 failures / redrive 子系统已退役。
- **in-place 重做**：按已知 `note_path` 覆盖旧文件；不同于按标题重新算文件名后覆盖同名。入口 `resum_save_batch.py`，见 PIPELINES §3。

## 抓取与质量

- **SharedCdpSession**：项目的登录态会话薄适配层；实例内核在用户级 `cdp-automation-profile`。健康实例优先复用，close 只断开连接，克隆缺失或过期才重建；不每日强制复制。
- **bvid / topicId**：B站视频 / scys 帖子的来源标识。系列集号不能替代 URL 判重；本地 raw 元数据疑似串位时须与原始来源核对。
- **ASR**：无 CC 字幕时下载音频再本地转写；后端、分段与并发以 `videos/asr.py` 和 [ASR 手册](asr-bilibili-sandbox.md) 为准。UP 批量默认跳过 ASR，显式 `--with-asr` 才启用。
- **机械 / 内容门禁**：机械缺陷硬拦；结构、锚点和照搬信号按内容分类硬拦；形态信号软抽检。规则见 [RULES](../RULES.md) §4.6，语义忠实度仍须模型审阅。
- **原文与成品**：`notes/_scraped/` 是抓取材料，Obsidian 是默认成品目标。原文文件、发现计数或 HTTP 成功均不能单独证明总结完成。
