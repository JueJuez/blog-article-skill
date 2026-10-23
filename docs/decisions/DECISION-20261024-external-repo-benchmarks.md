# DECISION-20261024-external-repo-benchmarks

## 背景
用户 2026-10-24 发起调研 open-notebook / open-webui 有无可借鉴功能；需对「借鉴/优化」逐项定去留。

## 决策
- 全部不立项：两仓库是「对话式研究工作台 / 聊天前端」，与本管线物种不同；重叠能力（抓取引擎链、token 预算、定时任务、prompt 派生）本库均已覆盖且更强（锚点召回 / 接地审计 / 受控替换为对方所无）。
- 三项留档参考，触发条件满足再立项，调研细节与上游参数存 `references/external-repo-benchmarks.md`：
  1. **播客出口**（outline→transcript→TTS）：新消费出口，落法同分享版 HTML 的触发词模式，触发＝用户要出播客。
  2. **检索层参数**（Markdown 标题切分 + BM25/向量混合 + 引用回跳）：触发＝要做库内检索。
  3. **insight 独立 embedding**：A4 清单（`notes/_meta/extract_checklists/`）即现成索引料，随 ② 一并考虑。

## 不做什么
- 不重开 B 组已结项判断（下游检索已自行解决）。
- 不引入对方插件体系与团队功能（任意代码执行 / 单用户纯负担）。
- 不因此动现有 prompt 与门禁。
