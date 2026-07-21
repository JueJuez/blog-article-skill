---
name: blog-article-skill
description: "文章/视频结构化总结与多渠道归档技能：抓取链接/原文/字幕 → 按内容类型自适应 AI 总结 → 自动保存到本地/Obsidian/飞书"
---

# blog-article-skill

> 📌 **项目规则与地图索引见 [`RULES.md`](../../../RULES.md)**（执行前必读；规则以 RULES.md 为准，本文档不重复规则细节）。

## 0. 最短路径：用户给 YouTube 链接时（新会话必须照此执行）

> 本机无 YouTube 出口，走 CDP 全自动。不要绕路。

**只做这两步，不要写临时脚本，不要点 F12，不要 diagnose：**

1. **直接运行**：`videos/run.py --url <youtube_url>` 或调 `videos.fetch.fetch_transcript(<youtube_url>)`。
2. **看结果**：
   - 返回了字幕 → 继续总结/保存。
   - 返回 `None` 且页面能打开（`captionTracks` 为空）→ **原样回用户**：
     > **【此视频暂无 CC 字幕，无法为你抓取字幕总结内容。】**
   - 页面都打不开 / 9222 连不上 → 才是 CDP 机制故障，按 `references/youtube-cdp-workflow.md` 修。

**禁止行为**：
- ❌ 写 `_collect_subtitle.py` / `_debug_tmp.py` 等临时脚本。
- ❌ API 失败后去“诊断为什么失败”——`fetch_transcript` 内部会自动回退 CDP，失败即表示无字幕或机制不可用，按上表返回即可。
- ❌ 无 CC 字幕时走 ASR / 改代码“优化/开发”，除非用户明确说要做。

---

## ⚠️ 执行前强制检查（必须逐项确认，缺一不可）

**执行本技能前，AI 必须逐项确认以下内容：**

- [ ] 我已完整读完本 SKILL.md 文件
- [ ] 我已完整读完 `RULES.md` 相关章节（尤其是 §4.4 视频字幕抓取终端行为）
- [ ] 如果用户给的是 YouTube 链接，我已按本文件 §0 的最短路径执行
- [ ] 我会调用 `skill_main()` 而不是手动抓取网页
- [ ] 如果触发降级（`need_continue_summary=True`），我会用返回的 `prompt`（已按 note_type 选好）做总结
- [ ] 总结完成后，我会调用 `save_summarized_article()` 保存到所有配置目标
- [ ] 默认不在对话框输出完整笔记正文；只输出 1~3 句核心结论与成品文件路径（用户明确要求看全文时才展示）
- [ ] 执行完后输出一句话总结 + 笔记类型 + 成品路径
- [ ] **如果 YouTube 返回 None 且页面已加载、无字幕轨道，我会直接回固定文案，不诊断、不走 ASR、不开发兜底**

**违反任何一项 = 执行失败**

---

## 1. 什么时候触发

**必须同时满足以下两个条件**，才激活本技能：

**条件一：用户说了这些词**（任一即可）
- 总结 / 提炼 / 整理 / 归档 / 保存笔记

**条件二：用户给了以下素材**（任一即可）
- 一个文章链接（百度、掘金、头条、CSDN、微信公众号等）
- 直接粘贴了文章原文
- 一段视频/音频字幕或 transcript（视频模块：YouTube/Bilibili 字幕自动抓取、本地 ASR、Gemini 多模态；**YouTube：WorkBuddy 沙箱直连，或本机经 CDP 驱动带代理的 Chrome 自动抓取——详见 `references/youtube-cdp-workflow.md`**）

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

## 3. 执行规范（必须先读完再动手）

### 3.1 对话输出规则

| ✅ 可以说 | ❌ 禁止说 |
|-----------|-----------|
| 执行进度（正在抓取、正在总结...） | 直接把完整笔记正文贴进对话框（除非用户明确要求） |
| 抓取状态（成功/失败） | 可直接复制保存的 Markdown 成品全文 |
| 异常提示（链接失效、抓取失败等） | 核心要点全文 |
| 1~3 句核心结论（观点/适用人群/落点） | 未保存就声称已完成 |
| 成品文件路径 | |

**默认**不在对话框输出完整笔记正文——完整内容已写入 Obsidian / 飞书（本地 `notes/` 仅在两者都未配置时兜底），用户需要看时再用 Read 展示对应文件。

执行完后输出一句话总结 + 笔记类型 + 成品路径，例如：
> 流程执行完毕（笔记类型：要点提炼），总结成品已自动写入 Obsidian 与 飞书（飞书已配好、默认双写；本地 notes/ 仅在 Obsidian+飞书都未配置时兜底；详见 RULES.md §3.0 双写契约）。

