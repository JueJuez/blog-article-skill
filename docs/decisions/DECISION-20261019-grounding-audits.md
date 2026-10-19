# 2026-10-19 接地审计：源 payload 清洗 + 两项只读检查 + 复核两项

## 背景

2026-10-19 的 5 篇留出对照（`notes/_meta/eval/20261019-holdout-5/`）在保留率整体上升的同时，
暴露了三类机械检查看不到的问题：

1. **锚点被抓取 payload 污染**：scys 源嵌入 Coze JSON，图片尺寸 / logid / note_id 被当成内容锚点，
   一篇必留清单 15/15 的稿被 58% 召回硬拦（实际内容不缺失）。
2. **分析层数字不可溯源**：`笔记者分析 / 延伸思考 / 自测 / 讲出来` 里的「三年份素材」源里不存在；
   中文数字不进入硬锚点集合。
3. **表格数字挂错讲者**：多人实录的「结果与数据」表把哥飞的排名因素填进阿彪行；现有证据契约
   明确「不识别多说话者归属」。

复跑与补丁验证见 `notes/_meta/eval/20261019-holdout-5/ROOT_CAUSE_REGRESSIONS.md` 与
`VERIFY_P0_P1_P2.md`。

## 决定

### P0 · 锚点构造前剥 payload（已实施，影响门禁输入）

`prompts/content_signals.py:strip_source_payload`，在 `_anchor_specs` 里先清洗：

- 删「带 payload 字段的行」（logid / execute_id / note_id / xsec_token / image_list / debug_url /
  interact_info / cover 等）与超长单行 JSON；
- 删 URL / 图片 Markdown；
- **不**按「12 位以上数字」删正文——首版试过，误删真实金额 `4793700000000`、`946000000000`，已回退。
- `MIN_ANCHORS` / `RECALL_THRESHOLD` **未改**。

全量 1298 篇对比：仅 5 个源受影响，锚点 9,150→9,136（−0.15%），1 篇新通过、**0 篇新失败**。

### P1 · 两项只读审计（已实施，不拦盘）

`shared/note_grounding.py` + `scripts/audit_note_grounding.py`（PIPELINES §3.9）：

- `analysis_number_flags`：只扫分析/元层；中文数字也查；跳过列表编号、序数、问句、估算/推断标记。
- `table_attribution_flags`：只对「首列在源里有独立 `名字：` 小节、且源里反复出现」的行检查；
  4-gram 命中 ≥3 个讲者段落视为通用词跳过；拉丁词 casefold。

边界：**报告项**。全量分析层命中 9.1%（噪声偏高，不硬拦、不自动触发异步审核）；表格归属 0%
（多讲者表很少，精度高、量极小）。

### P2 · 复核两项（已实施，零生成风险）

- `QUALITY_GATE_PROMPT` 第 8/9 条：机制链完整性、数字归属；
- `build_round.py` 的 `_CHECK_INSTRUCTION.md` 与 `references/fulltext-review.md` 同步。

`QUALITY_GATE_PROMPT` 变更会改变 prompt hash；显式开启 `NOTE_QUALITY_GATE=1` 时旧报告需重出，
默认关闭不受影响。

## 不做的事

- **不加 prompt 补丁**。三条候选（H01 机制链、H03 结果表归属、H05 分析层数字）都验证过：
  保留信息能保住，但代价不稳定——H01 稿长 1.85–1.96×源、H03 fixA 新增「3—8 周」算错、
  H03 fixB 冗余 1,200 字、H05 只跑了 1 次。按项目纪律不为单次效果改生成 prompt。
- **不把 P1 直接接成门禁**。分析层 9.1% 噪声未压下来之前，接线会制造大量误报。
- **不调低锚点阈值**。P0 已修根因，调阈值会放过真漏内容。

## 后果与后续

- 本批按**有限验证收尾**，生成 prompt 保留；下一批真实生产按
  `notes/_meta/eval/20261019-holdout-5/NEXT_PRODUCTION_PROTOCOL.md`：
  跑 P1 + 消费异步复审 → 只对确认问题定向修订 → 在 `notes/_meta/grounding/ledger.jsonl`
  记真错 / 误报 / 修订次数 / 人工分钟 → 按实际成本决定是否自动接线或加窄补丁。
- 残留风险：H05 这类「分析层编数字」若没人跑 P1，仍可能落盘；异步审核默认 15% 抽样不保证抽到。
- 验收边界：P1 的「真错」需回源核原句；P2 的机制链判定仍受审者口径影响（H01 出现「严格清单判缺一环、
  P2 审者判 ok」的分歧），因此不以 P2 单独认定回归。
