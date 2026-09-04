# DECISION-20260808-obsidian-optin

> ⚠️ **已取代（2026-09-04）**：本决策「默认只写飞书、Obsidian 按需」已被 `DECISION-20260904-obsidian-default.md` 翻转——现**默认只写本地 Obsidian、不写飞书**（`.env` 设 `OBSIDIAN_WRITE=1` + `DISABLE_FEISHU_SYNC=1`）。

## 背景
用户原话：「写两遍有点浪费，默认写到飞书，如有需要写到 obsidian，我会提前和你说」。原规则是 Obsidian + 飞书强制双写（散落 RULES/AGENTS/SKILL/MEMORY/代码），用户认为重复写两遍不划算。

## 决策
- **默认只写飞书**，Obsidian 仅在用户提前明确要求（"写到 obsidian / 双写"）时才写。
- **代码门禁唯一真相**：`articles.manager.OutputManager` 是所有落盘闸门。`OutputManager()` 默认不含 Obsidian；开启方式：`obsidian=True` 构造参数 / 任意入口传 `obsidian=True` / `.env` 设 `OBSIDIAN_WRITE=1`（回退双写的逃生舱，默认关）。
- **不靠 AI 记性**：默认行为由代码决定，没显式开启就不写 Obsidian；飞书不可用且未请求 Obsidian 时回退本地 `notes/`。
- **各入口已打通**：文章 `articles/run.py --obsidian` / `skill_main({"obsidian":True})`；视频 `videos/run.py --obsidian` / `summarize_video({"obsidian":True})`；监控发现段 + drain 段（apply_pending / _save_pending_item / save_series_batch / persist_summary）均带 `--obsidian`（监控**两处都要带**）。
- **文档同步**：RULES.md §3.0（整节重写+自检清单）、AGENTS.md、references/config.md §五、README.md、monitors/README.md 全部改为新默认；回归测试 `tests/test_obsidian_optin.py`（6 例）已建。

## 不做什么
- 不保留「双写契约（强制）」旧规则；不默认写 Obsidian。
- 不改 `save_to` 单端指定能力、本地兜底、DISABLE_FEISHU_SYNC 兼容。
- 单写飞书时无需跑 `audit_sync`（Obsidian 为空是预期，跑会报噪声）。
