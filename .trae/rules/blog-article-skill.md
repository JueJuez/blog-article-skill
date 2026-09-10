---
alwaysApply: true
description: "blog-article-skill 项目规则入口；跨平台真源见 AGENTS.md，规则唯一来源见 RULES.md"
globs: ""
---

# blog-article-skill — Trae 项目规则（薄指针）

本项目的**跨平台真源入口**是仓库根目录的 [`AGENTS.md`](../../AGENTS.md)，**规则唯一来源**是 [`RULES.md`](../../RULES.md)。

**每次动手前，先读这两个文件**（尤其是 RULES.md 的红线：落盘端默认、复用入口不手写抓取、无字幕 ASR 兜底）。AGENTS.md 给了能力清单、入口函数、配置引导与「自举指针」。

## 三条红线（详见 RULES.md）
- 成品默认只落**本地 Obsidian**（2026-09-04 起）；飞书仅 `DISABLE_FEISHU_SYNC=0`（双写）或整库镜像 `audit_sync.py` 时写入，本地 `notes/` 仅两者都未配时兜底，禁止 AI 手写 `notes/`。
- 一律走入口函数，禁止手搓抓取 / 总结脚本：`articles.skill_main` / `videos/run.py --url` / `monitors/run.py`。
- YouTube 无 CC 字幕（`fetch_transcript` 返回 None 且页面已加载）→ 自动走 ASR 兜底转写（2026-08-06 授权），仅 ASR 也失败才回「无可用字幕」文案；不自行开发其它兜底。

## 两个入口
- **一次性总结**：文章给链接/原文 → `articles.skill_main`；视频给链接 → `videos/run.py --url`。
- **订阅监控（关注 B站UP / 公众号）**：改 `monitors/subscriptions.json` 后跑 `monitors/run.py --mode first --apply`；之后每日增量跑 `python monitors/run.py --mode auto --apply`（**用户说「跑一次 / 跑一下」即触发，不挂定时任务**）。

> ⚠️ 真规则只维护 `AGENTS.md` + `RULES.md` 一处；本文件不重复细节，改动请同步回这两个文件。
