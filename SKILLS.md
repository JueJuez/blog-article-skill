# blog-article-skill 代码与指令

## 技能实现

### 目录结构

```
assets/
├── __init__.py        # 模块导出
├── prompt.py          # 内容总结Prompt模板
├── base.py            # 输出模块基类
├── local.py           # 本地文件输出
├── obsidian.py        # Obsidian输出
├── feishu.py          # 飞书知识库输出
└── manager.py         # 输出管理器
```

### 核心代码示例

#### 1. 内容总结

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

#### 2. 输出管理

```python
from assets.output import OutputManager

# 创建输出管理器
manager = OutputManager()

# 保存到所有可用位置
manager.save_all(formatted_note, "文章标题.md")

# 或保存到指定位置
manager.save_to(formatted_note, "文章标题.md", "local")   # 本地
manager.save_to(formatted_note, "文章标题.md", "obsidian") # Obsidian
manager.save_to(formatted_note, "文章标题.md", "feishu")   # 飞书
```

### API 参考

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

### 3. 完整执行入口

#### 方式一：Python 直接调用（自动保存）
```python
from assets import summarize_and_save, skill_main

# 方式1：使用 summarize_and_save（推荐，自动保存）
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

# 方式2：使用 skill_main（技能系统调用入口）
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

### 配置要求

#### Obsidian

**前提条件**：已安装 Obsidian 客户端

**配置步骤**：
1. 在 `.env` 文件中添加：
   ```env
   OBSIDIAN_VAULT_PATH=D:\你的Obsidian库路径
   ```

#### 飞书

**前提条件**：
1. 安装飞书CLI：`npx @larksuite/cli@latest install`
2. 完成飞书应用配置：`lark-cli config init`

**配置步骤**：
1. 在 `.env` 文件中添加：
   ```env
   FEISHU_WIKI_SPACE=你的知识库空间ID
   ```