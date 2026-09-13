# DECISION-20260913 字数门禁改为 source-aware 参考值 + 父 Agent 抽检 rubric

**背景**：两轮返修后仍出现「为压进固定字数上限而语义残缺」的段落（如 structured 3000 硬上限把 12.7K 转录稿压成 2.1K，句子压碎）。固定比例带（0.25–0.45）同理误伤——水货源压到 0.25 也合理、干货源压到 0.45 也可能丢。用户决策：字数从「硬拦截」降级为「参考值 + 父 Agent 抽检触发」。

**决策**：
1. `verifier.py`：有源长时按 `源长×(0.25,0.45)` 算参考带；超/偏出带**不拦**，只置 `review_flags` 触发抽检；仅两种极端硬拦：`>源长×0.7`（畸高照搬）、`<400`（内容缺失）。无源长退回 `NOTE_WORD_LIMITS` 兜底。保留 `detect_compression_issues` 过度压缩启发式（只报 warning 不拦）。
2. 新增 `prompts/review_rubric.py`：触发条件（机械信号 + 随机抽样 15%）→ 五维语义判定（A 合成度/B 完整性/C 通顺度/D 忠实度/E 合规度，仅父 LLM 能判）→ 动作（pass / fix_inline 父就地修 / resummarize 打回 / needs_review 入队）；留痕 `_review_log.jsonl` + `_review_queue.jsonl`（无人值守降级，绝不假定语义 OK）。
3. `save_summary_only` 接入 `source_chars`（从 raw_file 算）、透传 `review_flags`；非空时写 `_review_queue.jsonl`。`templates.py` 字数规则改「参考值非硬框，语义完整+充分合成优先，禁止照搬堆字数」。

**后果**：机械层只管「形」（H1/链接/字数触发），语义层只由人/LLM 管（互不越权）。测试 `tests/test_note_mechanical_gate.py` 54 例全过（含 `TestSourceAwareReference`）。端到端演示：自由重写「不会谈薪」（源 12666→5744 字），落盘触发 review_flags+压缩信号B，父 Agent 五维判 A/B/D/E pass、C warn（1 处 ASR 残留就地修）→ fix_inline，证明无硬框后长文可充分合成不压碎。⚠️ 已入队 194 篇旧 prompt 不自动继承参考值规则，重跑需重生成。
