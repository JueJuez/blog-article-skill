# PLAN-20260827 · 总结 Prompt 模板体系优化方案

> **本文档自包含**：新会话不依赖历史对话即可执行。产生于 2026-08-27 对 8 模板体系的全链审计
> （模板目的 → 要求 → prompt 达成度逐个核对 + 系统维度审查）。执行前先读 `RULES.md` §4.6
> 「质量保障三件套」（模板公共机制现状）与 `docs/decisions/DECISION-20260821-scys-classification-fix.md`
> （词表纪律的历史教训）。模板清单与分类逻辑的真源是 `prompts/templates.py`（`NOTE_TEMPLATES`）+
> `prompts/classify.py`（分类优先级）。
>
> ⚠️ 本文件曾引用 `RULES.md` §3B，该章节并不存在（RULES 只有 §3.0/3.1/3.2/3.3），2026-09-03 已修正指向 §4.6。

---

## 0. 背景总览

### 0.1 体系现状（改动的对象）

- **模板注册表**：`prompts/templates.py` 的 `NOTE_TEMPLATES`（8 种：structured / key_points /
  case / opinion / interview / roundup / reading / dissection）。新增类型只改这一处。
- **分类器**：`prompts/classify.py: classify_note_type(title, content)`——机械子串匹配，
  9 级优先链命中即返回：教学超信号(structured) → interview → key_points → roundup →
  reading → opinion → dissection → case → structured 兜底。
- **选择权归属**：执行模型**不选模板**。`articles/main.py:580-585`、`videos/main.py:725`
  把「分类器选好的 prompt + QUALITY_GATE_SELFCHECK」直接塞进返回 dict，模型照写。
- **质量机制**：三层——模板内红线（UNIVERSAL_RULES）→ 输出前自检（QUALITY_GATE_SELFCHECK，
  当前默认）→ 外部 AI 审核环（`NOTE_QUALITY_GATE=1` 才开，当前关）。

### 0.2 审计结论摘要（问题全景）

| 编号 | 问题 | 严重度 | 一句话 |
|---|---|---|---|
| P0-1 | dissection 触发面宽于适用面 | 高 | 领域词（带货/私域）无动作词共现即触发，方法论文被要求填创作模具表 |
| P0-2 | 字数与原文体量脱钩 | 高 | 500 字短文被判 structured 要写 1500~3000 字，与禁凑数红线正面冲突 |
| P0-3 | 忠实 vs 重组无裁决规则 | 高 | 「一条不准删」与「必须给判断」两组指令冲突时无优先级 |
| P1-4 | structured 双重身份 | 中高 | 既是教程特化模板又是全链兜底，非教程内容落进去退化为通用总结+空壳章节 |
| P1-5 | 词表残留松动 | 中 | 「榜」单字、「读完」等泛匹配误触发 |
| P1-6 | get_note_prompt.py 脚本不存在 | 中 | AGENTS.md/scys-SOP 指示子 Agent 跑的 CLI 是空头支票，文档-代码漂移 |
| P2-7 | 分类错配静默劣化 | 中 | 无模板适配度自评，错配无从发现（只能靠人眼） |
| P2-8 | 标签自由生成无受控词表 | 中 | 知识库检索价值随规模衰减（只有读时优化，没有查时优化） |
| P2-9 | 无事后质量回流 | 中 | 全是输出前自评，无「读后差评→归因→修词表」闭环 |
| P2-10 | 等价规则双份维护无锁定 | 低 | structured 内联段 vs UNIVERSAL_RULES 漂移不报错 |

---

## 1. P0 批（第一批执行，预计 1.5 小时）

### P0-1 · dissection 触发收紧（领域词×动作词共现）

**背景**：2026-08-26 新增第 8 类 dissection（创作解剖，移植自 ppt-master 的
note_dissection_sop，模具字段=标题公式/钩子/节奏/CTA）。`classify.py` 的
`DISSECTION_KEYWORDS`（~L58）混入了两类词：
- **复合强词**（领域+动作二合一）：爆款拆解 / 爆款笔记 / 爆款视频 / 爆款文案 / 爆款标题
- **纯领域词**：带货 / 涨粉 / 起号 / 账号运营 / 私域 / 内容创作 / 小红书运营 / 抖音运营 / 公众号运营 / 自媒体运营

