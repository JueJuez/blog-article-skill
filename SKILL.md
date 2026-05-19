---
name: blog-article-skill
description: "文章总结与输出技能：支持将博客链接或原文总结成结构化笔记，并输出到本地、Obsidian或飞书知识库"
---

# blog-article-skill

## 1. 何时使用本 Skill

### 1.1 触发条件

以下场景 **必须同时满足** 才触发本 Skill：

**触发动作关键词**（任一）：
- 总结
- 提炼
- 整理
- 归档
- 保存笔记

**输入素材类型**（任一）：
- 提供文章链接（百度、掘金、头条、CSDN、微信公众号等）
- 手动粘贴文章原文

✅ **触发示例**：
- 「帮我总结这篇文章：https://xxx」
- 「把这篇内容整理成笔记：<原文>」

❌ **不触发示例**：
- 「你能帮我总结一下这类文章的写法吗？」（无链接 / 无原文）
- 「提炼是什么意思？」

若用户同时提供链接与原文，**链接优先**。
> ⚠️ 若用户仅使用动作关键词，但未提供任何链接或原文，  
> **不得激活本 Skill，应以普通对话方式响应。**

### 1.2 前置约束
1、公开免费无限制链接，自动抓取原文；
2、链接存在付费、登录、加密、反爬限制，导致模型抓取失败、解析失效、权限拦截时，立即终止自动抓取，模型必须提示：请手动复制全文原文发送，我将继续整理总结。
3、飞书知识库：需安装飞书CLI并完成配置。
4、Obsidian：需配置 OBSIDIAN_VAULT_PATH 环境变量。
5、无任何配置时，默认保存至本地 notes/ 目录。
6、若抓取到的正文长度 < 100 字，视为抓取失败，提示用户手动粘贴原文。

### 1.3 全局强制输出约束（精准区分规则）
1.3.1 对话窗口输出区分规则
| 允许输出 | 禁止输出 |
|--------|--------|
| 执行流程说明 | 完整结构化笔记 |
| 抓取状态 | 成品 Markdown |
| 异常提示 | 可直接入库的正文 |
| 保存结果提示 | 核心要点全文 |

✅ **允许在对话中简要口述结论**（1～3 句）  
❌ **禁止输出任何“可直接复制进知识库”的内容**

✅ 执行完成后仅输出：
✅ 流程执行完毕，总结成品已自动写入对应文件

### 1.3.2 文件写入强制稳定规则（解决 PowerShell 问题）
1. 全流程**默认禁用 PowerShell / CMD / Shell 命令行写入**，统一使用 Python 原生文件IO完成落地
2. 例外豁免：**飞书CLI调用为唯一特许Shell行为**，不受本条禁用规则限制
3. 本地存储路径固定：项目根目录 `notes/`，程序自动判空新建目录，不中断流程
4. 所有笔记文件统一使用 `UTF-8` 编码写入，杜绝乱码
5. 写入失败仅返回异常原因提示，不自动更换存储路径

### 1.4 AI Provider 不可用时的降级处理

当没有配置任何外部 AI Provider（OpenAI/Claude/Gemini/本地模型）时，说明用户希望使用当前对话大模型进行总结：
1. **内容抓取**：仍可正常抓取网页文章内容
2. **返回结构化信息**：`skill_main` 返回 `need_continue_summary=True`，包含已抓取的 `article_content` 和 `prompt`（CONTENT_SUMMARY_PROMPT）
3. **外层对话接手**：外层对话**必须**使用返回的 `prompt`（CONTENT_SUMMARY_PROMPT）来指导 AI 进行结构化总结
4. **最终保存**：外层总结完成后，调用 `save_summary_only()` 或 `save_summarized_article()` 自动保存到配置的目标位置

> 本 SKILL 为通用标准设计，不绑定任何特定平台 SDK。
> 无论是外部 API 路径还是降级路径，AI 总结都必须通过 CONTENT_SUMMARY_PROMPT 来保证输出格式统一。

**返回格式示例**：
```python
{
    'success': True,
    'need_continue_summary': True,
    'message': '⚠️ AI Provider 暂不可用，已成功抓取文章内容，请使用 CONTENT_SUMMARY_PROMPT 进行结构化总结',
    'article_content': '...',      # 已抓取的原文
    'prompt': '...',                # CONTENT_SUMMARY_PROMPT（外层必须使用此 prompt 总结）
    'original_url': '...',          # 原文链接
    'original_title': '...',         # 文章标题
    'author': '...',                # 作者
    'tags': [...]                   # 标签
}
```

