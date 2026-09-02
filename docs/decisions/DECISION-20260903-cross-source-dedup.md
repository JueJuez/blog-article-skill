# DECISION-20260903 跨来源去重（公众号 ↔ scys）

**日期**：2026-09-03

**问题**：生财有术同时订阅公众号与 scys 站内，同一篇帖子两边 URL 不同（`mp.weixin.qq.com/s/x` vs `scys.com/articleDetail/xq_topic/<id>`），按 URL 的 dedup 索引挡不住 → 同帖从两个渠道各总结一次、落两个目录。

**决策**：把拦截做在**公众号侧**（用户指定，公众号抓取本就不稳定，宁可少抓不错抓）。`monitors/run.py:_summarize_article` 在 `mp_name == "生财有术"` 时，送总结管线前调 `articles/dedup.py: find_cross_duplicate`：

- 基准 = `notes/_scraped/scys/` 原文归档（含未总结帖，覆盖比 dedup 索引全）；
- 标题规范化（去空白标点小写）相似 ≥0.85，或互为前缀（公众号 64 字截断标题）命中；
- 兜底：正文前 300 字规范化相似 ≥0.85；标题 <8 字直接放行防误伤。

**代价**：每篇一次归档扫描（进程内缓存后 ~7ms）。命中计健康度 `scys重复`。
