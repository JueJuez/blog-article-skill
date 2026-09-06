# DECISION-20260906 · 飞书杂物行与 frontmatter 处理延后至下会话

> 拍板：用户 2026-09-06 第 7 条原话「飞书杂物行和frontmatter 留到下个会话再开始先记录」。
> 关联：`docs/plans/PLAN-20260906-sync-ledger.md` §2 默认决策 3（YAML 存量本地不动、飞书写入端 strip）与 §3-D（frontmatter 停产）。

## 背景（待下会话现场复核）

- 飞书 wiki 存量页面（镜像端）头部残留同步写入产生的杂物行（疑为 YAML frontmatter 文本块——飞书不渲染 frontmatter，`---` 包裹的 YAML 直接显示为文字行；具体分布与数量需下会话实地清点确认，本记录不预填数字）。
- 本地 Obsidian 侧 frontmatter 按 V1 默认决策 3「本地不动」；写入端停产 + strip 属 V2 极简方案范围。

## 下会话待办

1. 飞书侧杂物行清点：扫描镜像端页面头部，确认杂物行构成（YAML 块 / 同步标记 / 空行）与数量。
2. 清理方案：确认 V2「Obsidian 唯一真源 + 飞书无状态镜像」下，杂物行应从真源重推覆盖还是飞书侧直接删除。
3. frontmatter 停产 + `_strip_frontmatter()` 实施（对齐 PLAN §3-D 与 P2-4 防误切约束），随 V2 重写 PLAN 一并 TDD 落地。

## 本会话（2026-09-06 round3）已完成，与此延后项无关

- 存量清理 round3 --apply 全绿：删 25（同URL 19 + 小猪仔 6）| 改名 36（H1 转正 15 + 日期前缀 21）；undo 61 行、备份 61、verify_round3.py 全绿（复扫 0 动作、变体残留 0）。
- scys 20 件「无日期」系上轮抓取脚本未带登录态所致（**非平台限制**，原结论作废）；待 CDP 登录态重抓补日期前缀，见 DECISION-20260907 已拍板 4。
