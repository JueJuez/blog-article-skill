# Blog Article Skill

一款通用的博客文章结构化总结与多渠道归档工具。支持将任意博客链接或文章原文，通过 AI 自动生成结构化笔记，并保存到本地、Obsidian 或飞书知识库。

## 功能特性

### 文章模块（articles）
- **增强抓取（A1）**：trafilatura（主力）+ readability-lxml（次选）+ 原 bs4 兜底三层提取，自动剥离导航/广告，保留 sina/baijiahao/og:title 标题特例
- **增量去重（A2）**：按规范化 URL（或正文 hash）持久化索引，重复链接/原文自动跳过，避免重复消耗 token
- **自适应总结**：内置 `prompts` 模板注册表，支持结构化复盘 / 要点提炼 / 案例拆解 / 观点卡等多种笔记形态，未指定时按标题自动分类
- **标签建议（A5）**：未指定 tags 时由笔记类型 + 内容关键词自动生成默认标签
- **Provider 健壮性（A4）**：限流/瞬错自动重试 + 指数退避；总结返回 token 用量并写入笔记 frontmatter
- **批量目录（A3）**：`--batch <dir>` 对目录下所有 `.md/.txt` 原文逐篇总结
- **多渠道输出**：本地文件 / Obsidian / 飞书知识库（CLI 走 stdin，安全清理不崩主流程）
- **WB 内置 AI 适配（A6/C2）**：best-effort 接入 WorkBuddy 内置 AI 作为降级增强，无则静默跳过，绝不阻断

### 视频模块（videos）
- **字幕自动抓取（P2.1）**：YouTube（youtube-transcript-api v1.x 直连）/ Bilibili（原生 API + yt-dlp 兜底）自动获取字幕，无需手动下载
- **分块两段式总结（P2.2）**：超长 transcript 按章节/时间窗切分 → 逐块小结 → 二次合并，绝不爆上下文
- **分集 / playlist 迭代（P2.3）**：自动解析 playlist 逐条总结，并可生成「系列总览」
- **本地/任意视频 ASR（P3）**：无字幕时经 yt-dlp 抽音频 + faster-whisper 本地免费转写
- **多模态理解（P4，可选）**：采样帧 + Gemini 视频理解（best-effort，无 Gemini 时优雅跳过）

### 共享模块（shared）
- **分块引擎（C1）**：`chunk_text` / `chunk_segments` / `two_stage_summarize`，被视频模块复用

## 快速开始

### 安装

```bash
pip install -e .
```

可选依赖（按需安装）：
```bash
pip install -e ".[openai]"      # OpenAI
pip install -e ".[anthropic]"   # Anthropic Claude
pip install -e ".[google]"      # Google Gemini（P4 多模态也需要）
pip install -e ".[async]"       # 异步抓取（aiohttp）
pip install -e ".[extract]"     # A1 增强抓取：trafilatura + readability-lxml
pip install -e ".[video]"       # P2.1 字幕抓取：youtube-transcript-api>=1.0 + yt-dlp
pip install -e ".[asr]"         # P3 本地转写：faster-whisper（另需 ffmpeg）
# 一键装全（含视频/多模态）：
pip install -e ".[extract,video,asr,google]"
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

# 视频字幕抓取代理（可选）：仅本机裸跑且需代理时设置，脚本自动映射到 HTTP(S)_PROXY
# YT_PROXY=http://127.0.0.1:7890

# 质量闸门（可选 · 默认关闭）：总结后由 AI 二次把关，低于阈值重试一次；省一轮调用故默认关
# NOTE_QUALITY_GATE=1
# NOTE_GATE_THRESHOLD=85
```

### 使用示例

```python
from articles import summarize_and_save, skill_main

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
python articles/run.py "https://example.com/article"

# 处理原文
python articles/run.py --content "文章原文..."

# 指定作者和标签
python articles/run.py --url "https://example.com" --author "作者" --tags "AI,技术"

# 批量目录：对目录下所有 .md/.txt 原文逐篇总结
python articles/run.py --batch ./my_articles/

# 强制重跑（忽略去重，--force）
python articles/run.py --content "原文..." --force

# 直接保存已总结好的内容（跳过 AI 总结）
python articles/run.py --summarized "总结内容..." --url "原文链接" --author "作者" --tags "AI,技术"
```

