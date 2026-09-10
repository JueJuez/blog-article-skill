# PLAN-20260906 · 双知识库同步登记表 + 淘汰策略（dedup.json 扩展）

> 状态：**方案 V1 已存档（2026-09-06）；同日完成架构审计——P0×3 / P1×7 / P2×7，见 §6；修订后开工**
> 背景：用户双库（飞书 + Obsidian）**不双写**、互迁；grilling 访谈已收敛（淘汰对象、link 语义、触发机制、4 项默认决策）。
> 原则：**记录 = 真源，永不自动清理**（沿用 scripts/vault_lifecycle.py 已确立的哲学）；清理清的是失效 link 与淘汰载荷，不是历史记录。

## 1. 访谈结论（已对齐）

- **登记表**：扩展现有 `.cache/dedup.json`，value 增至 `{source_url, title, filename, feishu_link, obsidian_link, ts}`。不另建表、不写 frontmatter（见 D）。
- **link 语义**：link = 「最后一次验证成功时写入的指针」，非实时状态。验证 = 对账时树遍历集合比对（本地文件清单 vs 飞书子文档树），**不做逐链接 HTTP 探活**。失效 → 清 link → 下轮补传 → 写新 link。每次同步结束，两端 link 有效且唯一。
- **重传**：默认「删旧写新」（与 articles/feishu.py save() 现有 upsert 语义一致）。
- **触发**：主动（「清理一下同步库/同步表」）+ 被动 journal（每次清理记 `.cache/sync_journal.jsonl`；任一入口发现距上次 >7 天自动补跑）。补跑的是「对账+补传+清死链」动作。
- **淘汰门槛**：中间产物（raw/transcripts）总结完即清、未总结完一直留；成品淘汰需**两端 link 均有效**（防「link 还在但知识库文件已被删」的漂移）。
- **豁免**：scys 原文归档（`notes/_scraped/scys/`）永不淘汰——它是跨源去重 find_cross_duplicate 的比对基准（articles/dedup.py:121）。

## 2. 默认决策（4 项，用户跳过提问视作接受，可随时推翻）

| # | 决策点 | 落定值 |
|---|---|---|
| 1 | raw 不可再生例外 | 公众号等不可再生来源 raw 永久留（历史代理仅回溯到 2026-06）；可再生来源正常淘汰；来源清单写配置 |
| 2 | 存量回填 | 惰性回填：旧记录不动，被对账/同步碰到才补；对账按标题集合比对自动接上存量 |
| 3 | YAML 存量 | 本地不动，飞书写入端 strip（一条逻辑覆盖新旧笔记） |
| 4 | 重传语义 | 删旧写新 |

## 3. 改动清单（A~E 五块）

- **A. 视频管线接入主索引**：mark 收敛进 `articles/main.py:save_summarized_article` 正常分支（384 行 save_all 后，与 draft-only 分支 380 行对齐）；文章/视频所有调用方自动登记。前提核实过：签名带 `original_url`，重命名（328-334）发生在保存前、最终名贯穿到底。
- **B. dedup.py 登记表扩展**：`mark_summarized` 增可选字段；新增 `set_links()` / `get_entry()` / `clear_links()`；旧记录读时 `.get()` 补空，不迁移；key 仍为 sha256[:16]。
- **C. 三步循环命令**（扩展 scripts/vault_lifecycle.py，整合 audit_sync.py）：`reconcile` 对账 → `push --target feishu|obsidian` 补传（飞书侧删旧写新）→ `gc` 淘汰（中间品总结完即清；成品需两端 link 均有效；scys 归档豁免；raw 按可再生性区分）+ journal。
- **D. frontmatter 停产 + 写入端 strip**：`articles/main.py` 341-357 去掉 YAML 拼装；`articles/feishu.py` 加 `_strip_frontmatter()` 接入 save()/save_async()。标题来源不受影响（title = original_title or 正文提取，main.py:298）。`templates.py` 不动（正文「来源链接」行是 source_url 的机器提取源）。
- **E. 测试（TDD）**：登记表字段与 link API 单测、mark 幂等与最终文件名单测、strip 单测、gc 门槛（scys 豁免/raw 例外/系列课判定）单测、全量回归。