纯领域词**命中即触发**导致错配：《私域运营方法论：怎么搭建 SOP》因「私域」判 dissection，
但它是方法论教程（适用 structured），模板却要求填「标题公式/开头钩子/结尾 CTA」模具表 →
空壳表格，违反模板自己的去水分红线。**领域词 ≠ 拆解/复盘意图**，分类器只看词存在性、
不看词组合。当年写测试时只测了「带货复盘→dissection（对）」，漏测「带货方法论→应
structured（错配）」。

**方案**（`prompts/classify.py`）：
1. 拆两组：`DISSECTION_DOMAIN`（上述纯领域词 + 涨粉/起号）与
   `DISSECTION_ACTION`（拆解 / 复盘 / 爆款 / 全过程 / 实录 / 从0 / 0到 / 怎么做到 / 我是怎么 / 起号 / 起盘）。
2. 触发条件改为：**复合强词直接触发**（保留现词表前 5 个）；或 **(任一 DOMAIN) AND (任一 ACTION)**。
3. 词表纪律不变：禁泛化词（引流/变现/粉丝/选题不收，见 DECISION-20260821 教训）。

**验收测试**（先写 RED，加进 `tests/test_dissection_template.py`）：
```python
def test_domain_word_without_action_not_dissection():
    # 领域词命中但无动作词 → 不触发 dissection（方法论走 structured）
    assert classify_note_type("私域运营方法论：怎么搭建SOP", "系统讲私域 SOP 搭建。") == "structured"

def test_domain_plus_action_is_dissection():
    assert classify_note_type("私域起号复盘：30天从0到1", "记录起号全过程。") == "dissection"
```
既有 21 个用例 + `test_scys_classification.py` 全部不回归。

### P0-2 · 字数比例制（长度随原文信息量伸缩）

**背景**：8 个模板各自写死绝对字数（500~3000 字不等）。原文可能 500 字也可能 30 万字：
短文被判 structured（软上限 1500~3000）时与「禁止凑数」红线正面冲突，执行模型只能违反
其一；长内容又一律封顶 1500~2000，硬压堆砌。模板是静态文本拿不到原文长度，**只能写
自适应规则让模型自行裁量**——这是已知限制，方案按此设计。

**方案**（`prompts/templates.py`，各模板「使用说明」段统一改写）：
把「单篇正文 X～Y 字」统一替换为语义：
> 「正文长度以**原文信息量**为锚：完整承载核心信息即止，**宁短勿凑**、禁止为凑字数注水；
> 软上限 Y 字（原文更短则更短）。」

structured 的「十一、字数与拆分」段（~L153）追加一句：
> 「原文体量不足目标区间时以信息密度优先，按需缩短，禁止注水膨胀。」

**验收**：字符串断言「宁短勿凑」出现在全部 8 个模板 prompt 中；「注水」出现在 structured。

### P0-3 · 忠实 vs 重组冲突裁决声明

**背景**：UNIVERSAL_RULES 同时要求「核心数据、案例、公式、金句、结论一条不准删」（第六节
忠实）与「总结必须提炼、重组、给出判断」「禁止目录式复述」（第八节重组）。张力真实：
小信息量原文按重组要求被迫生成「判断」时，风险滑向编造——与防编造红线冲突，但**冲突时听
谁的没写**。一行 prompt 即可修。

**方案**：UNIVERSAL_RULES 第六节「内容边界」末尾追加：
> 「**冲突裁决**：当『忠实保数据』与『给判断/重组』不可两全时，**忠实优先**——砍判断、
> 保事实；宁可笔记朴素，不可为深度脑补。」

**验收**：断言「冲突裁决」「忠实优先」出现在 UNIVERSAL_RULES，且出现在轻模板拼接后文本
（`test_dissection_template.py::test_dissection_prompt_universal_rules_merged` 同款断言方式）。

---

## 2. P1 批（第二批执行，预计 0.5 天，按 P1-5 → P1-6 → P1-4 顺序）

### P1-5 · 词表残留松动清理

**背景**：历史遗留的泛匹配：
- `ROUNDUP_KEYWORDS` 含单字「榜」——《打榜攻略》误入 roundup。
- `READING_KEYWORDS` 含「读完」——《读完这篇论文我总结了3点》误入 reading。

