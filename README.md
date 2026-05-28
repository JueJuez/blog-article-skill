# Blog Article Skill

一款通用的博客文章结构化总结与多渠道归档工具。支持将任意博客链接或文章原文，通过 AI 自动生成结构化笔记，并保存到本地、Obsidian 或飞书知识库。

## 功能特性

- **多渠道抓取**：支持微信公众号、百度、掘金、CSDN、知乎等主流平台的文章自动抓取
- **AI 结构化总结**：内置 `CONTENT_SUMMARY_PROMPT` 模板，提炼为固定模块的结构化笔记
- **多 Provider 支持**：OpenAI / Anthropic Claude / Google Gemini / 本地 Ollama，也可用当前对话模型降级处理
- **多渠道输出**：本地文件 / Obsidian / 飞书知识库，自动保存到所有已配置的目标
- **文件安全**：自动处理文件名冲突、非法字符过滤、UTF-8 编码写入

## 快速开始

### 安装

```bash
pip install -e .
```

可选依赖（按需安装）：
```bash
pip install -e ".[openai]"      # OpenAI
pip install -e ".[anthropic]"   # Anthropic Claude
pip install -e ".[google]"      # Google Gemini
pip install -e ".[async]"       # 异步抓取（aiohttp）
```

### 配置

复制模板并编辑：

```bash
cp .env.example .env
```

配置至少一种 AI Provider（不配也行，会自动降级由当前对话模型总结）：

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

# 或 本地 Ollama
# AI_PROVIDER=local
# LOCAL_API_BASE=http://localhost:11434/v1
```

### 使用示例

```python
from assets import summarize_and_save, skill_main

# 全自动：抓取 → AI 总结 → 保存
summarize_and_save("https://example.com/article", author="作者名", tags=["AI", "技术"])

# 或通过 skill_main 入口
result = skill_main({
    "content": "https://example.com/article",
    "author": "作者名",
    "tags": ["AI", "技术"]
})
```

命令行：

```bash
# 处理链接
python assets/run.py "https://example.com/article"

# 处理原文
python assets/run.py --content "文章原文..."

# 指定作者和标签
python assets/run.py --url "https://example.com" --author "作者" --tags "AI,技术"

# 直接保存已总结好的内容（跳过 AI 总结）
python assets/run.py --summarized "总结内容..." --url "原文链接" --author "作者" --tags "AI,技术"
```

## API 参考

### 核心函数

| 函数 | 说明 |
|------|------|
| `fetch_web_content(url)` | 抓取网页，返回 `(title, content)` 或 `None` |
| `summarize_and_save(url, author, tags)` | 全自动：抓取→总结→保存到所有目标 |
| `skill_main(params_dict)` | 技能系统统一入口，处理链接/原文/降级逻辑 |
| `save_summarized_article(content, url, author, tags)` | 保存已总结好的内容到所有目标 |
| `save_summary_only(input_data)` | 降级模式下外层总结完后的保存入口 |
| `summarize_content(content, author, url, tags, original_title)` | 调用 AI 对内容做结构化总结 |

**`save_summarized_article` 参数说明**：

```python
save_summarized_article(
    summarized_content="AI 总结好的内容...",
    original_url="https://example.com/article",  # 可选
    author="作者名",                               # 可选
    tags=["AI", "技术"]                            # 可选
)
```

### AI Provider 模块

检测与获取 Provider：

| 函数 | 说明 |
|------|------|
| `get_ai_provider(name=None)` | 获取指定 Provider（含 Trae），为 None 时自动检测 |
| `get_external_ai_provider()` | 仅获取外部 Provider（不含 Trae），用于降级判断 |
| `has_external_provider()` | 检查是否有可用的外部 Provider |
| `list_available_providers()` | 列出所有可用 Provider（含 Trae） |
| `list_external_providers()` | 仅列出外部可用的 Provider |
| `call_ai_summarize(prompt, content)` | 调用任意 AI Provider 总结（含 Trae） |
| `call_external_ai_summarize(prompt, content)` | 仅调用外部 Provider，无配置返回 None |

支持的 Provider 类型：

| Provider | 说明 | 配置要求 |
|----------|------|----------|
| `openai` | OpenAI API | `OPENAI_API_KEY` |
| `anthropic` | Anthropic Claude | `ANTHROPIC_API_KEY` |
| `google` | Google Gemini | `GOOGLE_API_KEY` |
| `local` | 本地 Ollama | `LOCAL_API_BASE` |
| `trae` | Trae SDK | 需安装 trae Python 包，显式设置 `AI_PROVIDER=trae` |
| `mock` | 模拟 Provider | 仅测试用 |

自动检测优先级：`openai` > `anthropic` > `google` > `local`（Trae 不参与自动检测）

### OutputManager

```python
from assets.manager import OutputManager

