# DECISION-20260905-template-borrow-methodology 模板借鉴方法论库增量

**日期**：2026-09-05 ｜ **状态**：已实施 ｜ **测试**：`tests/test_template_borrow_20260905.py`（13 用例全过）

## 决策
把 PPT 项目侧两个方法论源的部分纪律移植进 5 个模板（源 A：`content/reading/BOOK_DIGEST_METHODOLOGY.md`；源 B：`.workbuddy/skills/social-content`）：
- **reading**（3 点）：论点间关系标注（并列/递进/对比/反驳）、三重验证筛选（跨域佐证/预测力/独特性）、适用边界与常见误用块；「只带走三句话」条件化收束。
- **dissection**（2 点）：分发门归类（算法/订阅/社交 + 第一抓手）、模具=单案例假设待校准。
- **case**（2 点）：数据基线口径（无基线标「原文未提供」，不写虚数）、单案例经验=假设。
- **structured**（1 点）：私房术语注明与日常用法的差异，防望文生义。
- **opinion**（1 点）：论据标注佐证强度（单一案例/多源跨域/纯逻辑推演）。

## 不移植（YAGNI，已逐条核对无缺口）
- key_points / interview / roundup / general：正反例对照、分歧保真、矩阵坑位行、「未提及」纪律已覆盖对应价值，再加属无信息量稀释。
- reading 5 提取器全文流水线、RIA++ 六段全量结构：我们的输入是书评/拆书稿二手内容，非原书全文。

## 机制
TDD：RED（10 failed, 3 passed）→ 8 处 SearchReplace 改 `prompts/templates.py` → GREEN（313 passed）。借鉴点以「筛选/标注纪律」注入而非新增结构段，防模板膨胀。