**方案**（`prompts/classify.py`）：
- 「榜」→ 收窄为词组：「榜单 / 上榜 / 红榜 / 黑榜 / 霸榜 / 排行榜」（「榜」「排行」已有）。
- 「读完」→ 改「读完这本书」或直接删除（「读书/书评/拆书」已覆盖真场景）。
- 动手前先跑分布验证：`rg -c "读完|打榜" notes/_scraped/scys/*.md` 看实际命中量，若某词
  命中大量真场景则保留词组形式而非删除。

**验收**：《打榜攻略》非 roundup；《读完这篇论文》非 reading；既有点击/盘点/读书正例不回归。

### P1-6 · 修文档漂移（删 CLI 承诺，改为消费队列已算 prompt）

**背景**：`AGENTS.md`（能力 2 第 4 步）、`references/scys-fetch-sop.md`（L161/L169）、
`docs/decisions/DECISION-20260821-scys-classification-fix.md`（L16）多处指示子 Agent 执行
`python get_note_prompt.py <raw> <title>`。**该脚本从不存在**（git 历史证实：字符串仅作为
文档承诺出现在 2026-08-21/22 两次提交，仓库只有 `prompts/templates.py: get_note_prompt()` 函数）。
但真相是：子 Agent 根本不需要自调 CLI —— `articles/main.py:585` 已按分类器选定模板 +
`QUALITY_GATE_SELFCHECK` 把 prompt **算好塞进返回 dict，随 `pending_summaries.json` 队列 in-band
投递**；子 Agent 读队列条目直接 `save_summary_only` 落盘即可。CLI 是「承诺即过时」的文档意图，被
队列内联投递架构取代，因此无调用方、无失败触发，成幽灵。

**方案**：不补脚本，改修文档漂移 —— 将上述三处「子 Agent 先调 `get_note_prompt.py`」改为
「子 Agent 消费队列中已算好的 prompt（无需自调 CLI）」。若未来确有子 Agent 需独立取 prompt，
让其 `from prompts.templates import get_note_prompt`（同 `main.py` 路径）即可，不造新脚本。

**改动清单**：
- `AGENTS.md:47` 能力 2 第 4 步 scys 子项：删 `get_note_prompt.py` 引用。
- `references/scys-fetch-sop.md:161` / `:169`：删 `get_note_prompt.py` 引用。
- `docs/decisions/DECISION-20260821-scys-classification-fix.md:16`：删 `get_note_prompt.py` 引用。

**验收**：仓库内 `get_note_prompt.py`（作脚本名）零引用；`grep -rn 'get_note_prompt\.py' --include=*.md`
输出为空（仅保留 `get_note_prompt()` 函数本身的合法引用）。

### P1-4 · 拆「兜底 structured」与「教程 structured」（本批最大改动，放最后）

**背景**：structured 身份冲突——它是教程特化模板（概念定义/分维度拆解/步骤/工具表/正反例），
**同时**是全链兜底（9 级链最后一环 return "structured"）。新闻、人物故事、数据报告、情绪
随笔全落它，顶着教程模子总结，靠「按需不凑」红线兜住后产物退化为「通用总结 + 大半空壳
章节」。**兜底要求最通用，模板却最特化**。

**方案**：
1. `NOTE_TEMPLATES` 新增第 9 类 `general`（通用兜底，极简骨架）：
   标题/标签/作者 → 一句话核心（引用块）→ 分层核心内容（按原文主线分块提炼，每块
   结论小标题+背景因果+证据）→ 金句摘录（如有）→ 延伸思考+我的想法留白 → 加粗收束。
   拼接 UNIVERSAL_RULES（同轻模板路径）。字数按 P0-2 比例制措辞。
2. `classify_note_type` 兜底分支 `return "structured"` → `return "general"`（教程命中
   TUTORIAL_SUPER_SIGNALS / STRUCTURED_KEYWORDS 时仍返回 structured，不受影响）。
3. `get_note_prompt()` 回退默认与显式传 "structured" 的调用方**不变**（回退语义：
   未知类型给最完整模板，structured 合理）。

