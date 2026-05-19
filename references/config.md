# 配置说明

## 一、配置文件

配置文件 `.env` 需要放在技能根目录下：

```env
# AI Provider 配置（留空自动检测）
AI_PROVIDER=openai
OPENAI_API_KEY=sk-xxxx

# 输出目标配置
OBSIDIAN_VAULT_PATH=D:\你的Obsidian库路径
FEISHU_WIKI_SPACE=你的知识库空间ID
FEISHU_WIKI_PARENT_NODE=父节点Token
```

## 二、AI Provider 配置

### 支持的 Provider

| Provider | 说明 | 配置要求 | 获取地址 |
|----------|------|----------|----------|
| `trae` | Trae SDK（子会话模式） | 需安装 trae Python 包 | - |
| `openai` | OpenAI API | `OPENAI_API_KEY` | https://platform.openai.com/api-keys |
| `anthropic` | Anthropic Claude API | `ANTHROPIC_API_KEY` | https://console.anthropic.com/settings/keys |
| `google` | Google Gemini API | `GOOGLE_API_KEY` | https://aistudio.google.com/app/apikey |
| `local` | 本地模型（Ollama） | `LOCAL_API_BASE` | https://ollama.com |
| `mock` | 模拟 Provider | `AI_PROVIDER=mock` | 仅测试用 |

### 自动检测逻辑

1. 优先使用 `AI_PROVIDER` 环境变量指定的外部 Provider（openai/anthropic/google/local）
2. 如果未指定或指定的 Provider 不可用，按优先级自动检测：`openai` > `anthropic` > `google` > `local`
3. Trae SDK 不参与自动检测，需显式设置 `AI_PROVIDER=trae` 才会启用；无外部 Provider 时触发降级流程，由外层对话接手
4. 使用第一个可用的 Provider

### 配置示例

**OpenAI**
```env
AI_PROVIDER=openai
OPENAI_API_KEY=sk-xxxx
```

**Anthropic Claude**
```env
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-xxxx
```

**Google Gemini**
```env
AI_PROVIDER=google
GOOGLE_API_KEY=xxxx
```

**本地 Ollama**
```env
AI_PROVIDER=local
LOCAL_API_BASE=http://localhost:11434/v1
```

### 验证 Provider 配置

```python
from assets import list_available_providers, get_ai_provider

# 查看所有可用的 Provider
print("可用 Provider:", list_available_providers())

# 获取当前 Provider
provider = get_ai_provider()
print("当前使用:", provider.name if provider else "无")
```

## 三、Obsidian 配置

### 前提条件

- 已安装 Obsidian 客户端
- 已创建 Obsidian 知识库（Vault）

### 配置步骤

1. 打开 Obsidian，创建或打开一个知识库
2. 获取知识库路径：
   - 在 Obsidian 中打开设置（Settings）
   - 点击 "Vault" -> "Open folder"
   - 复制文件夹路径

3. 在 `.env` 文件中添加：
   ```env
   OBSIDIAN_VAULT_PATH=D:\Your\Obsidian\Vault\Path
   ```

### 效果

配置成功后，文档将自动同步到你的 Obsidian 知识库中。

## 四、飞书配置

### 前提条件

1. **安装飞书CLI**：
   ```bash
   npx @larksuite/cli@latest install
   ```

2. **完成应用配置**：
   ```bash
   lark-cli config init
   ```

### 配置步骤

1. 获取飞书知识库空间 ID：
   - 打开飞书知识库
   - 进入目标空间
   - 从 URL 中提取空间 ID

2. 在 `.env` 文件中添加：
   ```env
   FEISHU_WIKI_SPACE=7636965310725115074
   ```

### 效果

配置成功后，文档将自动同步到你的飞书知识库中。

## 五、输出规则

| 配置情况 | 输出目标 |
|----------|----------|
| 无任何配置 | 默认保存到 `notes/` 目录 |
| 仅配置 Obsidian | 输出到 Obsidian 知识库 |
| 仅配置飞书 | 输出到飞书知识库 |
| 两者都配置 | 同时输出到 Obsidian + 飞书 |

## 六、验证配置

运行以下代码验证配置是否正确：

```python
from assets import OutputManager

manager = OutputManager()
available = manager.get_available_outputs()
print(f"可用输出模块: {[o.name for o in available]}")
```

## 七、常见问题

### Q: 飞书CLI 安装失败？

**A:** 确保 Node.js 版本 >= 14.0.0，然后重新安装：
```bash
npm install -g @larksuite/cli
```

### Q: Obsidian 路径配置后无法输出？

**A:** 检查路径是否正确，确保路径存在且有写入权限。

### Q: 飞书文档创建失败？

**A:** 检查飞书CLI 是否已正确配置：
```bash
lark-cli auth login --status
```

### Q: AI Provider 无法使用？

**A:** 检查对应的 API Key 是否配置正确，或尝试设置 `AI_PROVIDER` 指定具体的 Provider：
```env
AI_PROVIDER=openai
OPENAI_API_KEY=sk-xxxx
```
