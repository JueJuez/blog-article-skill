---
name: project-import
version: 2.0.0
description: "把 GitHub / Gitee 开源项目（直接给链接，或从文章/视频内容里抠出来）写入本地 Obsidian 项目库（markdown + YAML frontmatter）归档：采集 README+Stars、LLM 分类打分、入库。只归档项目，不总结原文。"
metadata:
  requires:
    bins: ["lark-cli"]
---

# Project Import · 开源项目抽取归档

把「开源项目」调研分类后写入**本地 Obsidian 项目库（markdown + YAML frontmatter）**归档。选本地而非飞书，是因为别的项目的 agent 能零鉴权直接 grep / 解析 / embedding 这些文件，便于「哪些项目能用上」的检索；飞书作为可选回退。

> **两种输入都支持**：
> 1. **直接给仓库链接** —— 旧行为。给一条或多条 GitHub / Gitee 仓库地址，直接采集分析入库。
> 2. **从文章 / 视频里抽项目** —— 新行为（enrichment）。给文章 URL 或视频 URL，工具自动从**正文 / 视频描述（优先）/ 字幕**里抠出提到的仓库并归档。
>
> ⚠️ **本工具只做「抽项目 → 入本地项目库」，绝不总结原文**。给文章/视频链接时，它不会生成笔记、不会写知识库 wiki、不碰 Obsidian 笔记——那是项目能力 1（文章/视频总结）的事。两者是独立动作，不要混为一谈。

---

## 触发规则

### 激活条件

用户表达明确的「项目归档 / 评估」意图时执行：

- 直接给 GitHub / Gitee 链接 + 「评估 / 归档 / 入库 / 打分 / 收录 / 批量导入」
- 给**文章或视频链接** + 「提取这篇文章/视频里提到的开源项目」「把里面提到的仓库收进库」
- 给一段含仓库链接的自由文本（如「我找到一个好用的 PPT MCP https://github.com/A/B」）

### 不激活条件

- 用户只是问「这个项目是干什么的 / 怎么样」（纯答疑，不入库）
- 用户要求**总结**某篇文章/视频内容（走能力 1，不要顺手触发本工具）
- 用户只给链接但没说要评估/入库

> 判断不准时，默认不激活，先问用户是否需要执行归档流程。

---

## 安全约束

- 任何时候不要输出 `FEISHU_BASE_TOKEN` 和 `FEISHU_TABLE_ID` 的值
- 未确认用户意图前不要执行全流程
- 不要克隆仓库，只用 HTTP 请求获取公开数据

## 配置与环境变量

本工具无任何私有数据硬编码。`.env.example` 列出全部可配置项（占位符，**不含真实 token**），复制到 `.env` 后填入你自己的值：

```bash
cp .env.example .env   # 然后编辑 .env 填入你自己的 FEISHU_BASE_TOKEN / LLM 配置等
```

关键变量：

| 变量 | 说明 |
|------|------|
| `PROJECT_LIBRARY_DIR` | **本地项目库目录**（每个项目一个 `.md` + YAML frontmatter）。建议放在 Obsidian vault 根，如 `D:\你的库\开源项目`。不设置则回退到 `OBSIDIAN_VAULT_PATH/开源项目`。**这是默认存储。** |
| `OBSIDIAN_VAULT_PATH` | 你的 Obsidian 库路径（本项目笔记也用它）；未设 `PROJECT_LIBRARY_DIR` 时作为库目录基类。 |
| `PROJECT_STORAGE` | 存储后端选择：`local`（默认，写 Obsidian markdown）/ `feishu`（回退写飞书多维表格，需下方 `FEISHU_BASE_TOKEN`）。 |
| `FEISHU_BASE_TOKEN` | **（可选）** 仅在 `PROJECT_STORAGE=feishu` 时生效：飞书多维表格 Base 的 token（裸 base token 或 Wiki/文档链接，自动辨别解析）。本项目飞书笔记用的是 `FEISHU_WIKI_SPACE`，与本变量不同、不冲突。 |
| `FEISHU_TABLE_ID` | （可选）表 id（用 wiki 链接且带 `?table=` 时可省略） |
| `FEISHU_FIELD_MAP` | 可选，覆盖字段映射；默认读 `feishu_fields.json`（本地私有，gitignore），仓库自带 `feishu_fields.example.json` 为占位模板 |
| `BATCH_LLM_API_KEY` / `BATCH_LLM_BASE_URL` / `BATCH_LLM_MODEL` | OpenAI 兼容 LLM 配置（DeepSeek / 通义等） |
| `BATCH_LLM_ANALYSIS_FILE` | 可选，离线分析 JSON 文件，跳过真实 API |
| `GITHUB_TOKEN` / `GITEE_TOKEN` | 可选，提升 API 限流 60→5000 次/小时 |
| `BATCH_MAX_WORKERS` | 并发采集分析的线程数，默认 5，上限为待处理仓库数 |
| `QUALITY_GATE_ENABLED` | 收录质量门禁开关，默认 `0`（关闭，所有项目直接入库）；置 `1` 开启，低质项目转 `pending_review.json` 待复核 |
| `QUALITY_GATE_MIN_STARS` | 低星阈值，默认 `100`；stars 低于此值判低质，不自动入库 |
| `QUALITY_GATE_MIN_DOC` / `QUALITY_GATE_MIN_FUNC` | doc/func 评分阈值，默认 `5`；两者**同时**低于阈值也判低质 |
| `NAME_SEARCH_DELAY` | 按项目名批量搜索时，每次搜索的间隔秒数，默认 `1.2`，用于绕过 GitHub 搜索 API 限流 |