**兼容性清单（动手前逐项核对）**：
- 全量测试跑一遍，改 `== "structured"` 的兜底断言为 `== "general"`（已知至少
  `test_scys_classification.py` 2 处、`test_dissection_template.py` 2 处）；
  `test_templates.py` 的 fallback 断言是 `get_note_prompt` 层面，不受影响，需复核。
- `RULES.md` L113 模板清单 8→9 种 + 优先链末尾「structured 兜底」改「general 兜底」；
  `AGENTS.md` 能力 1 模板列表；`references/scys-fetch-sop.md` L122「八种模板」改九种。
- 新决策记录 `docs/decisions/DECISION-YYYYMMDD-general-fallback.md`（≤15 行）。
- **存量不迁移**：飞书已有 structured 笔记不动，仅影响新增分类分布。
- 通知面：分类分布会变（原兜底流从 structured 转向 general），无需任何数据迁移。

**验收**：`classify_note_type("某公司完成B轮融资", "新闻正文")` → "general"；教程/干货正例仍
structured；全量测试通过（已知存量失败除外，见 §5）。

---

## 3. P2 批（按需执行，每项 0.5~1 小时~1 天不等，按价值自选）

### P2-7 · 模板适配度自评（错配降级留痕）

**背景**：分类器是子串匹配，模板持续特化后错配代价从「不够贴」升级为「明显错位」
（dissection 是第一个强特化模板）。与其继续堆关键词，不如让执行模型在写笔记前自评一次。

**方案**：`QUALITY_GATE_SELFCHECK`（templates.py ~L842）追加第⑦条：
> 「⑦ **模板适配自评**：判断本模板结构与该内容是否明显错配（如方法论文被要求填创作
> 模具表）；错配则降级用 structured 骨架完成笔记，并在笔记头部标注
> `> 模板适配：低——原因：xxx（已降级 structured 骨架）`。」

**价值**：错配从「静默劣化」变「留痕可统计」——抽检时 grep `模板适配：低` 即得错配清单，
直接反哺词表修正（P0-1 类问题的发现回路）。

### P2-8 · 受控标签词表（查时优化）

**背景**：每篇标签自由生成，无受控词表 → 规模一大必然碎片化，Obsidian/飞书检索价值随
笔记数衰减。所有模板都在优化第一遍阅读（读时），没有优化三个月后的检索（查时）。

**方案**：
- 新建 `shared/tag_taxonomy.py`：按订阅域分主题组维护受控词表（AI/编程/出海/内容创作/
  商业案例/工具测评/读书…，每组 5~15 词，**参数化**：词表放 JSON 或模块常量，扩组不改
  调用代码——用户偏好参数化配置）。
- `format_note_with_prompt`（templates.py）注入标签规则：「优先从受控词表选 1~3 个 +
  允许 ≤2 个自由标签」。
- 存量不迁移，增量生效。

### P2-9 · 质量抽检闭环（复用已有 audit 脚本，勿重造轮子）

**背景**：三层质量机制全是「输出前」模型自评，没有「用户读后觉得差 → 归因 → 修词表/模板」
回流。2026-08-22 的 309 篇 boilerplate 误判事故就是靠人眼发现的。

**现状**：`scripts/audit_fidelity.py` 已有 `--stage mechanical/fidelity`（双链接/双作者/坏
标题/域名错配 + 5 维 fidelity 对照，飞书只读，产物 `notes/_audit/fidelity_audit.md`）；
`scripts/audit_content_fidelity.py` 做总结↔本地真值配对。**缺的是「分类正确性」维度**。

**方案**：`audit_fidelity.py` 加 `--stage classify`：抽 N 篇（飞书笔记的模板痕迹 → 取原
raw/URL → 重跑 `classify_note_type` 对照现分类）→ 产出错配报告。配合 P2-7 的
`模板适配：低` 留痕 grep。触发方式：用户说「抽检总结质量」即手动跑（遵循 §3C 精神，
不挂自动调度）。

### P2-10 · 等价性锁定测试

**背景**：structured 内联「等价规则」（第十四/十五节）、轻模板拼 UNIVERSAL_RULES（第九/
十节），注释自称等价但**没有测试锁定**，改一处忘另一处不会报错（漂移风险）。

