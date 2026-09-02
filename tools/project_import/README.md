<p align="center">
  <h1 align="center">project-import</h1>
  <p align="center">从文章 / 视频 / 直接链接中抽取提到的开源项目，调研分类后归档到本地 Obsidian 项目库</p>
  <p align="center">
    <a href="#what">简介</a> ·
    <a href="#features">功能</a> ·
    <a href="#quick-start">快速开始</a> ·
    <a href="#usage">使用方式</a> ·
    <a href="#storage-format">存储格式</a> ·
    <a href="#configuration">配置</a> ·
    <a href="#project-structure">项目结构</a>
  </p>
</p>

## What

`project-import` 把 GitHub / Gitee 开源项目**调研分类后存入本地 Obsidian 项目库**（每个项目一个 `.md` + YAML frontmatter）。它可以从「直接给的仓库链接 / 文章链接 / 视频链接」中抽取提到的项目，采集 README 和 Stars 数据，经 LLM 分类评估后落盘。

### 为什么是本地 Obsidian 而非飞书

- **跨项目 agent 检索**：别的项目的 agent 不需要飞书 token 就能直接 `grep` / 解析 / embedding 这些 markdown，便于发现「哪些项目能用上」。
- **零鉴权、可版本化、可被 RAG / 全文检索直接消费**。
- 飞书多维表格仍保留为 `PROJECT_STORAGE=feishu` 时的**可选回退**写入（与笔记用的 `FEISHU_WIKI_SPACE` 变量不同、不冲突）。

## Features

- **多入口抽取** — 直接文本含仓库链接 / 文章 URL（从正文抠项目）/ 视频 URL（描述优先、字幕兜底）/ 小黑盒帖子（无头浏览器渲染正文）
- **小黑盒抓取** — `xiaoheihe.cn` 分享链接是 App 深链，通用爬虫拿不到正文；用 Node Playwright + 系统 Chrome 渲染后再抠仓库链接
- **按项目名搜索收录** — 只有项目名、没有确切地址时，调 GitHub 搜索 API 命中后收录（`--from-name`）
- **三层智能去重** — 批次内去重 + `imported.txt`（已入库）+ `pending_results.json`（待上传）联合去重
- **自动数据采集** — 获取 README 内容（raw / API 兜底）和 Stars 数量（GitHub / Gitee API）
- **LLM 分类评分** — 一次 Prompt 完成项目分类、功能领域、能力标签、核心亮点、文档/功能评分
- **本地归档（默认）** — 一项目一 `.md` + YAML frontmatter，置于 `PROJECT_LIBRARY_DIR`
- **飞书回退（可选）** — `PROJECT_STORAGE=feishu` 时写飞书多维表格
- **一次性迁移** — 从已有飞书 Bitable 迁到本地（`migrate_feishu_to_local.py`）
- **并发采集+分析** — 多个仓库的「采集+分析」并行执行（线程池，受 `BATCH_MAX_WORKERS` 控制）
- **汇总报告** — 处理完成后输出格式化统计报告（成功率/类型分布/失败明细）
- **收录质量门禁（默认关，opt-in）** — 需设 `QUALITY_GATE_ENABLED=1` 才生效；开启后，低星（< `QUALITY_GATE_MIN_STARS`，默认 100）或文档/功能评分双低（`doc_score` 与 `func_score` 均 < `QUALITY_GATE_MIN_DOC`/`QUALITY_GATE_MIN_FUNC`，默认 5）的项目**不自动入库**，先转入 `pending_review.json` 待复核队列，由人工确认后再决定；不设或置 0 时所有项目直接入库

## Quick Start

### Prerequisites

- **Python >= 3.10** — 运行辅助脚本
- **GitHub / Gitee API** — 无 Token 限流 60 req/h；批量使用建议设置 `GITHUB_TOKEN` / `GITEE_TOKEN` 提升到 5000 req/h（少量导入可不填）
- **LLM（全自动模式必填）** — 配置 `BATCH_LLM_API_KEY` 后阶段三自动调用外部模型分析；不配置则默认由**子代理**分析（执行模型隔离出的独立上下文，避免把 README 带进主会话）

### Install

```bash
cd tools/project_import
pip install requests
```

### Configuration

复制 `.env.example` 为 `.env` 并填入你自己的值（`.env` 已被 `.gitignore` 忽略，不会上传）：

