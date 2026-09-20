# DECISION-20260920-script-naming-and-folder-field

## 背景
2026-09-20 两起静默事故：① `scripts/_run_with_env.py` 被 `.gitignore:53`（`scripts/_*`）忽略、从未入库，却是两个
DETACHED 长任务壳的必需环节 ⇒ 克隆/换机后入口 rc=1 秒退；② 重做清单的 `folder` 字段被填成 registry 的 filename
（含 `.md`），消费方当目录用 ⇒ 路径推导失败 + 标题为空时 `generate_filename` 静默兜底，实测落成 8 篇「未命名笔记」。

## 决策
- 决策 1：脚本文件名**禁用 `_` 前缀**；新脚本入库前必须先 `git check-ignore -v <路径>` 验通。
- 决策 2：「加载 `.env` 后转发子命令」下沉为 `shared.env.run_with_env(cmd)`，CLI 壳 `scripts/run_with_env.py`（入库）；
  旧 `_run_with_env.py` 仅保留为带弃用提示的转发壳。
- 决策 3：清单里的 `folder` 字段 = **目录**，取值统一 `os.path.dirname(filename)`（相对库根）；
  **禁止**消费 registry 的 `folder` 字段（相对/绝对混用，会让 zone/author 的 split 取到盘符 `D:`）。
- 决策 4：`generate_filename` 标题为空时保留兜底命名，但必须打 stderr 告警（返回值不变，兼容 `tests/test_patch_trio.py`）。

## 不做什么
- 不改 `generate_filename` 的返回值或改为抛异常（会破坏既有落盘路径与测试）。
- 不批量重命名历史 `_*.py`（弃用壳保留即可，避免无谓 churn）。
- 不去统一 registry `folder` 字段的语义（存量相对/绝对混用，改造成本高、收益低——只要不再消费它）。