## 视频总结（videos 模块）

对视频/音频的字幕或 transcript 做结构化笔记，复用 articles 的抓取/保存能力。

```python
from videos import summarize_video

# P1：直接传字幕/转录文本
summarize_video({"content": "字幕文本...", "note_type": "key_points"})

# P2.1：YouTube / Bilibili 单视频（自动抓 CC 字幕）
summarize_video({"url": "https://www.youtube.com/watch?v=xxxx", "note_type": "key_points"})

# P2.3：playlist / 合集（逐条总结 + 系列总览）
summarize_video({"url": "https://www.youtube.com/playlist?list=PLxxx", "playlist": True})

# P3：本地视频/音频（无字幕 → ASR 转写，需 yt-dlp + ffmpeg + faster-whisper）
summarize_video({"file": "/path/to/video.mp4", "note_type": "key_points"})

# P4：多模态画面理解（可选，需已配置的 Google Gemini Provider）
summarize_video({"url": "https://www.youtube.com/watch?v=xxxx", "multimodal": True})
```

命令行入口：

```bash
python videos/run.py --url "https://www.youtube.com/watch?v=xxxx"
python videos/run.py --playlist "https://www.youtube.com/playlist?list=PLxxx"
python videos/run.py --file "/path/to/video.mp4"
```

> 任意外部依赖缺失或网络失败，均优雅降级（返回 `need_continue_summary` 或提示），
> 绝不因某个库不可用而整体崩溃。

### 字幕获取说明（P2.1 实测要点）

- **YouTube**：经 `youtube-transcript-api` 直连获取 CC 字幕，**在 WorkBuddy 沙箱内可直接运行，无需本地代理或浏览器**。库每次调用实时抓取 watch 页并现场生成签名，不存在"URL 过期"问题（旧版手动拼的 `signature` URL 几小时即失效，请勿手搓）。
- **Bilibili**：走原生 API 链路（view → dm/view → aisubtitle，无需登录 cookie；仅当 AI 字幕缺失时回退 yt-dlp 兜底）。
- **字幕轨道**：YouTube 公开视频通常只有一条 CC 字幕轨道（可能是中英混合的单一轨道）。`fetch_youtube_transcript` 默认按 `("zh-Hans", "zh", "en")` 优先级自动选择，可通过 `languages` 参数覆盖。
- **代理（可选）**：本机裸跑且仅有本地代理端口时，设 `YT_PROXY=http://127.0.0.1:7890` 即可复用（见上方配置说明）。

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
from articles.manager import OutputManager

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
from articles.prompt import CONTENT_SUMMARY_PROMPT, format_note_with_prompt

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
python articles/run.py "https://example.com/article"

# 方式2：指定参数
python articles/run.py --url "https://example.com" --author "作者" --tags "AI,技术"

# 方式3：传原文
python articles/run.py --content "文章内容..."

# 方式4：跳过 AI 总结，直接保存已总结好的内容
python articles/run.py --summarized "总结内容..." --url "原文链接" --author "作者" --tags "AI,技术"

