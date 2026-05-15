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
| 输出模块 | 将总结内容输出到目标位置 | base.py, local.py, obsidian.py, feishu.py, manager.py |

### 2.2 输出目标选择

| 输出目标 | 配置要求 | 说明 |
|----------|----------|------|
| 本地文件 | 无需配置 | 默认输出到 notes/ 目录 |
| Obsidian | 配置 OBSIDIAN_VAULT_PATH | 输出到 Obsidian 知识库 |
| 飞书知识库 | 安装飞书CLI + 配置 FEISHU_WIKI_SPACE | 输出到飞书知识库 |

## 3. 核心代码与API

### 3.1 目录结构

```
assets/
├── __init__.py        # 模块导出
├── prompt.py          # 内容总结Prompt模板
├── base.py            # 输出模块基类
├── local.py           # 本地文件输出
├── obsidian.py        # Obsidian输出
├── feishu.py          # 飞书知识库输出
├── manager.py         # 输出管理器
└── main.py            # 主入口与完整流程
```

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

**内容总结**：
```python
from assets.prompt import CONTENT_SUMMARY_PROMPT, format_note_with_prompt

# 使用Prompt指导AI总结
summarized_content = ai.summarize(article_content, prompt=CONTENT_SUMMARY_PROMPT)

# 格式化笔记
formatted_note = format_note_with_prompt(
    content=summarized_content,
    author="作者名",
    url="https://example.com/article",
    tags=["AI", "提示词"]
)
```

**输出管理**：
```python
from assets.manager import OutputManager

# 创建输出管理器
manager = OutputManager()

# 保存到所有可用位置
manager.save_all(formatted_note, "文章标题.md")

# 或保存到指定位置
manager.save_to(formatted_note, "文章标题.md", "local")   # 本地
manager.save_to(formatted_note, "文章标题.md", "obsidian") # Obsidian
manager.save_to(formatted_note, "文章标题.md", "feishu")   # 飞书
```

### 3.3 API 参考

#### OutputManager

| 方法 | 用途 | 参数 |
|------|------|------|
| `get_available_outputs()` | 获取所有可用输出模块 | 无 |
| `save_all(content, filename)` | 保存到所有可用位置 | content: 内容, filename: 文件名 |
| `save_to(content, filename, target)` | 保存到指定位置 | target: "local"/"obsidian"/"feishu" |

#### Prompt 模块

| 方法 | 用途 |
|------|------|
| `get_content_summary_prompt()` | 获取内容总结Prompt |
| `format_note_with_prompt(content, author, url, tags)` | 格式化笔记内容 |

### 3.4 完整执行入口

#### 方式一：Python 直接调用（自动保存）
```python
from assets import summarize_and_save, skill_main

# 处理博客链接
article_content, formatted_note, filename = summarize_and_save(
    "https://example.com/article",
    author="作者名",
    tags=["人工智能", "技术"]
)

# 处理文章原文
article_content, formatted_note, filename = summarize_and_save(
    "文章内容...",
    tags=["总结", "笔记"]
)

# 使用 skill_main（技能系统调用入口）
result = skill_main({
    'content': 'https://example.com/article',
    'author': '作者名',
    'tags': ['AI', '技术']
})
print(result['message'])
```

#### 方式二：命令行运行
```bash
python -m assets.main --content "文章内容" --url "https://example.com" --author "作者" --tags "AI,技术"
# 或
python -m assets.main --file article.txt --tags "总结"
```

#### 方式三：自动保存流程说明
当调用 `summarize_and_save()` 或 `skill_main()` 时，系统会自动执行以下流程：
1. **获取内容**：自动抓取网页内容或直接使用提供的文本
2. **AI总结**：使用 CONTENT_SUMMARY_PROMPT 进行结构化总结
3. **自动保存**：保存到所有配置的目标位置（本地/Obsidian/飞书）

### 3.5 执行规则

| 阶段 | 执行方式 | 说明 |
|------|---------|------|
| 内容获取 | WebFetch / 直接输入 | 支持链接抓取或直接粘贴原文 |
| AI总结 | 对话交互 | 使用 CONTENT_SUMMARY_PROMPT 进行结构化总结 |
| 保存输出 | 自动执行 | 调用 save_summarized_article() 自动保存 |

### 3.6 提示词模板

**内容总结模板**：`assets/prompt.py` 中的 `CONTENT_SUMMARY_PROMPT`

包含 13 个章节的完整规则体系：
- 开篇固定范式（标签、作者、链接、主标题）
- 文章宏观架构（概要、核心定义、分层拆解等）
- 标题层级规范（一/二/三级标题及编号规则）
- 排版/格式/符号规范
- 内容提炼固定逻辑
- 列表与表述习惯
- 语言风格固定特征
- 笔记必备9大固定模块
- 基础优化补充规则
- 重点定制强化规则（案例/Prompt/文末合集）
- 新增补齐漏洞规则（11条硬性补全）
- 文末Prompt合集固定模板
- 最终执行结束语

## 4. 配置说明

配置文件 `.env`：

```env
# Obsidian 知识库路径（可选）
OBSIDIAN_VAULT_PATH=D:\你的Obsidian库路径

# 飞书知识库空间 ID（可选）
FEISHU_WIKI_SPACE=你的知识库空间ID

# 飞书知识库父节点 Token（可选）
FEISHU_WIKI_PARENT_NODE=父节点Token
```

### Obsidian 配置

**前提条件**：已安装 Obsidian 客户端

**配置步骤**：
1. 在 `.env` 文件中添加：
   ```env
   OBSIDIAN_VAULT_PATH=D:\你的Obsidian库路径
   ```

### 飞书配置

**前提条件**：
1. 安装飞书CLI：`npx @larksuite/cli@latest install`
2. 完成飞书应用配置：`lark-cli config init`

**配置步骤**：
1. 在 `.env` 文件中添加：
   ```env
   FEISHU_WIKI_SPACE=你的知识库空间ID
   ```

## 5. 参考文档

详细配置说明见 [references/config.md](references/config.md) — 飞书/Obsidian 配置说明。

### 输出规则

| 配置情况 | 输出目标 |
|----------|----------|
| 无任何配置 | 默认保存到 `notes/` 目录 |
| 仅配置 Obsidian | 输出到 Obsidian 知识库 |
| 仅配置飞书 | 输出到飞书知识库 |
| 两者都配置 | 同时输出到 Obsidian + 飞书 |

### 验证配置

```python
from assets import OutputManager

manager = OutputManager()
available = manager.get_available_outputs()
print(f"可用输出模块: {[o.name for o in available]}")
```