```bash
cp .env.example .env
# 然后编辑 .env
```

关键变量：

```bash
# 本地项目库目录（推荐：Obsidian vault 根下的 开源项目/）
PROJECT_LIBRARY_DIR="D:/Code/Obsidian/obsidian-path/开源项目"

# 存储后端：local（默认，写 Obsidian markdown）/ feishu（回退写飞书多维表格）
PROJECT_STORAGE="local"

# 仅 feishu 模式需要：飞书 Base Token（裸 token 或 Wiki/文档链接，自动辨别解析）
FEISHU_BASE_TOKEN="https://xxx.feishu.cn/wiki/xxxx?table=tblXXXX&view=vewXXXX"

# 收录质量门禁（默认关闭，opt-in）：不设或置 0 则所有项目直接入库；
# 置 "1" 开启后，低质项目不自动入库，转入 pending_review.json 待复核
# QUALITY_GATE_ENABLED="1"        # 置 "1" 开启门禁；默认 "0"（关闭）直接入库
# QUALITY_GATE_MIN_STARS="100"    # stars 低于此值判低质
# QUALITY_GATE_MIN_DOC="5"        # doc_score 阈值
# QUALITY_GATE_MIN_FUNC="5"       # func_score 阈值
# 判定低质（任一成立即拦截）：stars < MIN_STARS  OR  (doc_score < MIN_DOC AND func_score < MIN_FUNC)
```

> 未配置本地库（`PROJECT_LIBRARY_DIR` 与 `OBSIDIAN_VAULT_PATH` 皆空）也未设 feishu 时，
> 结果会自动保存到本地 `pending_results.json`，配置后再运行会自动落库。

## Usage

### 直接给仓库链接

```bash
python assets/main.py "https://github.com/langchain-ai/langchain-mcp-server 还有 https://gitee.com/mirrors/axios"
```

### 从文章抽项目（从正文抠仓库链接）

```bash
python assets/main.py --from-article "https://blog.example.com/post/1"
```

### 从视频抽项目（描述优先，字幕兜底）

```bash
python assets/main.py --from-video "https://www.bilibili.com/video/BVxxxx"
```

> 视频场景**描述（简介）优先于字幕**找仓库链接：字幕常只提仓库名、地址在简介里。
> ⚠️ 视频只处理**简介 + 字幕**两种来源；不抓评论区、不做画面 OCR/截图识别。项目只出现在画面里时请用 `--from-name`。

### 从小黑盒帖子抽项目（渲染正文抠链接 / 抠项目名）

```bash
python assets/main.py --from-article "https://www.xiaoheihe.cn/bbs/post_share?link_id=xxxx"
```

小黑盒分享链接是 App 深链，通用爬虫只能拿到占位文本。工具用无头浏览器渲染正文后，会按三种情况处理：

1. **正文直接给了仓库链接** → 直接抽取。
2. **只给了项目名、没给地址** → 自动从标题/正文中提取项目名，调 GitHub Search API 反查仓库地址并收录。
3. **一个帖子介绍了多个项目** → 同时处理多个链接 / 项目名。

> ⚠️ GitHub 搜索 API 未认证时限 10 次/分钟；单篇小黑盒项目较多时可能触发限流，稍等再跑即可。只有项目名且不想走自动搜索时，可改用显式 `--from-name "项目名"`。

### 按项目名搜索收录（无确切地址时）

```bash
python assets/main.py --from-name "ToolKnit"
```

> 调 GitHub 搜索 API 取 stars 最高的匹配并收录；未认证约 10 次/分钟（设 `GITHUB_TOKEN` 升到 5000/h）。`source_kind` 记为 `name-search`。

### 从已有飞书 Bitable 迁移到本地

```bash
# 先预览（不写盘）
python migrate_feishu_to_local.py --dry-run
# 正式迁移：读取 FEISHU_BASE_TOKEN，把每行记录写成 开源项目/<owner>__<repo>.md
python migrate_feishu_to_local.py
```

迁移脚本逐页读取飞书多维表格，按字段映射还原逻辑字段，已存在的文件跳过（幂等）。

### 分步调用（子代理工作流）