### 1.5 文件命名与冲突处理
1. 默认命名格式：文章标题-年月日.md
2. 自动过滤Windows非法文件名：\ / : * ? " < > |
3. 无标题原文统一命名：未命名笔记-时间戳.md
4. 同名文件自动追加序号：文章标题-1.md、文章标题-2.md
5. 默认禁止覆盖已有笔记文件

## 2. 模块与命令导航

### 2.1 模块地图

| 大模块 | 处理什么问题 | 包含的小模块 |
|--------|-------------|-------------|
| AI调用模块 | 支持多种外部 AI Provider（OpenAI/Anthropic/Google/Local），无配置时降级由外层对话（不限平台）接手 | ai_provider.py |
| 内容总结模块 | 将输入内容按 CONTENT_SUMMARY_PROMPT 规则结构化总结 | prompt.py |
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
blog-article-skill/
├── assets/
│   ├── __init__.py        # 模块导出
│   ├── ai_provider.py     # AI Provider 架构（配置驱动）
│   ├── prompt.py          # 内容总结Prompt模板
│   ├── base.py            # 输出模块基类
│   ├── local.py           # 本地文件输出
│   ├── obsidian.py        # Obsidian输出
│   ├── feishu.py          # 飞书知识库输出
│   ├── manager.py         # 输出管理器
│   ├── main.py            # 主入口与完整流程
│   ├── run.py             # 命令行入口脚本（稳定执行入口）
│   └── _save_summary.py   # 外层对话保存入口（解决Shell引号问题）
├── references/
│   └── config.md          # 飞书/Obsidian 配置说明
├── .env                   # 配置文件（用户配置）
├── .env.example           # 配置示例模板
├── pyproject.toml         # 项目依赖配置
└── SKILL.md               # 技能说明文档
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

#### AI Provider 模块

| 函数/类 | 用途 | 参数 |
|---------|------|------|
| `get_ai_provider(name=None)` | 获取 AI Provider（含 Trae），为 None 时自动检测全部 | `name`: Provider 名称 |
| `get_external_ai_provider()` | 仅获取外部 Provider（不含 Trae），降级判断用 | 无参 |
| `has_external_provider()` | 检查是否有可用的外部 Provider | 无参 |
| `list_available_providers()` | 列出所有可用的 Provider（含 Trae） | 无参 |
| `list_external_providers()` | 仅列出外部可用的 Provider | 无参 |
| `call_ai_summarize(prompt, content)` | 调用任意 AI Provider 总结（含 Trae） | `prompt`, `content` |
| `call_external_ai_summarize(prompt, content)` | 仅调用外部 Provider 总结，无配置返回 None | `prompt`, `content` |
| `AIProviderManager` | Provider 管理器，提供配置检测与切换 | - |

#### AI Provider 类

| 类 | 说明 |
|----|------|
| `AIProviderBase` | 抽象基类，所有 Provider 继承 |
| `TraeProvider` | Trae SDK Provider（子会话模式） |
| `OpenAIProvider` | OpenAI API Provider |
| `AnthropicProvider` | Anthropic Claude API Provider |
| `GoogleProvider` | Google Gemini API Provider |
| `LocalProvider` | 本地模型 Provider（Ollama 等） |
| `MockProvider` | 模拟 Provider（仅测试用） |

#### OutputManager

| 方法 | 用途 | 参数 |
|------|------|------|
| `get_available_outputs()` | 获取所有可用输出模块 | 无 |
| `save_all(content, filename)` | 保存到所有可用位置 | content: 内容, filename: 文件名 |
| `save_to(content, filename, target)` | 保存到指定位置 | target: "local"/"obsidian"/"feishu" |

#### Prompt 模块

| 方法 | 用途 |
|------|------|
| `get_content_summary_prompt()` | 获取内容总结Prompt模板 |
| `format_note_with_prompt(content, author, url, tags)` | 格式化笔记内容（添加元数据） |

#### 辅助函数