**方案**：`test_templates.py` 加锚点测试：对 UNIVERSAL_RULES 的关键红线句（质量自检六条
关键词：标题级空话/目录式复述/万能概括句/凑数/脱离原文/一本正经胡说；可信度标注
「笔记者推断」；内容边界「一条不准删」）逐条断言**同时出现**在 structured prompt 与
UNIVERSAL_RULES 中。不能逐字断言（structured 是改写非复制），取关键词锚点。

---

## 4. 执行顺序与依赖

```
第一批（P0，~1.5h）：P0-1 → P0-2 → P0-3     ← 相互独立，纯 prompt/classify 改动
第二批（P1，~0.5天）：P1-5 → P1-6 → P1-4     ← P1-4 动面最大，放批末；P1-4 完成后 P2 全部受益
第三批（P2，自选）：  P2-7 → P2-10 → P2-8 → P2-9
                     P2-7 的留痕是 P2-9 抽检的数据源，先 7 后 9
```

每项独立可交付，可拆会话执行；P1-4 若时间紧可单独延后，不阻塞 P0 收益。

## 5. 新会话执行指引（冷启动照做）

1. **先读**：`RULES.md`（§3B 公共机制段）+ 本文档对应小节 +
   `docs/decisions/DECISION-20260821-scys-classification-fix.md`（词表纪律教训）。
2. **TDD 红线**：每项优化先写失败测试（参考 `tests/test_dissection_template.py` 的结构：
   RED 注释 + 纯字符串断言 + 护栏用例），跑一次确认失败，再实现，再跑绿。
3. **每项完成后跑**：
   ```
   python -m pytest tests/test_templates.py tests/test_dissection_template.py \
       tests/test_scys_classification.py tests/test_note_quality.py -q
   ```
4. **已知存量失败（与模板改动无关，勿修勿跳）**：`test_prd.py` / `test_sub_monitor.py`
   5 个失败（monitors 在途改动）；`test_asr_fallback.py` 3 个（本机缺 ctranslate2）。
   判断标准：改动前后失败集合一致即零回归（可用 git stash 对照验证，参考 2026-08-26 的做法）。
5. **文档同步（每批收尾必做）**：`RULES.md` L113 模板清单段、`AGENTS.md` 能力 1 模板列表、
   `references/scys-fetch-sop.md` §7 模板数、`docs/decisions/` 新增决策记录（≤15 行/篇）。
6. **项目红线**：总结一律走入口函数（`skill_main` / `summarize_video` / `save_summary_only`），
   不手搓抓取/总结脚本（P1-6 是补文档承诺的正规入口，属例外且必须做端到端测试）；
   默认只落飞书；Python 规范见 `.trae/rules/`（类型注解、snake_case、行宽 120、双引号）。

## 附录 · 快速索引

| 改动对象 | 文件 | 关键符号 |
|---|---|---|
| 模板本体 | `prompts/templates.py` | `NOTE_TEMPLATES` / `DISSECTION_PROMPT` / `UNIVERSAL_RULES` / `QUALITY_GATE_SELFCHECK` |
| 分类器 | `prompts/classify.py` | `DISSECTION_KEYWORDS` / `ROUNDUP_KEYWORDS` / `READING_KEYWORDS` / `classify_note_type` |
| prompt 注入点 | `articles/main.py` L580-585 | `get_note_prompt(note_type) + QUALITY_GATE_SELFCHECK` |
| prompt 注入点 | `videos/main.py` L725 | 同上 |
| 测试 | `tests/test_templates.py` / `test_dissection_template.py` / `test_scys_classification.py` | 字符串断言惯例 |
| 质量配置 | `references/config.md` L214-229 | `NOTE_QUALITY_GATE` / `NOTE_GATE_THRESHOLD` |
| 已有审计 | `scripts/audit_fidelity.py` | `--stage mechanical/fidelity`（P2-9 复用） |

---

# 附录 B · 分类器架构设计（路线 B 预研 · 先设计后改）