### 3.2 文件写入规则
1. **禁止用 Shell 命令写文件**（PowerShell/CMD/bash 都不行）。用 Python 原生文件 IO 写入。
2. **唯一例外**：调用飞书 CLI 上传到知识库。
3. 写入编码统一用 **UTF-8**。
4. 写入失败只报错，不改路径重试。

### 3.3 大批量处理（>3 条）→ 子 Agent 隔离主线程
待处理 ≥ 阈值（如 >3 条笔记/视频，或单批内容大到撑爆主上下文）时，**必须**用 Agent 工具派发子 Agent 并行处理，勿把全部原文/中间稿堆在主线程。
- 子 Agent 上下文是空白的 → 派发 prompt 必须**自包含**：双写契约（Obsidian+飞书、不写本地）、入口函数（`videos/run.py --url` 或 `skill_main`）、`note_type`、YouTube/无字幕规则（按需）。
- ⚠️ 飞书并发写有**重复节点坑** → **安全模式**：子 Agent 只**返回成品文本＋元数据**，由你（编排方）**串行**调保存入口落盘，绝不让多子 Agent 并发各自 `save_series`。

---

## 4. 执行流程

**⚠️ 必须调用技能函数执行，禁止手动抓取网页或手动生成总结**

```
用户触发 → 调用 skill_main() → 技能自动完成抓取/总结/保存 → 返回结果
```

### 4.1 标准流程（推荐）

直接调用 `skill_main()` 一步到位：

```python
from articles import skill_main

result = skill_main({
    "content": "https://xxx",   # 或直接传文章原文 / 字幕文本
    "note_type": "key_points"   # 可选：structured / key_points / interview / roundup / reading / case / opinion，留空自动分类
})
# result['success'] = True 表示完成
# result['filename'] = 保存的文件名
```

### 4.2 降级模式处理

如果 `result['need_continue_summary'] == True`，说明没有外部 AI Provider：

1. 用返回的 `result['prompt']`（已按 `result['note_type']` 选好对应模板）对 `result['article_content']` 做总结
2. 调用 `skill_continue_summary()` 保存（**推荐**，参数更清晰）：

```python
from articles import skill_main, skill_continue_summary

# 第一步：调用技能
result = skill_main({"content": "https://xxx"})

# 第二步：如果需要降级
if result.get('need_continue_summary'):
    # 用 result['prompt']（对应 note_type 的模板）对 result['article_content'] 做总结
    summary = your_ai_summarize(result['article_content'], result['prompt'])

    # 第三步：保存（必须调用！）
    save_result = skill_continue_summary(
        article_content=result['article_content'],
        summary_content=summary,
        original_url=result['original_url'],
        tags=result['tags'],
        original_title=result['original_title']
    )
```

### 4.3 文件命名规则
- 文件名规则：`文章标题-年月日.md`
- 同名文件自动加序号：`文章标题-1.md`，不会覆盖
- 非法文件名符号自动过滤：`\ / : * ? " < > |`

## 5. 异常处理

| 情况 | 怎么做 |
|------|--------|
| 链接抓取失败（含 < 100 字） | 提示用户手动粘贴原文 |
| 抓取成功但 AI 总结失败 | 保留原始内容并告知用户可重新发起总结（**不要为此手动写 `notes/`**；本地仅在 Obsidian+飞书都未配时兜底） |
| 文件写入失败 | 返回异常原因，不自动换路径 |
| 飞书 CLI 没装或没配置 | 跳过飞书输出，不影响其他目标 |
| Obsidian 路径没配置 | 跳过 Obsidian 输出，不影响其他目标 |
| YouTube 视频**无 CC 字幕**（`fetch_transcript` 返回 None，且页面已加载、`captionTracks` 为空） | **原样回：「【此视频暂无 CC 字幕，无法为你抓取字幕总结内容。】」并停止**；不补 ASR、不改动代码。详见 `RULES.md` §4.4 与 `references/youtube-cdp-workflow.md` §8 |

## 6. 核心接口速查（给 AI 调用用）

| 函数 | 干啥的 |
|------|--------|
| `fetch_web_content(url)` | 抓网页，返回 `(title, content)` 或 `None` |
| `summarize_and_save(url, author, tags, note_type)` | 全自动：抓→总结→保存，一步到位 |
| `skill_main(params_dict)` | 技能系统入口，处理链接/原文/降级逻辑 |
| `skill_continue_summary(article_content, summary_content, ...)` | **降级模式专用**：AI 做完总结后调用此函数保存 |
| `save_summarized_article(content, url, author, tags)` | 保存已总结好的内容到所有目标 |
| `save_summary_only(input_data)` | 降级模式下，外层总结完后的保存入口 |
| `prompts.get_note_prompt(note_type)` | 按笔记类型取对应的总结模板 |
| `prompts.list_note_types()` | 列出所有可用笔记类型（key / 名称 / 说明） |
| `prompts.classify_note_type(title, content)` | 按标题/正文自动判定笔记类型 |

