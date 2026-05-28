---
name: blog-article-skill
description: "文章结构化总结与多渠道归档技能：抓取博客链接/原文 → AI 结构化总结 → 自动保存到本地/Obsidian/飞书"
---

# blog-article-skill

## 1. 什么时候触发

**必须同时满足以下两个条件**，才激活本技能：

**条件一：用户说了这些词**（任一即可）
- 总结 / 提炼 / 整理 / 归档 / 保存笔记

**条件二：用户给了以下素材**（任一即可）
- 一个文章链接（百度、掘金、头条、CSDN、微信公众号等）
- 直接粘贴了文章原文

✅ **触发例子**：
- 「帮我总结这篇文章：https://xxx」
- 「把这篇内容整理成笔记：<原文>」

❌ **不触发**：
- 「帮我总结一下这类文章的写法」（没链接也没原文）
- 「提炼是什么意思？」

> 如果用户同时给了链接和原文，**优先用链接**。
> 如果用户只说了关键词但没给任何素材，**不许激活技能**，当普通聊天处理。

## 2. 执行前的检查

1. **公开免费链接**才能自动抓取。链接需要登录、付费、被反爬拦截时——**立即停**，告诉用户：请手动复制全文原文发送，我将继续整理总结。
2. 抓取到的正文如果**少于 100 字**，也当抓取失败处理，提示用户手动贴原文。
3. 飞书输出需要安装飞书 CLI 并完成配置（见 README）。
4. Obsidian 输出需要配置 `OBSIDIAN_VAULT_PATH`（见 README）。
5. 啥都没配的时候，默认存到 `notes/` 目录。

## 3. 执行流程

```
用户触发 → 抓取内容（或直接用原文） → AI 结构化总结 → 保存到配置的目标
```

### 3.1 内容获取
- 有链接：调用 `fetch_web_content(url)` 抓取
- 有原文：直接用
- 有链接也有原文：用链接

### 3.2 AI 总结
- 检测有没有配置外部 AI Provider（OpenAI/Claude/Gemini/本地模型）
- **有外部 Provider**：调用它按 `CONTENT_SUMMARY_PROMPT` 模板做结构化总结
- **没有外部 Provider**：走降级模式——只抓内容不总结，返回 `need_continue_summary=True`，把原文和 `CONTENT_SUMMARY_PROMPT` 甩给外层对话模型处理

### 3.3 保存
- 总结完成后，自动保存到所有已配置的目标（本地 + Obsidian + 飞书）
- 文件名规则：`文章标题-年月日.md`
- 同名文件自动加序号：`文章标题-1.md`，不会覆盖
- 非法文件名符号自动过滤：`\ / : * ? " < > |`

## 4. 输出规则（重要）

### 4.1 对话窗口里能说啥

| ✅ 可以说 | ❌ 不能说 |
|-----------|-----------|
| 执行进度说明（正在抓取、正在总结...） | 完整的结构化笔记正文 |
| 抓取状态（成功/失败） | 可直接复制保存的 Markdown 成品 |
| 异常提示（链接失效、抓取失败等） | 核心要点全文 |
| 保存结果（已保存到 xxx） | 总结出来的完整内容 |

**禁止**在对话框输出任何"可直接复制进知识库"的完整内容。可以说 1~3 句口头结论，但不能把全文贴出来。

执行完后只输出一句话：
> 流程执行完毕，总结成品已自动写入对应文件

### 4.2 文件写入规则
1. **禁止用 Shell 命令写文件**（PowerShell/CMD/bash 都不行）。用 Python 原生文件 IO 写入。
2. **唯一例外**：调用飞书 CLI 上传到知识库。
3. 写入编码统一用 **UTF-8**。
4. 写入失败只报错，不改路径重试。

## 5. 异常处理

| 情况 | 怎么做 |
|------|--------|
| 链接抓取失败（含 < 100 字） | 提示用户手动粘贴原文 |
| 抓取成功但 AI 总结失败 | 保存原始内容到 `notes/`，告诉用户手动处理 |
| 文件写入失败 | 返回异常原因，不自动换路径 |
| 飞书 CLI 没装或没配置 | 跳过飞书输出，不影响其他目标 |
| Obsidian 路径没配置 | 跳过 Obsidian 输出，不影响其他目标 |

## 6. 降级模式（无外部 AI Provider）

啥外部 AI 都没配的时候：
1. 正常抓取网页内容
2. 调用 `skill_main()` 返回以下结构，`need_continue_summary` 设为 `True`
3. **外层对话接手总结**：必须用返回的 `prompt`（即 `CONTENT_SUMMARY_PROMPT`）做结构化总结
4. 外层总结完了，调用 `save_summary_only()` 或 `save_summarized_article()` 保存

```python
{
    'success': True,
    'need_continue_summary': True,
    'message': '⚠️ AI Provider 暂不可用，已成功抓取文章内容，请使用 CONTENT_SUMMARY_PROMPT 进行结构化总结',
    'article_content': '...',
    'prompt': '...',               # 外层必须用这个 prompt 总结
    'original_url': '...',
    'original_title': '...',
    'author': '...',
    'tags': [...]
}
```

## 7. 核心接口速查（给 AI 调用用）

| 函数 | 干啥的 |
|------|--------|
| `fetch_web_content(url)` | 抓网页，返回 `(title, content)` 或 `None` |
| `summarize_and_save(url, author, tags)` | 全自动：抓→总结→保存，一步到位 |
| `skill_main(params_dict)` | 技能系统入口，处理链接/原文/降级逻辑 |
| `save_summarized_article(content, url, author, tags)` | 保存已总结好的内容到所有目标 |
| `save_summary_only(input_data)` | 降级模式下，外层总结完后的保存入口 |
| `CONTENT_SUMMARY_PROMPT` | 结构化总结模板，AI 必须按这个格式输出 |

> 以上函数的详细参数说明见 [README.md](README.md#python-api-调用) 或源码注释。