> 缘起：评审时用户将 P0-1 从「词表互斥收紧」升级为「多面性 + 加权倾向裁决」，本质是对 9 级优先链的局部架构改动；C 点（整体架构升级为加权 top-k + 规则裁决）用户要求「先设计后改」。本附录即 C 的设计预研，也是 P0-1 的实现蓝图。开发前必读，开发时严格按 §B.5 分阶段。
> ⚠️ **本附录（路线 B 加权引擎）已于 2026-08-27 搁置 SHELVED**：经评审属 YAGNI（本项目无多面性路由消费者）。实际落地为最小版——dissection 共现护栏 + `general` 兜底（见 `prompts/classify.py`）。原独立设计文档 `DESIGN-20260827-classifier-rewrite.md` 已归档 `_archive/decisions/`，**勿照此实现**；多面性消费者出现前勿 revive 加权引擎。

## B.1 现状审计（为什么 9 级链不够）
当前 `classify_note_type`（prompts/classify.py L121-175）是 **11 步命中即返回 + default structured**：
1. TUTORIAL_SUPER_SIGNALS → structured
2. INTERVIEW_KEYWORDS → interview
3. `_content_looks_interview` → interview
4. KEY_POINTS_KEYWORDS → key_points
5. ROUNDUP_KEYWORDS → roundup
6. 《》书名号 → reading
7. READING_KEYWORDS → reading
8. OPINION_KEYWORDS → opinion（已剔「评论区」）
9. DISSECTION_KEYWORDS → dissection
10. CASE_KEYWORDS → case
11. STRUCTURED_KEYWORDS → structured
12. default → structured

结构性问题：
- **顺序即命运**：dissection 在 step 9、structured 在 step 11 → 「私域运营方法论」被 step 9 的「私域」抢成 dissection（错配，P0-1 根因；按用户决策方法论应走 structured）。
- **dissection 纯领域词无动作搭配即触发**：DISSECTION_KEYWORDS 全是领域词（带货/私域/起号/账号运营/内容创作/小红书运营…），只要出现任一即 dissection，无「拆解/复盘/打法」动作搭配 → 高误判。
- **赢家通吃**：命中即返回，无法表达多面性（同一篇能吃出教程味也能吃出复盘味，只能取第一个）。
- **加类=堆词+护栏**：每加一类靠补词+回归测试，边际成本递增。

## B.2 优化边界（哪些硬短路、哪些加权）
**保留硬短路（强信号、几乎零错配、格式级区分，不进加权池）**：
- TUTORIAL_SUPER_SIGNALS（手把手/保姆/实操/从零/教程/课程/step by step）→ structured
- INTERVIEW 标题关键词 + 内容级探针 → interview
- 《》书名号（标题强信号）→ reading

**进入加权池（多面性、需倾向裁决）**：
key_points / roundup / reading(关键词) / opinion / dissection / case / structured / general(新增)

## B.3 业务流程（分类器被谁调、产出怎么用）
- **调用点**：`articles/main.py:339` 分类；`:580` 降级回退；`videos/main.py`（3 处 L80/489/725）。
- **当前接口**：`classify_note_type(title, content) -> str`（单类型）。
- **加权后接口（向后兼容）**：
  - `classify_note_type(title, content) -> str` **签名不变**，内部返回 top-1，所有调用方零改。
  - 新增 `classify_note_type_scored(title, content) -> ScoredResult`（dataclass：top1, top2, gap, signals:list[(type, weight, matched_kw)]），供 P2-7 自评留痕 / 调试 / 未来路由。
- **与 prompt 衔接**：`articles/main.py:585` 已 `get_note_prompt(note_type)`；P0-2 改签名为 `get_note_prompt(note_type, length=None)`（length 由 `len(content)` 映射注入，机械封顶）。

## B.4 加权算法（初版权重表 · 待 TDD 校准）
**信号权重（命中单词计权，可累加）**：
| 组 | 单词权重 | 说明 |
|---|---|---|
| TUTORIAL_SUPER_SIGNALS | +100（硬短路，不进池） | 极强教程信号 |
| INTERVIEW 标题 kw | +100（硬短路） | |
| INTERVIEW 内容探针 | +90（硬短路） | |
| 《》书名号 | +100（硬短路） | |
| KEY_POINTS | +28 / 词 | 口播要点 |
| ROUNDUP | +28 / 词（P1-5 清「榜」残留） | 盘点横评 |
| READING kw | +28 / 词 | 读书书摘 |
| OPINION | +24 / 词（已剔评论区） | 观点立场 |
| CASE | +30 / 词 | 案例复盘 |
| STRUCTURED kw | +34 / 词 | 教程方法论兜底 |
| general | 基础分 +10（兜底，低于任何实命中即不赢） | 极简骨架 |
| **DISSECTION** | 见下方共现公式 | 内容创作域拆解 |