> 以上函数的详细参数说明见 [README.md](../../../README.md#python-api-调用) 或源码注释。

---

## 7. 笔记类型（note_type）与项目结构

### 7.1 笔记类型

同一套素材，不同内容该用不同笔记形态，才能让人真正理解并掌握：

| note_type | 名称 | 适用内容 | 形态特点 |
|-----------|------|----------|----------|
| `structured` | 结构化复盘笔记 | 学习/课程/教程/方法论/干货（含教学视频） | 概念→分维度拆解→正反例→步骤→可复用模板（最完整，默认兜底） |
| `key_points` | 要点提炼笔记 | 公开课/讲座/演讲/播客/视频口播（非访谈） | 核心论点+金句+行动项，轻量精炼 |
| `interview` | 访谈笔记 | 专访/对谈/播客访谈/Q&A/深度对话 | 问答对+嘉宾背景+双方分歧表格+金句+行动启发 |
| `roundup` | 盘点笔记 | N个最佳X/横评/测评/工具对比/选购指南 | 维度×对象对比矩阵+场景结论+选购建议 |
| `reading` | 读书笔记 | 书评/拆书/读后感/读书笔记/书单 | 全书地图+核心论点拆解+金句+行动启发 |
| `case` | 案例拆解笔记 | 案例/复盘/产品拆解/实战记录 | 背景→决策→结果→可复用经验 |
| `opinion` | 观点卡 | 评论/随笔/立场文/辩论/行业观点 | 核心立场+论据+正反双方+我的立场 |

- **自动分类**：调用时不传 `note_type`，技能按 `prompts/classify_note_type(title, content)` 自动判定。优先级：教学超信号(structured) > 访谈(interview) > 要点(key_points) > 盘点(roundup) > 读书(reading) > 观点(opinion) > 案例(case) > structured 兜底。其中教学/教程类视频（手把手/保姆/实操/从零/教程/课程）经「教学超信号」优先判 `structured`，不再被 `视频` 误判为口播要点；访谈（访谈/对谈/专访/Q&A）已独立成 `interview` 类（不再误判为口播要点）。
- **思维模型透镜（提质·按需）**：全部 7 模板共用 `UNIVERSAL_RULES` 第九节（structured 内联第十四节），6 模型按序 LIST（第一性原理→5-Why冰山→二阶思维→脉络还原→奥卡姆剃刀→类比迁移）逐条过、不适用跳过，直接服务「质量高、上下文清晰」且不破去水分红线。
- **手动指定**：传 `note_type` 覆盖自动判定。
- **扩展**：新增笔记类型只需在 `prompts/templates.py` 的 `NOTE_TEMPLATES` 里加一项，无需改其他代码。

### 7.2 项目三大模块

```
blog-article-skill/
├── articles/      # 文章/博客总结模块（A1–A6, C2）：抓取 + AI 总结 + 多端保存
├── videos/        # 视频总结模块（P2.1/P2.2/P2.3/P3/P4）：字幕抓取 + 分块两段式 + 本地 ASR + 多模态
│   ├── cdp_launch.py   # 确保本机带代理插件的 Chrome(CDP 副本) 调试端口就绪（每次强制同步配置含 iGuge 扩展 + 启动）
│   ├── cdp_capture.py  # 经 CDP 拦截 YouTube 字幕响应体（本机无 YouTube 出口时的终极解法）
│   └── fetch/asr/multimodal/main/run（获取层/ASR/多模态/编排/CLI）
├── shared/        # 跨模块共享（C1/C2）：chunking 两段式总结、WorkBuddy 内置 AI 适配器
├── prompts/       # 共享笔记模板模块：NOTE_TEMPLATES 注册表 + 自动分类，被 articles/videos 复用
├── tests/         # PRD 验收测试（pytest，test_prd.py）
├── notes/         # 产出与原始内容（已被 .gitignore 忽略，不进仓库）
├── references/    # 配置说明 + PRD
├── SKILL.md / README.md
└── pyproject.toml
```

> **个人信息保护**：`.env`（含 Obsidian 路径、飞书空间 ID）与 `notes/` 均已被 `.gitignore` 忽略，不会提交到 github/gitee。切勿手动 `git add .env` 或 `notes/`。
