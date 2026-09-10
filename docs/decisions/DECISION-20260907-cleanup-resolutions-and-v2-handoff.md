# DECISION-20260907 · 存量清理收尾拍板与 V2 交接清单

> 承接：round3 已全绿落地（删 25 / 改名 36 / undo 61 / 备份 61 / verify 全绿）；本文件记录复盘问答的最终拍板与结论修正，供 V2 开工会话直接消费。**原「待拍板」项已全部落定，本文件即开工依据。**

## 已拍板（2026-09-07）

1. **三个生成侧补丁并入 V2 一起 TDD 实施**：视频管线入口闸门（P1-2）、落盘日期前缀强制、系列课集数一致性校验。不单独打补丁。
2. **P0-1 选 B 方案**：gc 不清本地成品，只清中间产物（raw/transcripts）；成品淘汰交给用户手动。与「Obsidian 唯一真源」一致，零新增机制；PLAN-20260906 §6 的 evicted 标志分支不采用。
3. **系列课无数字集数时保持原标题**：如「A上 / A下」「坐庄（下）」这类标题不做数字集数推断；顺序可参考发布时间属性（先发的排前面），但**不好判断就保留原标题**，不强行加集数前缀。坐庄(下) 一件按此终态处理（保持原样）。
4. **scys「无日期」结论修正（原结论作废）**：
   - 原错误结论：scys 20 件无日期 = 平台限制、原链无发布日期字段、代码无解。
   - 真实根因：上轮一次性日期补抓脚本的 `fetch_scys()` 用**裸 HTTP 无 Cookie/登录态**访问（该脚本仅 bilibili 路径传 BILI_COOKIE），而 scys.com 是登录墙社区，未登录视图渲染不出发布日期。属我方抓取通路失误，非平台限制。
   - 用户举证原链（脚本未登录访问即复现无日期视图；浏览器登录态打开则一切正常）：
     - https://scys.com/articleDetail/xq_topic/55522825225481124
     - https://scys.com/articleDetail/xq_topic/14422114455242422
     - https://scys.com/articleDetail/xq_topic/45544148552844858
   - 修复路径：走项目统一 CDP 通路（`shared/cdp_session.py` SharedCdpSession，scys 链接自动分流登录态）重抓 20 条，补日期前缀。**列入 V2 实施清单**；注意 CDP 会先关闭用户 Chrome（侵入性动作，执行前需用户知情同意）。
   - 重抓清单依据：归档目录 `fetch_meta.json` 中 `status=no_date_found` 且 `kind=scys` 的条目。
5. **undo/备份归档保留**：两轮备份（cleanup2 517 文件 + cleanup3 61 文件）、两份 undo 日志、fetch_meta.json、targets.json 已移至 vault 外 `D:\Code\Obsidian\obsidian-path\_cleanup_archive\`（Obsidian 不索引；目录内有 README 说明与恢复方法）。观察 1-2 周确认无误后可整体删除。

## 归 V2 范围、无需单独处理

- 飞书镜像端的重复文档与杂物行：push/reconcile 以 Obsidian 真源重建时自动对齐。
- frontmatter 停产 + 写入端 strip：随 V2 重写 PLAN 落地（见 `DECISION-20260906-feishu-clutter-frontmatter-deferred.md`，已归档至 `_archive/decisions/`）。
- scys 20 条 CDP 登录态重抓补日期前缀（见已拍板 4）。

## 本轮教训（V2 实施时注意）

- **抓取必须走项目统一入口**：一次性脚本绕过 SharedCdpSession 通路、裸 HTTP 无登录态，导致「平台无日期」误判并差点固化进决策。平台侧结论必须先排除自身通路问题。
- 改名链执行前应做冲突排序（`-` 码点 < `.` 的陷阱），跳过动作必须显式报警而非只进统计行。
- 编辑类操作后必须回读核实（工具假成功已发生 2 次）。
- 每轮清理必须配机械 verify（undo 逐行断言 + 复扫 0 动作）再报完成；断言需豁免「源名被后续改名合法重占」情形。
- 用户质疑是纠错资源：本次「平台无日期」即被用户一句「scys 是登录态？」证伪。
