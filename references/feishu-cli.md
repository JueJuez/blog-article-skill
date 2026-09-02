# 飞书 Wiki CLI 速查（运维坑集中页）

> 配套 `RULES.md` §4.7。所有命令走 `lark-cli`（飞书 CLI），`--as user` 表示以已授权的 user 身份执行（安装与空间配置见 `README.md` 飞书段落）。

## 1. 查询空间与节点

```bash
lark-cli wiki +space-list                              # 空间列表（含空间ID / space-id）
lark-cli wiki +node-list --space-id <空间ID> \       # 节点列表（含父节点 Token）
        --parent-node-token <父节点Token> \
        --as user --page-all
```

⚠️ **坑⓪（2026-09-03）**：lark-cli ≥1.0.32 **移除了 `--json` 旗标**（输出默认就是 JSON），旧调用传 `--json` 会直接报 `Error: unknown flag: --json`——症状是文档能创建但查询/建容器节点全失败。等价写法是 `--format json`（默认值），一般无需显式传。

⚠️ **坑①**：`+node-list` 返回结构是 `data.nodes`，**不是** `data.items`。遍历：

```python
for item in listing.get("data", {}).get("nodes", []):
    if item.get("title") == "待归类":
        tok = item.get("node_token", "")
```

## 2. 创建容器节点（系列 / 收件箱）

飞书 wiki **没有独立 folder 类型**，容器即「一个有子节点的 docx 节点」。

```bash
lark-cli wiki +node-create \
    --title "系列名或待归类" \
    --node-type origin \
    --obj-type docx \          # ⚠️ 坑③：容器用 docx
    --parent-node-token <父节点Token> \
    --space-id <空间ID> \
    --as user
```

⚠️ **并发安全（2026-08-26 起）**：`_ensure_child_node` / `ensure_series_node` 的查-建已加**跨进程原子锁**（`_node_creation_lock`，原子 `O_EXCL` 锁文件）+ 建失败重查复用兜底。多会话并发新建同一容器节点（如两进程同时建 `【监控】/B站/土斯`）时，仅一个进程真正建出，另一进程建失败后重查到对方节点直接复用——**绝不重复建、不落空**。作者文件夹已存在时建步骤退化为只读复用，竞态本就关闭。详见 `docs/decisions/DECISION-20260826-feishu-node-creation-lock.md`。

## 3. 删除节点（清理测试 / 重生成前删旧总览）

```bash
lark-cli wiki +node-delete \
    --obj-type wiki \           # ⚠️ 坑②：删用 wiki，不是 docx
    --node-token <要删的Token> \
    --yes                        # ⚠️ 坑④：测完务必 --yes 清理
```

## 4. 系列课重生成总览（必看）

- ⚠️ **坑**：飞书 `save_series` 是「新建」非「更新」。重生成 `00_系列总览.md` 前**必须先删旧总览节点**，否则会建出第 2 个总览。
- 全集各集写完后，**务必调一次** `videos.main._generate_series_overview(series_title, series_dir, url)` 刷新总览，避免「（待总结）」标记过期。
- ✅ **并发安全（2026-08-26 起）**：
  - **集节点**：`save()` 是 upsert（父容器下已有同名标题先删旧再建新），天然幂等——多子 Agent / 多会话并发写**不同集**不会重复建节点。
  - **容器节点**（作者文件夹 / 系列容器）：查-建已加跨进程原子锁（见 §2），多会话并发落不同系列课完全安全，无需人工串行。
  - ⚠️ 仅剩运营层风险：多会话同时打 `lark-cli` 可能触发飞书 429 限流，个别集落盘瞬时失败记 FAIL、可重跑，非损坏。

## 5. 常见错误速查

| 现象 | 原因 | 修 |
|------|------|----|
| 遍历 node-list 拿到空 | 用了 `data.items` | 改用 `data.nodes` |
| `Error: unknown flag: --json` | lark-cli ≥1.0.32 移除了 `--json`（默认输出 JSON） | 删掉 `--json`，需要时用 `--format json` |
| 文档建成功但容器节点建失败/回读全挂 | 同上（旧代码带 `--json`，查询路径全灭） | 升级后同上；历史被错误兜底落错目录的文档用 `wiki +move` 归位 |
| 删节点报类型错 | 用了 `--obj-type docx` | 改用 `--obj-type wiki` |
| 重生成总览出现 2 个 | 没先删旧节点 | 先 `wiki +node-delete --obj-type wiki` 再生成 |
| 两个同名 `土斯` 文件夹 | 两进程同秒抢建作者文件夹（TOCTOU） | 已加原子锁根治：建失败重查复用，不再重复建 |
| 落盘个别集 FAIL | 飞书 429 限流 | 重跑 `apply_pending_series.py`（幂等） |
