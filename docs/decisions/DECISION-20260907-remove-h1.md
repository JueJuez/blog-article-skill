# DECISION-20260907-remove-h1

## 背景
用户拍板：AI 总结正文去掉 H1 与话题标签行——标题由文件名/飞书节点标题承担，标签由系统权威追加；双写路径曾出现「LLM 标签行 + formatter 系统标签行」重复。已确认飞书节点标题走 `feishu.py` 的 `title` 参数（`original_title` 优先、文件名 stem 兜底），从不取正文 H1，本改造对飞书主路径零影响。

## 决策
- `prompts/verifier.py`：门禁由「主标题恰好 1 个」翻转为「正文 0 个一级标题」，扫描前剥离代码围栏（`#` 注释不误伤）。
- `prompts/templates.py`：8 模板 + UNIVERSAL_RULES 全部改为「禁 H1 / 禁标签行」措辞，作者行升首条；key_points 删条后重编号 1..10；formatter 标签行剥离升级为围栏感知状态机（`_HASHTAG_LINE_RE`：`#` 后无空格、整行 token 均 `#xxx`，围栏内不处理）。
- `articles/main.py`：`_extract_title_from_summary` 删「作者行上方扫 H1」循环，仅留「核心定位：」兜底。
- `monitors/drain_pending.py`：删 `_extract_h1`，`choose_node_title(title)` 单参——node_title 纯由来源侧 title 决定。
- 全程 TDD：新增 TestNoH1/TestExtractTitleFromSummary/TestDrainPendingNoH1/双标签回归共 13 用例，修 8 处旧格式 fixture；全量 559 passed。

## 不做什么
- 存量笔记不重总结（用户手动修正）；旧队列条目输出 H1 时由门禁拦截、issues 引导子 Agent 自修复，闭环设计内。
- `shared/title_norm.py` 不动（`choose_node_title(source_title, summary_h1="")` 签名保留，旧笔记 H1 兜底兼容）。
