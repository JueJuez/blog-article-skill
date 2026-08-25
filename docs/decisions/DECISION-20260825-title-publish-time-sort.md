# 决策：飞书落盘标题 / 排序 / 发布时间一致性（2026-08-25）

## 问题
1. **标题退化**：监控列表标题（如「中金研究」）被当 `original_title` → 文件名 → 飞书节点标题。
2. **排序**：监控/补齐批量落盘时飞书默认按创建顺序，旧的排前面。
3. **散文日期错**：散文文件名日期 = 处理时间（`publish_time` 默认 0），不是文章发布时间。

## 决策
- **标题（三类统一）**：均走 `feishu.save(title=真实标题)`；监控/补齐用 `fetch_web_content` 取正文页真实标题 `real_title`，`_is_generic_title` 兜底泛化标题降级从正文提炼。
- **排序（分源）**：仅监控/补齐在 `apply_summaries` 按 `publish_time` 倒序创建（新在前）；**散文单篇提交不做批量排序**（走 `articles/run.py` 单篇路径，本就无队列）。
- **发布时间（三类统一）**：监控/补齐用 feed 自带发布时间；**散文同步**——`fetch_web_content` 新增 `publish_time`（从 meta `article:published_time` / JSON-LD `datePublished` / `<time>` 提取）→ `summarize_and_save` 透传，文件名日期 = 文章发布时间。三类文件名日期现已统一为文章发布时间。

## 验证
py_compile 全过；pytest test_prd + test_scys_routing 10 passed；离线验证 `_parse_date_to_epoch` 支持 ISO / 中文 / naive（按 +08:00）。