## 4. 任务拆分

| # | 任务 | 文件 | 验证 |
|---|---|---|---|
| 1 | dedup.py 登记表扩展 + link API | `articles/dedup.py` | 新增单测 |
| 2 | 正常分支补 mark + 停产 YAML（收敛删除 main.py:564 调用方 mark） | `articles/main.py` | mark 幂等单测 |
| 3 | 飞书写入端 strip | `articles/feishu.py` | strip 单测 |
| 4 | gc 淘汰 + journal | `scripts/vault_lifecycle.py` | 门槛单测 |
| 5 | reconcile/push 整合 audit_sync 对账逻辑 | `scripts/vault_lifecycle.py` | 手工对账一轮 |
| 6 | 全量测试回归 | 全仓 | 全绿 |

## 5. 既有机制整合声明

- `scripts/vault_lifecycle.py`（2026-09-06 已存在）：已实现「记录=真源永不清理」+ reconcile（清丢失记录，--apply 强制 --scope-substr）+ old-report（只读）。本方案是它的**扩展**，不另起炉灶。
- `audit_sync.py`（根目录 + scripts/ 各一份）：Obsidian↔飞书标题树对账 + --fix 幂等补传（不看登记表）。**必须收编进 push**，否则同步层重蹈「管线各自实现」的反模式（用户问题 1 的教训）。
- `articles/feishu.py:save()`：删旧+新建 upsert + `_node_creation_lock` 文件锁，天然支撑重传语义。

## 6. 架构审计发现（2026-09-06，V1 存档后逐行核实）

### P0 —— 会破坏整套逻辑

- **P0-1｜gc 与 reconcile 语义冲突：合法淘汰 vs 异常丢失不可区分。**
  reconcile 的判定是「index 命中但 vault 文件丢失 → 清记录 → 重抓重总结」；新 gc 的合法动作是「成品两端 link 均有效 → 清本地成品」。两者在磁盘上表现为**同一状态**（index 有记录、本地无文件），语义却相反。本地成品被 gc 合法清掉后，reconcile 会误清 index 记录 → 下次同一内容重抓重总结 → **重复总结 + 飞书端删旧建新 + token 重复消耗**。
  修复方向：登记表加 `evicted` 标志（gc 清本地成品时打标，reconcile 跳过 evicted 且 link 完好的记录）；reconcile 的清理条件收紧为「从未上传（两端 link 空）且无 evicted」。或最保守：gc 不清本地成品，只清 raw/transcripts。
  **已拍板（2026-09-07）：选最保守方案（B）——gc 不清本地成品，只清中间产物（raw/transcripts）；成品淘汰交给用户手动。evicted 标志分支不采用。**

- **P0-2｜登记表（真源）放在 .cache/（gitignore 易失目录）。**
  dedup.py:6 docstring 自证 .cache 不进仓库；`_load_index` 异常时返回 `{}`（dedup.py:50）。索引一丢（清缓存/换机/误删），**全部内容会被重新总结落盘一遍**。现状 dedup 只是防重索引、丢了可忍；V1 把同步登记表（feishu_link/obsidian_link）也压上去 = 真源放缓存目录，丢库即丢全部 link 记录。
  修复方向：登记表迁出 .cache（如 `notes/_meta/sync_ledger.json`），迁移代码兼容旧路径自动搬家；并纳入备份策略。

- **P0-3｜dedup.json 读写无锁、整文件读改写——并发丢记录。**
  `_load_index → 改 → _save_index` 三步无锁（dedup.py:43-56）。并行监控（--parallel 三 worker + 父进程）、recurring 定时任务、手动 sync 命令、队列消费子 Agent 均可能并发。A 读 → B 读 → A 写 → B 写 → **A 的 mark/link 更新被覆盖丢失**。现状 mark 已有并发（风险存在但写入少）；新方案写入点大增（每次同步写 link），且 link 记录丢失 = 下轮 push 重复上传。
  修复方向：文件锁（复用 feishu.py `_node_creation_lock` 先例）或统一单写入口。

### P1 —— 特定场景出错