**dissection 共现公式（P0-1 核心）**：
```
dom = 命中 DISSECTION_DOMAIN 词数（带货/私域/起号/账号运营/内容创作/小红书运营/抖音运营/公众号运营/自媒体运营）
act = 命中 DISSECTION_ACTION 词数（拆解/复盘/打法/公式/钩子/节奏/CTA/选题/爆款/起号逻辑/运营sop）
dissection_score = dom*W_DOM(12) + act*W_ACT(30) + (dom>0 and act>0 ? COOCCUR_BONUS(40) : 0)
# 护栏：dom>0 但 act==0（纯领域词无动作，如「私域运营方法论」）→ dissection_score 仅 12，低于 STRUCTURED 的「方法论」+34 → 不赢，落到 structured（符合用户决策）
# dom>0 且 act>0（如「带货复盘」「爆款拆解起号」）→ +82 起，稳赢
```
> 注：当前 `DISSECTION_KEYWORDS` 的纯领域词（带货/私域/起号…）拆为 `DISSECTION_DOMAIN`；动作词另立 `DISSECTION_ACTION`。「起号」按用户原意归 DOMAIN（它是领域信号，不是动作）。

**top-1 / top-2 裁决（多面性留痕）**：
```
GAP_PASS = 20   # 初版阈值，待 309 篇抽样校准
if top1.score - top2.score >= GAP_PASS: 置信高，返回 top1
else: 置信低，仍返回 top1，但 ScoredResult.low_confidence=True（供 P2-7 标「模板适配：低」）
```
- 硬短路组（TUTORIAL/INTERVIEW/《》）命中即直接返回，不参与 gap 比较（它们零歧义）。

## B.5 过渡规则（不破坏 21+ 用例 · 严格分阶段）
- **阶段 0（等价翻译，零行为变更）**：把现有 11 步链逐条翻译成 §B.4 权重表，使 `classify_note_type` 输出与现状**完全一致** → 全测试绿（零回归）。此阶段不改任何分类结果。
- **阶段 1（应用 dissection 共现收紧 = 原 P0-1 行为变更）**：启用 §B.4 共现公式。此变更会改变部分预期 → 更新预期变更的测试（PLAN §5 已标 `test_prd/test_sub_monitor` 5 + `test_asr_fallback` 3 已知失败，勿修勿跳；**行为变更引入的新失败须改预期或加 RED 测试**）。
- **阶段 2（general 接入 = P1-4）**：把 §B.4 的 general 基础分 + 兜底分支 `return "general"` 接入，作为新加权项。
- **门禁**：每阶段结束 `pytest tests/` 全绿（已知存量失败集合一致）才进下一阶段；任何词表/权重改动须同步本附录 §B.4 表（单一真相源，防文档-代码漂移）。

## B.6 决策记录要求
- （路线 B 加权引擎已 SHELVED，本 DECISION 不再撰写；最小版落地结论见项目 MEMORY.md「🚫 已否决方案护栏」。）
- 零回归判据：git stash 对照法（改动前后失败集合一致），参考 PLAN §5.4。

## B.7 风险与校准
- 权重初版须对 309 篇 scys 重跑抽样，确认分布不炸（dissection 占比、general 占比合理）。
- GAP_PASS 阈值需校准：过低→全标低置信（P2-7 噪声大）；过高→错配静默。先用 20，按抽检调整。
- 硬短路组（TUTORIAL/INTERVIEW/《》）保持不变，降低回归面。

## B.8 与本 PLAN 其他项的关系
- P0-1 实现蓝图 = 本附录 §B.4 共现公式 + §B.5 阶段 1。
- P0-2 机械封顶 = `get_note_prompt` 加 length 参数（§B.3），AI 凝练度仍交模型。
- P0-3 忠实红线 = 模板文案改写（不属分类器，独立项）。
- P1-4 general = §B.4 general 基础分 + §B.5 阶段 2。
- P2-7 自评 = 消费 §B.3 `classify_note_type_scored` 的 low_confidence 留痕。
