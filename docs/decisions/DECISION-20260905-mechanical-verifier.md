# DECISION-20260905-mechanical-verifier

## 背景
模板质量评估（建议 4+5，用户已批）：`save_summary_only` 主路径（FORCE_AGENT_MODE=1 三队列/手贴兜底全走此入口）落盘前无任何校验，历史上偶发缺/多主标题、正文写来源链接（formatter 权威追加后出现两个链接）、篇幅严重偏离模板区间；AI 审核员（`NOTE_QUALITY_GATE`）默认关且无外部 AI 返回 None，兜不住。

## 决策
- 新建 `prompts/verifier.py`：`verify_note_mechanical`（零 AI 依赖、不受开关控制）——主标题恰好 1 个；正文禁来源链接行 / 禁出现原文 URL；字数对照 `NOTE_WORD_LIMITS`（8 模板 + general，与模板「单篇正文 X～Y 字」声明由测试防漂移）硬阈值 <min×0.6 / >max×1.5 拦截，轻微越界仅告警。
- 接入 `save_summary_only`：**dedup 闸门之后**（已总结出队优先，保 ALREADY_EXISTS 语义）、**folder 自动路由之前**（违规不触发路由副作用）；拦截返回 `VERIFIER_FAILED:<issues>` 不落盘不 dedup，子 Agent 按 issues 修复重试；`force` 只豁免 dedup 不豁免质量底线。
- 建议 5：`QUALITY_GATE_SELFCHECK` 两条 → 三条（+篇幅区间）；`QUALITY_GATE_PROMPT` 评分维度 6 → 7（+篇幅达标）。

## 不做什么
- 不接 `save_summarized_article`（AI stub 测试路径与真实 AI 路径已由 `verify_note` 覆盖）。
- 不重复查标签行/作者（已由 `normalize_note_metadata` 机械归一，避免误报）。