- **P1-1｜URL 归一化只排序不剥离 query（dedup.py:25-36）→ 同源多 key。** 公众号 URL 的 chksm/encKey 等可变参数、B站 `vd_source`/`?p=` 分P、`b23.tv` 短链与长链并存 → 同一内容多个 hash、多条登记、重复文档。监控靠 seen 窗口兜底，手动+监控混用即触发。修复：host 白名单剥离可变 query（b23.tv 先展开）；分P 参数保留（是不同内容）。
- **P1-2｜视频路径无入口闸门、只出口 mark → 重复跑产生重复文件。** skill_main 有闸门（main.py:482/518）；videos/main.py 只 import save_summarized_article，对同一视频跑两次：第二次无闸门直接再保存 → 冲突重命名《xxx-1.md》→ 两份成品 + 飞书删旧建新互相覆盖。修复：视频调用点前置 `is_summarized` 检查；同时收敛删除 main.py:564 调用方 mark（否则 mark 分散问题只修一半）。
- **P1-3｜标题 sanitize 碰撞 → push/reconcile 死循环。** 飞书按 sanitized 标题删旧建新；本地允许同名加后缀共存。两篇不同文章标题归一后相同（《第 1 期》vs《第1期》）→ push A、push B 删 A、reconcile 又补 A 删 B → 无限翻新。修复：对账时标题碰撞告警人工裁决；本地命名前做 sanitize 归一碰撞检测。
- **P1-4｜缺 pull：本地空 + raw 已淘汰的场景无法重建。** 全部路径单向（本地→云）。gc 清本地成品 + raw 可再生也已清后，重建 Obsidian / 迁第三个库只能重抓（公众号 2026-06 前不可能）或重总结（raw 无）。修复：补 pull 命令（audit_sync 的 collect_feishu 树遍历可复用）；或声明 V2。
- **P1-5｜audit_sync --fix 不收编 → 两套对账并存。** 旧的既不看登记表也不看 link。修复：改造为 push 薄壳或废弃。
- **P1-6｜系列课链路零 mark 零闸门（已核实 apply_pending_series.py 无 dedup 调用）。** gc「总结完即清」对系列 raw 无法用 index 判定；漏判 = 总结中的分片被清 → apply_pending_series 落地空壳。修复：系列 raw 按 `.body.md` 存在性判定或直接豁免。
- **P1-7｜force 逃生舱无联动。** save_summary_only force=True 重复总结 → 第二份文档、两个 link，旧 link 无人清理。修复：force 路径联动删旧文件 + 清旧 link。

### P2 —— 边界与降级

- **P2-1** obsidian_link 应记 vault 相对路径（防用户搬家 vault 全失效），不记绝对路径。
- **P2-2** transcripts 淘汰有重新抓取的 412 限流熔断成本，建议跟随 raw 的不可再生策略或保留期更长。
- **P2-3** 存量笔记正文若无「来源链接」行（老模板时期产出），惰性回填提取不到 source_url → 永缺 → 对账降级纯标题匹配（可接受，需知悉）。
- **P2-4** YAML strip 需满足「首行即 `---` 且闭合 `---` 在前 N 行内」才动作，防正文水平线误切。
- **P2-5** gc 豁免名单显式落配置（scys 归档、不可再生 raw），不做隐式硬编码散落。
- **P2-6** evicted/淘汰动作全部进 journal，被动触发 >7 天补跑的判定读 journal 而非文件 mtime。
- **P2-7** 跨源去重基准读文件头 4000 字符、`split("---",1)` 取后半（dedup.py:154）——停产 frontmatter 后 scys 归档无 YAML，该行取到全文，无害；但正文含 `---` 水平线时会误切相似度前缀，影响可忽略，知悉即可。

## 7. 下一步

P0-1 已拍板（2026-09-07）：gc 不清本地成品、只清中间产物。按 §4 任务拆分 TDD 开工；P0-2/P0-3 并入任务 1 一起落地；三个生成侧补丁（视频入口闸门 P1-2、落盘日期前缀强制、系列课集数一致性校验）与 scys 20 条 CDP 登录态重抓一并纳入实施（依据 DECISION-20260907）。新会话开工前先读 DECISION-20260907-cleanup-resolutions-and-v2-handoff.md。