| 方法 | 用途 |
|------|------|
| `fetch_web_content(url)` | 获取网页内容（返回(title, content)或None） |
| `extract_article_title(content)` | 从内容中提取文章标题 |
| `generate_filename(title, url)` | 生成安全的文件名 |
| `summarize_content(content, author, url, tags, original_title)` | 调用 AI 对内容进行结构化总结，返回总结内容或 None |
| `save_summarized_article(summarized_content, ...)` | 保存已总结的文章到所有可用目标（返回格式化笔记和文件名） |
| `save_summary_only(input_data)` | 外层兜底总结后的保存入口，接收字典格式 |
| `save_raw_content_to_file(content, title)` | 降级流程中保存原始内容到 notes/ 目录 |
| `save_summarized_from_file(filepath, ...)` | 从文件读取已总结内容并保存 |

#### 输出模块类

| 类 | 说明 |
|----|------|
| `BaseOutput` | 输出模块抽象基类 |
| `LocalOutput` | 本地文件输出到 notes/ 目录 |
| `ObsidianOutput` | 输出到 Obsidian 知识库 |
| `FeishuOutput` | 输出到飞书知识库 |

#### 常量

| 常量 | 说明 |
|------|------|
| `CONTENT_SUMMARY_PROMPT` | 结构化总结 Prompt 模板 |

### 3.4 完整执行入口

#### 方式一：Python 直接调用（自动保存）
```python
from assets import summarize_and_save, skill_main

# 处理博客链接（返回4个值：总结内容, 原文内容, URL, 标题）
summarized, article_content, original_url, original_title = summarize_and_save(
    "https://example.com/article",
    author="作者名",
    tags=["人工智能", "技术"]
)

# 使用 skill_main（技能系统调用入口）
result = skill_main({
    'content': 'https://example.com/article',
    'author': '作者名',
    'tags': ['AI', '技术']
})

# 返回值说明：
# AI成功时：
# {
#     'success': True,
#     'message': '文章总结已自动保存！',
#     'filename': '文件名.md',
#     'content': '总结内容'
# }
#
# AI不可用时（降级处理）：
# {
#     'success': True,
#     'need_continue_summary': True,
#     'article_content': '...',      # 已抓取的原文
#     'original_url': '...',          # 原文链接
#     'original_title': '...',        # 文章标题
#     'author': '...',
#     'tags': [...]
# }
```

#### 方式二：命令行运行（推荐）
```bash
cd d:\Code\Skills\test\.trae\skills\blog-article-skill

# 方式1：直接传 URL
python assets/run.py "https://example.com/article"

# 方式2：传 URL + 作者 + 标签
python assets/run.py --url "https://example.com" --author "作者" --tags "AI,技术"

# 方式3：直接传文章内容
python assets/run.py --content "文章内容..."

# 方式4：直接传入已总结好的内容（跳过AI总结，直接保存）
python assets/run.py --summarized "总结内容..." --url "原文链接" --author "作者" --tags "AI,技术"

# 方式5：从文件读取已总结内容并保存
python assets/run.py notes/_summary.md --author "作者" --tags "AI,技术"
```

#### 方式三：自动保存流程说明
当调用 `summarize_and_save()` 或 `skill_main()` 时，系统会自动执行以下流程：
1. **获取内容**：自动抓取网页内容或直接使用提供的文本
2. **AI总结**：使用 CONTENT_SUMMARY_PROMPT 进行结构化总结
3. **自动保存**：保存到所有配置的目标位置（本地/Obsidian/飞书）

## 4. 配置说明

配置文件 `.env`：

```env
# AI Provider 配置
AI_PROVIDER=openai  # 可选：trae/openai/anthropic/google/local/mock
OPENAI_API_KEY=sk-xxxx

# 输出目标配置
OBSIDIAN_VAULT_PATH=D:\你的Obsidian库路径
FEISHU_WIKI_SPACE=你的知识库空间ID
FEISHU_WIKI_PARENT_NODE=父节点Token
```

### 4.1 AI Provider 配置

#### 4.1.1 Provider 类型

