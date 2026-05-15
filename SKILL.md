---
name: blog-article-skill
description: "\"文章总结与输出技能：支持将博客链接或原文总结成结构化笔记，并输出到本地、Obsidian或飞书知识库\""
---

# blog-article-skill

## 1. 何时使用本 Skill

### 1.1 触发条件

以下场景应使用本 skill：

- 用户提供博客链接，需要总结并保存
- 用户粘贴原文文本，需要整理成结构化笔记
- 用户需要将文章同步到 Obsidian 知识库
- 用户需要将文章同步到飞书知识库

### 1.2 前置约束

1. 如需输出到飞书知识库，需先安装飞书CLI并完成配置
2. 如需输出到 Obsidian，需配置 OBSIDIAN_VAULT_PATH 环境变量
3. 未配置时默认输出到本地 notes/ 目录

## 2. 模块与命令导航

### 2.1 模块地图

| 大模块 | 处理什么问题 | 包含的小模块 |
|--------|-------------|-------------|
| 内容总结模块 | 将输入内容按固定Prompt规则总结 | prompt.py |
| 输出模块 | 将总结内容输出到目标位置 | base.py, feishu.py, local.py, obsidian.py, manager.py |

### 2.2 输出目标选择

| 输出目标 | 配置要求 | 说明 |
|----------|----------|------|
| 本地文件 | 无需配置 | 默认输出到 notes/ 目录 |
| Obsidian | 配置 OBSIDIAN_VAULT_PATH | 输出到 Obsidian 知识库 |
| 飞书知识库 | 安装飞书CLI + 配置 FEISHU_WIKI_SPACE | 输出到飞书知识库 |

## 3. 执行规则

### 3.1 标准执行流程

1. **获取内容**：使用 WebFetch 获取博客链接内容，或直接接收原文
2. **AI总结**：使用 `CONTENT_SUMMARY_PROMPT` 指导AI进行结构化总结
3. **格式化**：使用 `format_note_with_prompt()` 添加标签、作者、链接等元数据
4. **自动保存**：调用 `save_summarized_article()` 保存到所有可用目标

### 3.2 核心函数

**主要保存函数**：`assets.save_summarized_article()`

**函数用途**：接收已总结好的内容，自动格式化并保存到本地、Obsidian、飞书等所有配置好的目标。

**使用方式**：
```python
from assets import save_summarized_article

# 保存总结好的文章
save_summarized_article(
    summarized_content="这里是AI已经总结好的内容...",
    original_url="https://example.com/article",  # 可选
    author="作者名",  # 可选
    tags=["AI", "技术"]  # 可选
)
```

### 3.3 工作模式

| 阶段 | 执行方式 | 说明 |
|------|---------|------|
| 内容获取 | WebFetch / 直接输入 | 支持链接抓取或直接粘贴原文 |
| AI总结 | 对话交互 | 使用 CONTENT_SUMMARY_PROMPT 进行结构化总结 |
| 保存输出 | 自动执行 | 调用 save_summarized_article() 自动保存 |

### 3.4 提示词模板

**内容总结模板**：[assets/prompt.py](file:///d:/Code/Skills/blog-article-skill/.trae/skills/blog-article-skill/assets/prompt.py) 中的 `CONTENT_SUMMARY_PROMPT`

包含：
- 开篇固定范式（标签、作者、链接）
- 文章宏观架构（概要、核心定义、分层拆解等）
- 标题层级规范
- 排版格式规范
- 内容提炼固定逻辑
- Prompt合集规范

### 3.5 配置说明

配置文件 `.env`：

```env
# Obsidian 知识库路径（可选）
OBSIDIAN_VAULT_PATH=D:\你的Obsidian库路径

# 飞书知识库空间 ID（可选）
FEISHU_WIKI_SPACE=你的知识库空间ID

# 飞书知识库父节点 Token（可选）
FEISHU_WIKI_PARENT_NODE=父节点Token
```

## 4. 参考文档

- [references/config.md](references/config.md) — 飞书/Obsidian 配置说明