# 方式5：从文件读取已总结内容再保存
python articles/run.py notes/_summary.md --author "作者" --tags "AI,技术"
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
├── articles/                 # 文章/博客总结模块（A1–A6, C2）
│   ├── __init__.py           # 模块导出
│   ├── ai_provider.py        # AI Provider 架构（A4：重试/退避、token 用量）
│   ├── fetch.py              # 文章抓取（A1：trafilatura/readability/bs4）
│   ├── dedup.py              # 增量去重（A2）
│   ├── prompt.py             # 结构化总结 Prompt 模板
│   ├── base.py               # 输出模块基类
│   ├── local.py              # 本地文件输出
│   ├── obsidian.py           # Obsidian 输出
│   ├── feishu.py             # 飞书知识库输出
│   ├── manager.py            # 输出管理器
│   ├── main.py               # 主入口与完整流程
│   ├── run.py                # 命令行入口
│   └── _save_summary.py      # 外层对话保存入口
├── videos/                   # 视频总结模块（P2.1/P2.2/P2.3/P3/P4）
│   ├── __init__.py
│   ├── fetch.py              # 字幕抓取（P2.1：YouTube/Bilibili）
│   ├── cdp_launch.py         # 确保本机带代理插件的 Chrome(CDP 副本) 调试端口就绪（强制同步配置 + 启动）
│   ├── cdp_capture.py        # 经 CDP 拦截 YouTube 字幕响应体（本机无 YouTube 出口时的终极解法）
│   ├── asr.py                # 本地语音识别（P3：faster-whisper）
│   ├── multimodal.py         # 多模态理解（P4：Gemini）
│   ├── main.py               # 视频总结主流程（分块两段式 P2.2/P2.3）
│   └── run.py                # 命令行入口
├── shared/                   # 跨模块共享（C1/C2）
│   ├── __init__.py
│   ├── chunking.py           # 文本/字幕分块与两段式总结
│   └── wb_ai.py              # WorkBuddy 内置 AI 适配器
├── prompts/                  # 共享笔记模板注册表
│   ├── __init__.py
│   ├── templates.py          # NOTE_TEMPLATES + classify_note_type
│   └── classify.py           # 笔记类型分类
├── monitors/                 # 订阅监控（B站UP主 / 公众号）：发现新内容→AI总结→双写
│   ├── bilibili.py           # B站源（官方 API + WBI 签名，带登录 Cookie）
│   ├── wechat.py             # 公众号源（经 weread 代理发现新文）；token 数小时失效，交互式弹码续期、headless 跳过
│   ├── state.py              # 每源去重状态 + 防膨胀裁剪
│   ├── ad_filter.py          # 广告过滤（整篇纯广告 skip / 干货夹广告净化）
│   ├── run.py                # CLI + 调度入口（--apply 直接调总结管线）
│   ├── subscriptions.example.json  # 订阅配置模板
│   └── README.md             # 监控运营文档 + 已知坑
├── tests/                    # PRD 验收测试（pytest）
│   └── test_prd.py
├── notes/                    # 原始/中间笔记（被 gitignore）
├── references/
│   ├── config.md             # 配置详细说明
│   ├── PRD.md                # 产品需求文档
│   ├── youtube-cdp-workflow.md  # YouTube 字幕 CDP 全自动抓取流程（本机无出口必读）
│   └── testing_rules.md      # TDD 流程规范（grill_rules 动手时遵循）
├── .env.example              # 环境变量模板
├── pyproject.toml            # 项目依赖
├── SKILL.md                  # AI 技能规则（供 AI 模型读取）
└── README.md                 # 本文件
```

## 跨平台加载（换会话 / 换 AI 平台也能用）

本项目的流程、功能、配置集中在根目录 **`AGENTS.md`**（平台无关真源入口）。换用其他 AI 平台时，
把 `AGENTS.md`（或 `RULES.md`）加载进模型上下文即可接上全部能力，无需 WorkBuddy 专属机制：

- **WorkBuddy**：激活 `blog-article-skill` skill；`SKILL.md` 为触发层
- **Cursor**：`.cursorrules` 或 `.cursor/rules/*.mdc`
- **Trae**：原生认 `AGENTS.md`（Settings → Import Settings 开「Include AGENTS.md in the context」）；也放 `.trae/rules/blog-article-skill.md`（`alwaysApply: true` 薄指针，确保必加载）
- **Claude Code / Desktop**：`CLAUDE.md`（可 `cp AGENTS.md CLAUDE.md`）
- **Codex / OpenAI**：`AGENTS.md`（本文件即）
- **GitHub Copilot**：`.github/copilot-instructions.md`
- **裸 API / 其他**：把 `AGENTS.md` 全文作为 system prompt 前置

> 真规则只维护一处（`AGENTS.md` + `RULES.md`），各平台入口文件引用或复制它，避免漂移。

## 许可证

MIT