```bash
# 1) 采集 README 到磁盘（主会话只看到摘要）
python -m assets.pipeline collect "https://github.com/owner/repo ..." collected_data.json
# 2) 派生子代理读 collected_data.json，产出 analysis_results.json
#    （analysis_results.json 为 dict：{"owner/repo": {summary, project_type, ...}}）
# 3) 主会话拿到结果后归档
python -m assets.pipeline upload collected_data.json analysis_results.json
```

## Storage Format

每个项目一个文件 `<owner>__<repo>.md`（`/` 转 `__`，非法文件名字符转 `_`）：

```markdown
---
owner_repo: owner/repo
url: "https://github.com/owner/repo"
platform: github
summary: 一句话简介
project_type: MCP
run_form: MCP-stdio
target_user: Agent调用
domain: 通用工具
tags:
  - AI
  - MCP
highlights: 核心亮点
doc_score: 7
func_score: 8
community_score: 4
total_score: 19
source_kind: direct
imported_at: 2026-09-02 22:00:00
status: 已入库
---

（可选正文：项目详情 / 使用笔记）
```

- `community_score` 由 Stars 换算（`stars_to_score`），`total_score = community_score + doc_score + func_score`。
- `source_kind`：`direct`（直接链接）/ `body`（文章正文）/ `description`（视频描述）/ `subtitle`（字幕）/ `xiaoheihe`（小黑盒渲染正文）/ `name-search`（按项目名搜索）/ `feishu-migration`（迁移所得）。
- 飞书回退模式的字段映射见 `feishu_fields.example.json`。

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PROJECT_LIBRARY_DIR` | No | 本地项目库目录（建议 Obsidian vault 根下 `开源项目/`）；不设则回退 `OBSIDIAN_VAULT_PATH/开源项目` |
| `PROJECT_STORAGE` | No | 存储后端：`local`（默认，写 Obsidian markdown）/ `feishu`（回退写飞书多维表格） |
| `FEISHU_BASE_TOKEN` | No | 仅 `PROJECT_STORAGE=feishu` 时生效：飞书 Base Token **或** Wiki/文档链接（自动辨别解析） |
| `FEISHU_TABLE_ID` | No | 飞书 Table ID（用 Wiki 链接时可省略，从 `?table=` 读取） |
| `OBSIDIAN_VAULT_PATH` | No | Obsidian vault 路径；`PROJECT_LIBRARY_DIR` 缺省时取其下 `开源项目/` |
| `GITHUB_TOKEN` | No | GitHub PAT，提升 API 限流（60 → 5000 req/h）；少量导入可不填 |
| `GITEE_TOKEN` | No | Gitee 私有 Token，提升 API 限流；公开仓库可不填 |
| `BATCH_LLM_API_KEY` / `OPENAI_API_KEY` | No | LLM API Key，配置后阶段三**自动**分析（推荐） |
| `BATCH_LLM_BASE_URL` / `OPENAI_BASE_URL` | No | OpenAI 兼容接口地址（默认 `https://api.openai.com/v1`，国内可用 DeepSeek/通义等） |
| `BATCH_LLM_MODEL` / `OPENAI_MODEL` | No | 模型名（默认 `gpt-4o-mini`） |
| `BATCH_LLM_ANALYSIS_FILE` | No | 指向一个分析 JSON 文件，直接读取（测试/离线用） |
| `BATCH_MAX_WORKERS` | No | 采集+分析并发线程数（默认 5，不超过待处理仓库数） |
| `FEISHU_FIELD_MAP` | No | JSON 字符串，覆盖飞书字段映射（仅 feishu 模式） |

### Scoring

项目评估有三个维度，加起来总分 30：

| 维度 | 分值 | 怎么算的 |
|------|------|---------|
| 社区评分 | 1-10 | 根据 GitHub Stars 数量换算，Stars 越多分越高 |
| 文档评分 | 1-10 | LLM 看 README 写得完不完善来打分 |
| 功能评分 | 1-10 | LLM 看项目功能完不完整来打分 |

**Stars 和社区评分对照：**

| Stars 数量 | 分数 |
|-----------|------|
| 0 - 10 | 1 |
| 11 - 100 | 2 |
| 101 - 500 | 3 |
| 501 - 1000 | 4 |
| 1001 - 5000 | 5 |
| 5001 - 10000 | 6 |
| 10001 - 30000 | 7 |
| 30001 - 100000 | 8 |
| 100001+ | 9-10 |