> 注意：`.gitignore` 已忽略 `.env`、`pending_results.json`、`debug.log`、`imported.txt`、`__pycache__`，私有数据不会误提交。

## 自动化规则（必须遵守）

以下规则适用于每次执行，无需征求用户同意：

### 规则一：启动时自动检测存储目标（本地 / 飞书）

每次执行本 skill 时，**最先做**（在任何阶段之前）的一件事就是自动检测存储目标：

1. 运行：`from assets.local_writer import is_local_configured; print(is_local_configured())`
2. 默认（`PROJECT_STORAGE` 未设或 = `local`）→ 检测 `PROJECT_LIBRARY_DIR` / `OBSIDIAN_VAULT_PATH`；已配置则告知「本地项目库已就绪，将写入 Obsidian markdown」，**不要问用户是否已配置**
3. 若 `PROJECT_STORAGE=feishu` → 检测 `FEISHU_BASE_TOKEN`；已配置告知「飞书已配置，将写入 Bitable」
4. 两者都未配置 → 告知「未检测到本地库/飞书配置，结果将本地暂存到 pending_results.json」并输出配置指引

### 规则二：`pending_results.json` 上传后自动清理，无需用户确认

`pending_results.json` 记录待上传的评估结果，每次执行都会变化。清理逻辑已在 `pop_all()` 中内置（取出记录后删除文件），因此流程中**不会有任何「是否删除」的提问出现**。其他文件（`imported.txt`、`__pycache__` 等）是持久化缓存，保留不变。

---

## 入口与执行流程

> ⚡ 每次执行先跑「规则一：检测存储目标（本地/飞书）」，完成后再开始阶段一。

### 入口命令

```bash
# 直接给仓库链接（旧行为）
python tools/project_import/assets/main.py "https://github.com/owner/repo ..."

# 从文章里抽项目（不总结原文）
python tools/project_import/assets/main.py --from-article "<文章URL>"

# 从视频里抽项目（不总结原文，描述优先）
python tools/project_import/assets/main.py --from-video "<视频URL>"

# 按项目名搜索收录（只知道项目名、没有确切地址时）
python tools/project_import/assets/main.py --from-name "ToolKnit"

# 可选：子代理产出的分析文件 / 飞书目标覆盖
#   [--analysis-file path] [--feishu <wiki链接或token>] [--table <id>]
```

> 💡 **推荐路径**：
> - 已配 `BATCH_LLM_*` → 一行命令全跑完（外部 LLM，HEADLESS）。
> - 未配 `BATCH_LLM_*`（默认） → 走**子代理分析**工作流，避免把 README 带进主会话（见阶段三）。

### 输入路由（content_source + project_finder）

`main.py` 收到输入后：

- **`--from-article` / `--from-video`** → 调 `project_finder.find(input_text)`：
  1. `content_source.resolve()` 把输入路由成若干 `SourceText`：
     - 文本本身含仓库链接 → 按 `direct` 扫描（**先于**「以 http 开头」判断，否则含多链接的句子会被误当文章 URL 真实联网）。
     - 视频 URL → **先取描述/简介**（`SourceText("description")`），再取字幕/转录（`SourceText("subtitle")`）。
     - **小黑盒链接**（`xiaoheihe.cn`）→ 分享链接是 App 深链，通用爬虫只回占位；用 **无头浏览器（Node Playwright + 系统 Chrome）** 渲染正文，再从渲染后的 `SourceText("body")` 抠仓库链接。支持三种情况：①正文直接含 URL → 直接抽取；②只给项目名、没给地址 → 自动从标题/正文中提取项目名，调 GitHub Search API 反查仓库地址并收录；③一篇帖子含多个项目 → 同时处理多个链接/项目名。GitHub 搜索未认证约 10 次/分钟，项目较多可能触发限流。
     - 文章 URL → 取正文（`SourceText("body")`）。
  2. 每个 source 跑 `extract_repo_urls` → 按 owner/repo 批次内去重 → `filter_imported` + `filter_pending` 三层去重。
  3. 返回带 `source_kind`（description / subtitle / body / direct）标记的候选列表，供用户看到「项目是从哪抠出来的」。
