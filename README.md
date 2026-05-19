# Blog Article Skill - 文章结构化总结与归档工具

一款通用的博客文章结构化总结与多渠道归档工具。支持将任意博客链接或文章原文，通过 AI 自动生成结构化笔记，并保存到本地、Obsidian 知识库或飞书知识库。

## 功能特性

- **多渠道内容抓取**：支持微信公众号、百度、掘金、CSDN、知乎等主流平台的文章链接自动抓取
- **AI 结构化总结**：内置 CONTENT_SUMMARY_PROMPT 模板，将文章提炼为 9 大固定模块的结构化笔记
- **多 Provider 支持**：可接入 OpenAI、Anthropic Claude、Google Gemini、本地 Ollama 等多种 AI 模型
- **多渠道输出**：支持本地文件、Obsidian 知识库、飞书知识库三种输出方式
- **降级处理**：无外部 AI Provider 时自动降级，由当前对话模型接手总结，不中断流程
- **文件安全**：自动处理文件名冲突、非法字符过滤、UTF-8 编码写入

## 快速开始

### 1. 安装依赖

```bash
cd blog-article-skill
pip install -e .
```

可选依赖（按需安装）：
```bash
pip install -e ".[openai]"      # OpenAI 支持
pip install -e ".[anthropic]"   # Anthropic Claude 支持
pip install -e ".[google]"      # Google Gemini 支持
pip install -e ".[async]"       # 异步抓取支持（aiohttp）
```

### 2. 配置 AI Provider

复制环境变量模板并配置：

```bash
cp .env.example .env
```

根据需要配置至少一种 AI Provider（未配置时将使用当前对话模型进行降级总结）：

```env
# OpenAI（推荐）
AI_PROVIDER=openai
OPENAI_API_KEY=sk-xxxx

# 或 Anthropic Claude
# AI_PROVIDER=anthropic
# ANTHROPIC_API_KEY=sk-ant-xxxx

# 或 Google Gemini
# AI_PROVIDER=google
# GOOGLE_API_KEY=xxxx

# 或本地 Ollama
# AI_PROVIDER=local
# LOCAL_API_BASE=http://localhost:11434/v1
```

### 3. 使用方式

#### Python API 调用

```python
from assets import summarize_and_save, skill_main

# 自动抓取 + AI 总结 + 自动保存
summarize_and_save("https://example.com/article", author="作者名", tags=["AI", "技术"])

# 或使用 skill_main 入口
result = skill_main({
    "content": "https://example.com/article",
    "author": "作者名",
    "tags": ["AI", "技术"]
})
```

#### 命令行使用

```bash
# 处理文章链接
python assets/run.py "https://example.com/article"

# 处理纯文本内容
python assets/run.py --content "文章原文..."

# 指定作者和标签
python assets/run.py --url "https://example.com" --author "作者" --tags "AI,技术"
```

## 输出目标配置

### 本地文件（默认，无需配置）

无任何配置时自动保存到项目 `notes/` 目录。

### Obsidian 知识库

```env
OBSIDIAN_VAULT_PATH=D:\你的Obsidian库路径
```

### 飞书知识库

```env
FEISHU_WIKI_SPACE=你的知识库空间ID
FEISHU_WIKI_PARENT_NODE=父节点Token  # 可选
```

需要先安装飞书 CLI：
```bash
npx @larksuite/cli@latest install
lark-cli config init
```

## 项目结构

```
blog-article-skill/
├── assets/                  # 核心模块
│   ├── __init__.py          # 模块导出
│   ├── ai_provider.py       # AI Provider 架构（OpenAI/Claude/Gemini/Local）
│   ├── prompt.py            # 结构化总结 Prompt 模板
│   ├── base.py              # 输出模块抽象基类
│   ├── local.py             # 本地文件输出
│   ├── obsidian.py          # Obsidian 输出
│   ├── feishu.py            # 飞书知识库输出
│   ├── manager.py           # 输出管理器
│   ├── main.py              # 主入口与完整流程
│   └── run.py               # 命令行入口
├── references/
│   └── config.md            # 配置详细说明
├── .env.example             # 环境变量模板
├── pyproject.toml           # 项目配置
└── SKILL.md                 # 完整技能文档
```

## AI Provider 自动检测逻辑

1. 优先使用 `AI_PROVIDER` 环境变量指定的外部 Provider
2. 未指定时按优先级自动检测：`openai` > `anthropic` > `google` > `local`
3. 使用第一个可用的 Provider
4. 无任何外部 Provider 时触发降级流程，由当前对话模型接手总结

## 降级处理流程

当未配置任何外部 AI Provider 时：

1. 正常抓取网页文章内容并暂存到 `notes/` 目录
2. 返回 `need_continue_summary=True` 及 `CONTENT_SUMMARY_PROMPT`
3. 当前对话模型使用该 Prompt 进行结构化总结
4. 总结完成后调用 `save_summary_only()` 或 `save_summarized_article()` 保存

## 许可证

MIT