## Design

- **本地优先、飞书可选回退** — 默认写 Obsidian markdown；`PROJECT_STORAGE=feishu` 才写飞书。
- **内容驱动** — 直接链接 / 文章 / 视频三入口统一抽取，「坐在内容层之上」的 enrichment 能力。
- **视频描述优先于字幕** — 仓库地址通常在简介里，字幕常只提仓库名。
- **本地去重，不查远端** — 用本地文件记录已入库和待上传的项目。
- **一次 LLM 搞定** — 所有分析字段一次 Prompt 输出，不分多次调。
- **不 clone 代码** — 只用 HTTP 请求拿公开数据，不 git clone。
- **没配存储也能用** — 结果先存本地 `pending_results.json`，配好后再落库。

## Project Structure

```
project-import/
├── SKILL.md                    # AI Agent 执行指令（触发规则 + 流程控制）
├── README.md                   # 项目说明（本文）
├── pyproject.toml              # Python 项目配置
├── .env.example                # 环境变量模板（复制为 .env 填写自己的配置）
├── .gitignore
├── feishu_fields.example.json  # 飞书字段映射模板（仅 feishu 回退模式用）
├── assets/                     # Python 辅助脚本（核心逻辑）
│   ├── extractor.py            # 链接提取 + 标准化 + 本地去重（GitHub / Gitee）
│   ├── collector.py            # 数据采集（README + Stars，GitHub / Gitee）
│   ├── analyzer.py             # LLM Prompt 模板 + 分析字段校验纠偏
│   ├── llm_client.py           # LLM 分析（子代理/Api 回退，已移除交互模式）
│   ├── content_source.py       # 输入路由（direct / article / video：描述优先+字幕）
│   ├── project_finder.py       # 编排抽取 + 三层去重（候选带 source_kind）
│   ├── local_writer.py         # 本地 Obsidian markdown 写入（YAML frontmatter）
│   ├── feishu_writer.py        # 飞书多维表格写入（仅 PROJECT_STORAGE=feishu 回退）
│   ├── storage.py              # 本地待上传记录管理（pending_results.json）
│   ├── tracker.py              # imported.txt 已入库清单维护
│   ├── reporter.py             # 统计汇总与报告生成
│   ├── pipeline.py             # 子代理工作流（collect / prompts / upload 子命令）
│   └── main.py                 # 一体化编排入口（--from-article / --from-video / 直接文本）
├── migrate_feishu_to_local.py  # 一次性：从飞书 Bitable 迁到本地 Obsidian
└── tests/
    └── test_core.py            # 单元测试
```

> 注：`imported.txt`、`pending_results.json`、`feishu_fields.json`、`.env`、`debug.log`
> 均为本地运行产物，已被 `.gitignore` 忽略，不进版本库。

## How It Works

```
用户输入（直接仓库链接 / 文章 URL / 视频 URL）
    │
    ▼
输入路由（content_source.resolve）
    ├─ 直接文本含仓库链接 → direct
    ├─ 文章 URL → 抓正文，从 body 抠仓库链接
    ├─ 小黑盒链接 → 无头浏览器渲染正文
    │   ├─ 抠到仓库链接 → body
    │   └─ 无链接但有项目名 → GitHub 搜索 API 反查 → name-search
    ├─ 视频 URL → 描述（简介）优先，字幕兜底
    └─ 项目名（--from-name） → GitHub 搜索 API 命中后收录
    │
    ▼
阶段一：三层去重
    │  正则匹配 → 批次内去重 → 查 imported.txt → 查 pending_results.json
    ▼
阶段二：数据采集
    │  HTTP GET → README（raw.githubusercontent.com）
    │  HTTP GET → Stars（api.github.com）
    ▼
阶段三：LLM 分析（一次 Prompt）
    │  分类 + 打标 + 评分 → JSON 输出
    ▼
阶段四：本地归档 / 飞书回退
    ├─ PROJECT_STORAGE=local（默认） → 写 <owner>__<repo>.md + 写 imported.txt
    └─ PROJECT_STORAGE=feishu        → 上传飞书多维表格
    └─ 两者皆未配 → 追加 pending_results.json，提示配置
    ▼
阶段五：输出汇总报告
    统计概要 + 类型分布 + 失败明细
```

## License

MIT
