# SKILL_DECISIONS — ZCode 技能决策记录（blog-article-skill 项目级加载点）

> **本文件是平台级台账，只描述「本加载点装了什么 / 弃了什么」。**
> 各平台各一份、互不共用、**不入 brain 中央库**（brain 只存 `_REGISTRY.md` 这类跨平台真源）。
> 用途：`skill_reconcile.py --global-zone <本加载点>` 的「决策记录缺口」判定依据。

```yaml
last_scan: 2026-09-19
platform: zcode
zone: <项目根>/.zcode/skills   # blog-article-skill 项目级加载点（非全局区）

baseline_2026-09-19:
  desc: "本加载点建点即装，无存量基线；此后装/弃照常逐条落 decisions"
  project_own: []

decisions:
  installed:
    - "content/topic-to-article（P）：2026-09-19 从 brain 中央库 skills/content/topic-to-article/ 复制安装（与 .trae/.workbuddy 项目级副本同源同内容）。它是本项目的编排器（读 prompts/ 的 9 个模板 + 调 scripts/persist_summary.py），本机值见技能目录 .env（不入库）。"
  declined: []

pending: []
```