manager = OutputManager()

# 保存到所有可用位置
manager.save_all(content, "文章标题.md")

# 或指定目标
manager.save_to(content, "文章标题.md", "local")    # 本地
manager.save_to(content, "文章标题.md", "obsidian")  # Obsidian
manager.save_to(content, "文章标题.md", "feishu")    # 飞书
```

### Prompt 模块

```python
from assets.prompt import CONTENT_SUMMARY_PROMPT, format_note_with_prompt

# 获取结构化总结模板
prompt = CONTENT_SUMMARY_PROMPT

# 格式化笔记（添加元数据头）
note = format_note_with_prompt(
    content="总结内容",
    author="作者名",
    url="https://example.com/article",
    tags=["AI", "提示词"]
)
```

### 完整命令行用法

```bash
# 方式1：直接传 URL
python assets/run.py "https://example.com/article"

# 方式2：指定参数
python assets/run.py --url "https://example.com" --author "作者" --tags "AI,技术"

# 方式3：传原文
python assets/run.py --content "文章内容..."

# 方式4：跳过 AI 总结，直接保存已总结好的内容
python assets/run.py --summarized "总结内容..." --url "原文链接" --author "作者" --tags "AI,技术"

# 方式5：从文件读取已总结内容再保存
python assets/run.py notes/_summary.md --author "作者" --tags "AI,技术"
```

## 输出目标配置

### 本地文件（默认，无需配置）

无配置时自动保存到项目 `notes/` 目录。

### Obsidian 知识库

```env
OBSIDIAN_VAULT_PATH=D:\你的Obsidian库路径
```

### 飞书知识库

先安装飞书 CLI：
```bash
npx @larksuite/cli@latest install
lark-cli config init
```

然后在 `.env` 中配置：
```env
FEISHU_WIKI_SPACE=你的知识库空间ID
FEISHU_WIKI_PARENT_NODE=父节点Token  # 可选，不填则保存到根目录
```

**获取方式**：

推荐通过 CLI 命令获取：
```bash
lark-cli wiki +space-list        # 获取知识库空间列表（含空间ID）
lark-cli wiki +node-list --space-id <空间ID>  # 获取节点列表（含父节点Token）
```

也可从分享链接提取：
- 空间ID：`https://xxx.feishu.cn/wiki/space/<FEISHU_WIKI_SPACE>`
- 节点Token：`https://xxx.feishu.cn/wiki/<FEISHU_WIKI_PARENT_NODE>`

## AI Provider 详细配置

各 Provider 的 API Key 获取地址：

| Provider | 获取地址 |
|----------|----------|
| OpenAI | https://platform.openai.com/api-keys |
| Anthropic | https://console.anthropic.com/settings/keys |
| Google | https://aistudio.google.com/app/apikey |

配置示例：

**OpenAI**
```env
AI_PROVIDER=openai
OPENAI_API_KEY=sk-xxxx
# OPENAI_MODEL=gpt-4o-mini  # 可选，默认 gpt-4o-mini
```

**Anthropic Claude**
```env
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-xxxx
# ANTHROPIC_MODEL=claude-sonnet-4-20250514  # 可选
```

**Google Gemini**
```env
AI_PROVIDER=google
GOOGLE_API_KEY=xxxx
# GOOGLE_MODEL=gemini-2.0-flash  # 可选
```

**本地 Ollama**
```env
AI_PROVIDER=local
LOCAL_API_BASE=http://localhost:11434/v1
# LOCAL_MODEL=llama3  # 可选
```

## 降级处理流程

当未配置任何外部 AI Provider 时：

1. 正常抓取网页文章内容
2. 返回 `need_continue_summary=True` 及 `CONTENT_SUMMARY_PROMPT`
3. 当前对话模型使用该 Prompt 进行结构化总结
4. 总结完成后调用 `save_summary_only()` 或 `save_summarized_article()` 保存

## 项目结构

```
blog-article-skill/
├── assets/
│   ├── __init__.py          # 模块导出
│   ├── ai_provider.py       # AI Provider 架构
│   ├── prompt.py            # 结构化总结 Prompt 模板
│   ├── base.py              # 输出模块基类
│   ├── local.py             # 本地文件输出
│   ├── obsidian.py          # Obsidian 输出
│   ├── feishu.py            # 飞书知识库输出
│   ├── manager.py           # 输出管理器
│   ├── main.py              # 主入口与完整流程
│   ├── run.py               # 命令行入口
│   └── _save_summary.py     # 外层对话保存入口
├── references/
│   └── config.md            # 配置详细说明
├── .env.example             # 环境变量模板
├── pyproject.toml           # 项目依赖
├── SKILL.md                 # AI 技能规则（供 AI 模型读取）
└── README.md                # 本文件
```

## 许可证

MIT