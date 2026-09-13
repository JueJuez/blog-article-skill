# 决策：新笔记落盘自动追加命名空间语义标签（方案 A 已落地）

> 状态：**已落地**（2026-09-13）。取代原 `_tmp/new_template_proposal.md`（提案态）。
> 单一真源：`shared/note_classify.py` · 落盘接线：`articles/main.py:save_summarized_article` · 路由防护：`shared/routing.py:category_from_tags`。
> 关联决策：飞书 frontmatter 于 2026-09-06 刻意停产（YAML 头在飞书同步会被渲染成满屏 `---`）。

---

## 一、目标与根因

- 给**新抓取/总结的笔记**在落盘时自动带上「领域 / 主题实体 / 用途 / 来源 / 类型」五维语义标签，
  既能进 `index` 全库快照，也能在 Obsidian 内直接按维度检索（层级 hashtag 原生支持）。
- 不回填存量 1087 篇（靠 `index` 表覆盖检索）。
- 复用现有 `#标签` 行机制（纯 Markdown hashtag，Obsidian 层级 `#投资` 自动命中 `#投资/公司分析`），**不重开 YAML frontmatter**。

---

## 二、决策：采用方案 A（命名空间嵌套 hashtag，自动生成）

不引入结构化 frontmatter，只在 `tags` 列表里追加带命名空间的标签，由 `format_note_with_prompt` 统一加 `#` 输出。
语义标签**自动生成**（落盘时复用分类器算 domain/subdomain/topic/purpose，用户无需手填）。

### 落地后一篇新笔记的标签行

```
#投资/公司分析 #topic/伊利股份 #topic/护城河 #topic/ROE #用途/教学可用 #来源/价投小猪仔 #类型/结构化复盘 #文章总结 #转载
```

> 注：分类器返回**不带 `#` 前缀**的命名空间串（如 `投资/公司分析`），由 formatter 统一加 `#`，
> 避免双井号（`##`）。

### 五维命名约定

| 维度 | 命名空间 | 示例 | 检索写法 |
|---|---|---|---|
| 父领域 / 子领域 | `#父/子` | `#投资/公司分析`、`#投资/宏观`、`#商业/虚拟产品`、`#AI科技/AI编程` | `tag:#投资/公司分析`（命中子领域）/ `tag:#投资`（命中全部投资子领域） |
| 主题实体 | `#topic/…` | `#topic/伊利股份`、`#topic/小红书`、`#topic/Claude Code`、`#topic/MACD` | `tag:#topic/伊利股份` |
| 用途 | `#用途/…` | `#用途/教学可用`、`#用途/素材可用`、`#用途/金句观点`、`#用途/选题方向`、`#用途/方法论可复用` | `tag:#用途/教学可用` |
| 来源账号 | `#来源/…` | `#来源/价投小猪仔` | `tag:#来源/价投小猪仔` |
| 笔记类型 | `#类型/…` | `#类型/结构化复盘`、`#类型/案例拆解` | `tag:#类型/案例拆解` |

### 组合检索示例（Obsidian 搜索 / Dataview）

- "投资·公司分析 里能当教学案例的、讲伊利的"：
  `tag:#投资/公司分析 AND tag:#用途/教学可用 AND tag:#topic/伊利股份`
- "所有金句类口播素材（跨领域）"：
  `tag:#用途/金句观点`

---

## 三、实现要点（代码侧）

- **`shared/note_classify.py`**（正式模块，收敛自原型 `_tmp/classify_v4d.py`）：
  对外 `infer_semantic_tags(content, folder, author, note_type, url, source_account)`，
  内部含 `extract_topics`（分层抽取：标题/导语/`#标签` 命中即主题 + 正文仅补公司名/股票代码/≥2 次通用概念）、
  `lede_from_text`（抽「30秒速览」块）、`parent_from_lede`（导语信号兜底父领域）。
  - 领域标签**只输出含 `/` 的子领域**（如 `投资/公司分析`），绝不输出裸父级抢「分类」。
  - 父领域归一：`商业/搞钱` 取首级 → `商业/虚拟产品`；综合兜底类不输出 domain 标签（防 `#综合` 污染分类）。
  - 主题实体名里的 `/` 替换成 `·`（如 `Agent/智能体` → `topic/Agent·智能体`），避免三级层级。
- **`articles/main.py:save_summarized_article`**：落盘前注入语义标签（去重、异常非致命跳过）；
  分类推算加 `"/" not in tag` 跳过命名空间标签。
- **`shared/routing.py:category_from_tags`**：同步跳过含 `/` 标签（防御 + 一致性）。
- **`save_summary_only`**：调用 `save_summarized_article` 时透传 `note_type` 给分类器。
- **测试**：`tests/test_note_classify.py`（11 项全过，含端到端 mock 落盘验证：新笔记含五维标签、无双井号、不污染分类）。

---

## 四、方案 B（YAML frontmatter）已否决

| 维度 | 方案 A（采用） | 方案 B（否决） |
|---|---|---|
| 结构 | 复用 `#标签` 行，嵌套命名空间 | 重开 YAML `---` 头 |
| 回归风险 | 零结构性改动 | 须撤回 frontmatter 停产决策、重接 `draft.meta.json` / `feishu_strip` / dedup schema |
| 飞书风险 | 即使重开飞书同步也不被 YAML 头坑 | YAML 头被渲染成满屏 `---` 风险复活 |

---

## 五、范围与遗留

- **存量 1087 篇不回填**：靠 `index` 表覆盖检索；新笔记标签与分类器同源。
- **遗留（观察）**：
  1. 离线索引原型 `_tmp/classify_v4d.py` 已是过时重复实现（2026-09-13 清理时已删除）；将来若重跑 `index` 应直接复用 `shared.note_classify` 保持完全同源。
- **标签生成模式（已决 2026-09-13）**：保持**纯自动**——落盘时由代码分类器 `infer_semantic_tags` 注入五维标签，**不做**混合模式（不让总结 LLM 顺手抽标签 + 代码兜底）。理由：①与 `index` 分类器同源、信号稳定；②lite 模型（scys 批量用的）已实证不守模板纪律，标签命名空间（如 `#公司分析/投资` 写反）会写错导致 Obsidian 层级查询失效；③避免两套标签源分叉。
