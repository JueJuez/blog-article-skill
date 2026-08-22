# DECISION-20260821-scys-classification-fix

## 背景
批量总结 scys（生财有术）309 篇文章时发现分类器被 boilerplate 污染：
- scys 页面导航含「AI问答」-> INTERVIEW_KEYWORDS 的「问答」命中 -> 309 篇全部误判 interview
- 「分享」过于泛化 -> KEY_POINTS_KEYWORDS 误触发 -> 大量 structured 被抢成 key_points

## 决策
1. 从 INTERVIEW_KEYWORDS 移除「问答」（boilerplate 噪声；真访谈靠「访谈/对谈/专访/对话」+ 内容级兜底）
2. 从 KEY_POINTS_KEYWORDS 移除「分享」（过于泛化；真口播靠「公开课/讲座/演讲/播客/直播」等精确信号）
3. 新增 8 个回归测试 `tests/test_scys_classification.py`，全部通过；38 个旧测试零回归

## 影响
修正后分布：structured 244(79%) / case 17(6%) / opinion 16(5%) / key_points 15(5%) / interview 10(3%) / roundup 6(2%) / reading 1
- 65 篇用错模板的文章已删除飞书旧节点并重新总结
- 子 Agent 改为走正规入口（get_note_prompt.py 获取分类器选定的模板 + QUALITY_GATE_SELFCHECK）