- **直接文本/链接** → 走原 `phase1_extract`（阶段一）。

> **视频场景铁律（用户 2026-09-02 确认）**：优先扫**视频描述/简介**找仓库链接；字幕只作补充。原因：字幕常只提仓库名、地址其实在简介里。阶段一只解析**显式 URL**，字幕里「只提仓库名没给地址」的情况暂不解析。
>
> ⚠️ **视频来源范围封口（用户 2026-09-03 确认）**：视频只处理**简介 + 字幕**两种来源；**明确不做**①评论区抓取（置顶评论等）②画面 OCR / 截图识别（项目只出现在视频画面里）。对后者，走下方「按项目名搜索收录」——由你告诉项目名，工具去 GitHub 搜索并收录。

### 按项目名搜索收录（--from-name）

当你**只知道项目名、没有确切仓库地址**时（例如视频画面里出现、字幕/简介都只提名字），用这个入口：

```bash
python tools/project_import/assets/main.py --from-name "ToolKnit"
```

- 调 GitHub 公开搜索 API（`q=<name> in:name&sort=stars`），取 stars 最高的匹配。
- 命中后打印 `owner/repo ⭐stars url`，自动进入正常「采集 → 分析 → 入库」流程。
- 未命中（或限流 403）会提示，不入库。
- 未认证搜索约 10 次/分钟；大量使用建议设 `GITHUB_TOKEN`（限流升到 5000/h，但仍走搜索接口）。
- `source_kind` 记为 `name-search`，便于回溯「这个项目是怎么进库的」。

阶段一产出候选后，后续阶段二~五与旧行为完全一致（采集 → 分析 → 入库 → 报告）。

---

## 阶段二：数据采集

```python
from assets.extractor import parse_repo
from assets.collector import collect_project_data, stars_to_score

platform, owner, repo = parse_repo(url)              # platform ∈ {'github', 'gitee'}
readme, stars, error = collect_project_data(platform, owner, repo)
```

- README 源（多源兜底，逐源重试 3 次，间隔 1s）：
  - GitHub：`raw.githubusercontent.com/{owner}/{repo}/main` → `master` → API(base64)
  - Gitee：`gitee.com/{owner}/{repo}/raw/master` → `main` → API(base64)
- Stars：`api.github.com/repos/{owner}/{repo}` 或 `gitee.com/api/v5/repos/{owner}/{repo}` → `stargazers_count`
- 设置 `GITHUB_TOKEN` / `GITEE_TOKEN` 把匿名 60 次/小时限流提升到 5000 次/小时
- README 缺失则整条失败；Stars 失败仅记 0，不阻断流程

> 阶段二与阶段三在 `main.py` 中由 `ThreadPoolExecutor` **并发**执行，并发数由 `BATCH_MAX_WORKERS`（默认 5）控制。阶段四入库保持串行，避免飞书写入竞态。采集失败的记录失败原因，不中断流程。

---

## 阶段三：LLM 分析（默认由子代理完成，避免污染主会话）

**核心原则**：不要让「执行模型主会话」直接读 README 做分析——README 往往很长，塞进主会话会污染上下文。两条干净路径：

- **默认（未配 `BATCH_LLM_*`）：子代理分析**。派生一个**子代理**（独立上下文）读 README 并产出结构化分析 JSON，主会话只收到紧凑结果。
- **可选（配了 `BATCH_LLM_*`）：外部 LLM**。直接调用指定 OpenAI 兼容接口，HEADLESS，同样不进主会话。

#### 默认子代理工作流（推荐）

1. 采集 README 到磁盘：
   ```bash
   python -m assets.pipeline collect "含仓库链接的文本" collected_data.json
   python -m assets.pipeline prompts collected_data.json analysis_prompts.txt
   ```
2. 派生子代理，把下面这段发给它（让它读 `collected_data.json` 或 `analysis_prompts.txt`）：
   > 读取 `collected_data.json`（每条含 owner/repo、stars、readme）。对**每一个**项目，基于其 README 完成评估，输出**一个 JSON 对象**，key 为 `"owner/repo"`，value 为：
   > `{ "summary": 一句话简介(20字内), "project_type": "MCP"|"Skill"|"Agent工具"|"项目", "run_form": "MCP-stdio"|"MCP-SSE"|"Skill"|"不适用", "target_user": "Agent调用"|"本地运行"|"两者皆可", "domain": "功能领域单选", "tags": [能力标签...], "highlights": 核心亮点, "doc_score": 1-10, "func_score": 1-10 }`
   > 只输出这个 JSON，不要输出 README 原文。
   子代理把结果写回 `analysis_results.json`。
