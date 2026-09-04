# DECISION-20260904-obsidian-default

> **取代 `DECISION-20260808-obsidian-optin.md`**（2026-09-04 起生效）

## 背景
2026-08-08 的规则是「默认只写飞书，Obsidian 按需开启」——理由是写两遍浪费。
到 2026-09-04，用户决定反过来：**默认只写本地 Obsidian，不写飞书**。
原因：用户改用本地 Obsidian 库作为主归档，放弃飞书同步。

## 决策
- **默认只写本地 Obsidian（`OBSIDIAN_VAULT_PATH`），不写飞书。**
- 实现方式（零代码改动，纯 `.env` 开关，可回退）：
  - `OBSIDIAN_WRITE=1` → `OutputManager` 把 Obsidian 当作默认落盘目标（`obsidian_requested` 恒真）。
  - `DISABLE_FEISHU_SYNC=1` → `_resolve()` 跳过飞书。
  - 两者叠加 = `OutputManager()` 默认仅解析到 `['obsidian']`。
- 各入口的 `obsidian=False` 默认值与 `--obsidian` 标志语义保持不变（仍表示「追加/确保 Obsidian」），无需改动散落各处的调用。

## 回退路径
- 恢复飞书默认：把 `DISABLE_FEISHU_SYNC` 改为 `0`；要双写则同时保留 `OBSIDIAN_WRITE=1`。
- 回到 2026-08-08 旧规则：设 `OBSIDIAN_WRITE=0` 且 `DISABLE_FEISHU_SYNC=0`。
- 两者皆关（`OBSIDIAN_WRITE=0` 且 `DISABLE_FEISHU_SYNC=1`）→ `OutputManager` 无可用外部目标 → 回退本地 `notes/`，不丢数据。

## 验证
`python -c "from articles.manager import OutputManager; print([o.name for o in OutputManager().get_available_outputs()])"`
输出应为 `['obsidian']`。

## 文档同步
- `RULES.md` §3.0 已重写为新规则。
- `references/config.md` §五 输出规则表已更新。
- `articles/manager.py` 文档字符串已更新。
- 项目工作记忆（`.workbuddy/memory/MEMORY.md`）「输出规则（硬红线）」已更新。
