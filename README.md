# Blog Article Skill

将文章、视频、字幕及订阅内容转成结构化笔记，默认归档到本地 Obsidian。支持 B站、YouTube、微信公众号和生财有术；开源项目抽取另存项目库，不自动总结原文。

默认 `FORCE_AGENT_MODE=1`：脚本负责抓取、去重、模板选择和落盘，当前会话模型负责总结。抓取命令可能返回 `need_continue_summary` 或产生待总结队列，这不表示笔记已经生成。

## 安装与配置

需要 Python 3.10 或以上。基础与可选依赖以 [pyproject.toml](pyproject.toml) 为准。

```powershell
pip install -e .
# 网页提取与视频字幕按需安装
pip install -e ".[extract,video]"
# Whisper 转写按需安装，另需 ffmpeg
pip install -e ".[asr]"
Copy-Item .env.example .env
```

已有 `.env` 时直接编辑，勿用模板覆盖。至少检查：

- `OBSIDIAN_VAULT_PATH`：成品库路径。
- `OBSIDIAN_WRITE=1`、`DISABLE_FEISHU_SYNC=1`：本地写入，关闭飞书。
- `FORCE_AGENT_MODE=1`：交由会话模型总结；外部 Provider 是可选模式。
- `BILI_COOKIE`：B站登录态；公众号直连须启用 `WEREAD_SOURCE_ENABLED`，并具备浏览器登录态。

完整配置见 [配置参考](references/config.md)；浏览器路径见 [CDP 工作流](references/login-required-cdp-workflow.md)，转写环境见 [ASR 手册](references/asr-bilibili-sandbox.md)。真实配置与私人笔记不入库。

## 常用入口

在仓库根目录运行：

```powershell
python articles/run.py "<文章URL>"
python articles/run.py --content "<原文>"
python videos/run.py --url "<视频URL>"
# 日常增量：B站 + scys
python monitors/run.py --mode auto --apply
# 公众号：需有人在场完成登录，撞验证码由会话模型识图
python monitors/run.py --mode weread --apply
python scripts/export_note_html.py "<笔记路径>"
python tools/project_import/assets/main.py "<仓库URL>"
```

完整入口、历史补齐、存量重做、迁移与常见误用统一查 [docs/PIPELINES.md](docs/PIPELINES.md)。简短示例不替代完整流程；`--apply` 后仍须消费待总结队列。

## 总结与落盘

合格长源默认「提炼 → 写作 → 既有机械/内容门禁 → 原入口落盘」，`NOTE_DISTILL_DISPATCH` 默认开启、显式 `0` 可关闭；不适用源仍走原单轮。提炼与写作由会话 Agent 编排，完整材料包已含未选区间时，写作默认不再整篇读取原文。单篇按 PIPELINES §2 交接，批量按 §4 先刷新队列、缺包先提炼，再由子 Agent 写作并经 `scripts/save_pending.py` 保存；成功后编排方统一清队列。

可用 `python scripts/filter_pending.py --dry-run` 只读预览类型差异。覆盖自查、定向补稿、纯插入核验与独立审核等旧组合步骤全部保留，按需显式启用；当前默认不逐篇调用。详见 [PIPELINES](docs/PIPELINES.md) 与[监控说明](monitors/README.md)。

模板和质量纪律由 `prompts/templates.py` 维护；落盘经过机械与内容门禁，失败须修稿或明确报告。飞书双写、镜像及本地兜底见 [RULES.md](RULES.md) §3.0。

## 文档导航

| 目的 | 文档 |
|---|---|
| 新 Agent 开工与强制纪律 | [AGENTS.md](AGENTS.md)、[RULES.md](RULES.md) |
| 找入口、区分入口与零件 | [docs/PIPELINES.md](docs/PIPELINES.md) |
| 文档职责、模块边界、计划状态与维护方式 | [docs/README.md](docs/README.md) |
| 订阅、队列闭环、增量与补齐 | [monitors/README.md](monitors/README.md) |
| 公众号登录、请求统计与风控 | [weread 手册](references/weread-direct-source.md) |
| 生财有术抓取 | [scys SOP](references/scys-fetch-sop.md) |
| 配置、术语、测试 | [配置](references/config.md)、[术语](references/glossary.md)、[测试约定](references/testing_rules.md) |
| 总结流程决定与已完成开发记录 | [四步主流程决定](docs/decisions/DECISION-20261029-distill-write-default.md)、[完整计划归档](./_archive/plans/PLAN-20261030-intent-tree-summary.md)；其他计划及历史见 docs/README |
| 决策与历史 | [决策索引](docs/decisions/README.md) |
| 开源项目库 | [项目抽取说明](tools/project_import/README.md) |

## 验证

```powershell
python -m pytest tests/ -q
python scripts/audit_pipeline_coverage.py
```

测试验证代码约束；覆盖审计只检查入口是否被提及，不能证明描述正确。真实抓取依赖登录态、配额与外部服务，须单独记录验证结果。

## 许可证

MIT（沿用原 README 声明；仓库当前未提供独立 LICENSE 文件）。
