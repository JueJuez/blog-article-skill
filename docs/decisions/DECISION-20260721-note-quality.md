# DECISION-20260721-note-quality

## 背景
用户要求笔记总结「质量高、上下文清晰」，并建议在总结中引入第一性原理等思维模型提质。当前缺口：① 教学视频被误判为口播要点（KEY_POINTS 含"视频"且最先匹配）；② 系列课总览只有目录、无学习路径；③ 缺思维模型透镜。

## 决策
- 思维模型透镜：在 `UNIVERSAL_RULES` 新增「第九节 思维模型透镜（有序 LIST · 按需触发）」，`structured` 同步内联等价规则；6 模型按序（第一性原理→5-Why冰山→二阶思维→脉络还原→奥卡姆剃刀→类比迁移），每条适用才产出「不同的点」，不适用跳过，**不新增固定章节、不硬凑**（与去水分红线兼容）。
- 分类修复：`classify_note_type` 在 key_points 循环前先查「教学超信号」（手把手/保姆/实操/从零/教程/课程/step by step），命中即 `structured`，解决教学视频误判；演讲/访谈类视频仍走 `key_points`（不动 `KEY_POINTS_KEYWORDS`）。
- 系列课地图：把 `_generate_series_overview` 的渲染抽成纯函数 `_render_series_overview(series_title, url, rows, learning_path_md)`，新增「## 学习路径」段（由 `_ai_summarize` 基于各集标题+一句话结论生成「建议顺序+先修说明」），保留原「## 各集导航」表。
- 新模板（访谈/盘点/读书）：本期不做，留待后续（nice-to-have）。

## 不做什么
- 不新增固定「思维模型透视」章节（避免凑数，违去水分红线）。
- 不为新闻/快讯加模板。
- 不改双写/存储契约与 `note_type` 枚举。