3. 主会话拿到 `analysis_results.json` 后入库：
   ```bash
   python -m assets.pipeline upload collected_data.json analysis_results.json
   ```

#### 配了 `BATCH_LLM_*` 时的一键路径

```bash
python tools/project_import/assets/main.py "含仓库链接的文本"
```

`llm_analyze` 分析来源优先级：
1. `--analysis-file` / `BATCH_LLM_ANALYSIS_FILE` → 直接读分析 JSON
2. `BATCH_LLM_API_KEY`（或 `OPENAI_API_KEY`）+ `BATCH_LLM_BASE_URL` + `BATCH_LLM_MODEL` → 调外部接口
3. 都没配 → 返回 `None` 并报提示（已移除「交互粘贴」模式）

LLM 返回的枚举字段会经 `analyzer.coerce_choice` 校验与纠偏：精确匹配 → 大小写不敏感 → 子串匹配 → 回退默认值，避免脏值写入飞书。

分析 JSON 字段：
- `summary`：一句话简介（20字内）
- `project_type`：MCP / Skill / Agent工具 / 项目
- `run_form`：MCP-stdio / MCP-SSE / Skill / 不适用
- `target_user`：Agent调用 / 本地运行 / 两者皆可
- `domain`：功能领域
- `tags`：能力标签数组
- `highlights`：核心亮点
- `doc_score`：文档评分（1-10）
- `func_score`：功能评分（1-10）

社区评分由 `stars_to_score(stars)` 本地计算，不占用 LLM。

---

## 阶段四：本地入库（默认）/ 飞书回退 / 本地暂存

默认存储是**本地 Obsidian markdown**：每个项目一个 `.md` 文件，所有结构化字段写入 YAML frontmatter（便于其它 agent grep / 解析）。`PROJECT_STORAGE=feishu` 时回退到飞书多维表格。

#### 默认（local）→ 写入本地项目库

- 目录解析：`PROJECT_LIBRARY_DIR` 优先，否则 `OBSIDIAN_VAULT_PATH/开源项目`。
- 文件名：`owner/repo` → `owner__repo.md`（`/` 转 `__`，避免与含 `-` 的 repo 名撞车）。
- 写入逻辑：
  1. `build_frontmatter(...)` 组装 frontmatter（含 `owner_repo / url / platform / summary / project_type / run_form / target_user / domain / tags / highlights / doc_score / func_score / community_score / total_score / source_kind / imported_at / status`）。
  2. 文件已存在则跳过（幂等，不覆盖）。
  3. 写入成功后把 `owner/repo` 追加到 `imported.txt`（去重账本，供后续阶段一前置过滤）。
  4. **收录质量门禁**：在写入前检查（**默认关闭，需设 `QUALITY_GATE_ENABLED=1` 才生效**）；开启后，低星（< `QUALITY_GATE_MIN_STARS`）或 doc/func 双低的项目**不写入**，改为记入 `pending_review.json` 待复核队列（报告项中标记为 `reviewed`）。`pending_review.json` 与 `imported.txt` 同理是去重账本——已入队项目不会重复入队。门禁不回滚已入库项目。

#### 回退（feishu）→ 上传飞书多维表格

（`PROJECT_STORAGE=feishu` 且 `FEISHU_BASE_TOKEN` 已配置时）

1. 若 `has_items()`，用 `pop_all()` 取出本地暂存记录，逐条写入飞书
2. 写入成功后把 `_owner_repo` 标记写入 `imported.txt`
3. 再上传本次分析的新结果
4. 本次结果写入成功后也追加到 `imported.txt`

#### 两者都未配置 → 本地暂存

1. 用 `append_items()` 把本次结果追加到 `pending_results.json`
2. 输出配置提示，告知设置 `PROJECT_LIBRARY_DIR`（或 `PROJECT_STORAGE=feishu` + `FEISHU_BASE_TOKEN`）后会自动落库

---

## 阶段五：输出汇总报告 + 自动清理

```python
from assets.reporter import ReportItem, build_report

items = [
    ReportItem(url, owner_repo, "success", project_type=result.project_type),
    ReportItem(url, owner_repo, "failed", error_reason="原因"),
    ReportItem(url, owner_repo, "skipped", error_reason="已入库/待上传"),
]
report = build_report(items)
print(report.generate())
```

本地暂存的项目状态标记为 `"success"` + `not_uploaded=True`。报告输出后无需任何手动清理（`pending_results.json` 的上传删除已由 `pop_all()` 自动完成）。

---

## 错误处理

- 单条采集或写入失败不中断流程，继续处理下一条
- 飞书写入失败最多重试 3 次，间隔 1s
- 失败的记录在报告中列出原因
- lark-cli 不可用时 `write_record_with_retry` 返回 False，不崩溃
