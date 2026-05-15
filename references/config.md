# 配置说明

## 一、配置文件

配置文件 `.env` 需要放在技能根目录下：

```env
# Obsidian 知识库路径（可选）
OBSIDIAN_VAULT_PATH=D:\你的Obsidian库路径

# 飞书知识库空间 ID（可选）
FEISHU_WIKI_SPACE=你的知识库空间ID

# 飞书知识库父节点 Token（可选）
FEISHU_WIKI_PARENT_NODE=父节点Token
```

## 二、Obsidian 配置

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

## 三、飞书配置

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

## 四、输出规则

| 配置情况 | 输出目标 |
|----------|----------|
| 无任何配置 | 默认保存到 `notes/` 目录 |
| 仅配置 Obsidian | 输出到 Obsidian 知识库 |
| 仅配置飞书 | 输出到飞书知识库 |
| 两者都配置 | 同时输出到 Obsidian + 飞书 |

## 五、验证配置

运行以下代码验证配置是否正确：

```python
from assets import OutputManager

manager = OutputManager()
available = manager.get_available_outputs()
print(f"可用输出模块: {[o.name for o in available]}")
```

## 六、常见问题

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