| Provider | 说明 | 配置要求 | 自动检测 |
|----------|------|----------|----------|
| `trae` | Trae SDK | 需安装 trae Python 包 | ❌ 需手动配置 |
| `openai` | OpenAI API | `OPENAI_API_KEY` | ✅ 如果配置了 Key |
| `anthropic` | Anthropic Claude API | `ANTHROPIC_API_KEY` | ✅ 如果配置了 Key |
| `google` | Google Gemini API | `GOOGLE_API_KEY` | ✅ 如果配置了 Key |
| `local` | 本地模型（Ollama） | `LOCAL_API_BASE` | ✅ 如果配置了地址 |
| `mock` | 模拟 Provider | 无 | ❌ 仅当显式设置 |

> ⚠️ **注意**：当没有任何 AI Provider 可用时，技能会降级处理：
> 1. 仍可正常抓取网页文章内容
> 2. 返回 `need_continue_summary=True`，由外层对话接手总结

#### 4.1.2 自动检测逻辑

```
1. 优先使用 AI_PROVIDER 环境变量指定的外部 Provider
2. 如果未指定，按优先级检测：openai > anthropic > google > local
3. Trae SDK 不参与自动检测，需显式设置 AI_PROVIDER=trae
4. 使用第一个可用的 Provider
```

#### 4.1.3 配置示例

**OpenAI**
```env
AI_PROVIDER=openai
OPENAI_API_KEY=sk-xxxx
# 可选：OPENAI_MODEL=gpt-4o-mini
```

**Anthropic Claude**
```env
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-xxxx
# 可选：ANTHROPIC_MODEL=claude-sonnet-4-20250514
```

**Google Gemini**
```env
AI_PROVIDER=google
GOOGLE_API_KEY=xxxx
# 可选：GOOGLE_MODEL=gemini-2.0-flash
```

**本地 Ollama**
```env
AI_PROVIDER=local
LOCAL_API_BASE=http://localhost:11434/v1
# 可选：LOCAL_MODEL=llama3
```

**Mock（仅测试）**
```env
AI_PROVIDER=mock
```

#### 4.1.4 API Key 获取地址

| Provider | 获取地址 |
|----------|----------|
| OpenAI | https://platform.openai.com/api-keys |
| Anthropic | https://console.anthropic.com/settings/keys |
| Google | https://aistudio.google.com/app/apikey |

### 4.2 Obsidian 配置

**前提条件**：已安装 Obsidian 客户端

**配置步骤**：
1. 在 `.env` 文件中添加：
   ```env
   OBSIDIAN_VAULT_PATH=D:\你的Obsidian库路径
   ```

### 4.3 飞书配置

**前提条件**：
1. 安装飞书CLI：`npx @larksuite/cli@latest install`
2. 完成飞书应用配置：`lark-cli config init`

**配置步骤**：
1. 在 `.env` 文件中添加：
   ```env
   FEISHU_WIKI_SPACE=你的知识库空间ID
   FEISHU_WIKI_PARENT_NODE=父节点Token  # 可选，不填则保存到知识库根目录
   ```

**获取方式**：

#### 方式一：通过 CLI 命令获取（推荐）
```bash
# 获取所有知识库空间列表（包含空间ID）
lark-cli wiki +space-list

# 获取指定空间下的节点列表（包含父节点Token）
lark-cli wiki +node-list --space-id <空间ID>
```

#### 方式二：通过分享链接提取
**FEISHU_WIKI_SPACE（空间ID）**：
- 在知识库页面点击「分享」→「复制链接」
- 链接格式：`https://xxx.feishu.cn/wiki/space/<FEISHU_WIKI_SPACE>`
- 例如：`https://r1t40urlzrp.feishu.cn/wiki/space/7636965310725115074`
- **提取**：`7636965310725115074`

**FEISHU_WIKI_PARENT_NODE（父节点Token）**：
- 在目标文件夹/节点页面点击「分享」→「复制链接」
- 链接格式：`https://xxx.feishu.cn/wiki/<FEISHU_WIKI_PARENT_NODE>`
- 例如：`https://r1t40urlzrp.feishu.cn/wiki/FX33wKHwZiMzJqk7BQQctHD3nKh`
- **提取**：`FX33wKHwZiMzJqk7BQQctHD3nKh`

#### 配置建议

| 场景 | FEISHU_WIKI_SPACE | FEISHU_WIKI_PARENT_NODE |
|-----|-------------------|------------------------|
| 保存到知识库根目录 | 必填 | 留空 |
| 保存到指定文件夹 | 必填 | 必填 |

## 5. 参考文档

详细配置说明见 [references/config.md](references/config.md)。