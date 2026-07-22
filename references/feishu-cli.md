# 飞书 Wiki CLI 速查（运维坑集中页）

> 配套 `RULES.md` §4.7。所有命令走 `lark-cli`（飞书 CLI），`--as user` 表示以已授权的 user 身份执行（安装与空间配置见 `README.md` 飞书段落）。

## 1. 查询空间与节点

```bash
lark-cli wiki +space-list                              # 空间列表（含空间ID / space-id）
lark-cli wiki +node-list --space-id <空间ID> \       # 节点列表（含父节点 Token）
        --parent-node-token <父节点Token> \
        --as user --json --page-all
```

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
    --as user --json
```

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
- ⚠️ **并发坑**：多子 Agent 同时 `save_series` 写飞书 → 集级无查重建重复节点。安全模式：子 Agent 只返文本＋元数据，编排方**串行**调保存入口落盘。

## 5. 常见错误速查

| 现象 | 原因 | 修 |
|------|------|----|
| 遍历 node-list 拿到空 | 用了 `data.items` | 改用 `data.nodes` |
| 删节点报类型错 | 用了 `--obj-type docx` | 改用 `--obj-type wiki` |
| 重生成总览出现 2 个 | 没先删旧节点 | 先 `wiki +node-delete --obj-type wiki` 再生成 |
| 同集出现重复节点 | 子 Agent 并发写 | 改串行落盘 |
