# DECISION-20260826-dissection-template

## 背景
ppt-master-main 项目 social-content SKILL 含 note_dissection_sop.md（逆向解剖 SOP + note_molds 模具字段）。
评估结论：模具本体是生产侧创作骨架，与消费侧总结红线相反，不整体移植；仅移植其
「可复用结构模具」提炼字段，解决订阅源（scys 等）大量「爆款拆解/带货/涨粉/账号运营」
复盘文用 case 模板总结后「知其然不知其可复用」的缺口。

## 决策
1. `NOTE_TEMPLATES` 新增第 8 类 `dissection` 创作解剖：背景→打法→结果→**可复用结构模具**
   （标题公式/开头钩子/正文节奏/结尾 CTA，表格呈现 + 占位符化）+ 禁区 + 延伸思考；
   版权边界=**不侵权而非不借鉴**（用户 2026-08-26 定调灵活版）：微创新鼓励、大众/公开素材
   人人可用，仅禁整段照搬原创文案与搬运独家素材；引用原句做学习证据不受限。
2. `classify.py` 新增 `DISSECTION_KEYWORDS`（只收创作域专有词：带货/涨粉/起号/账号运营等；
   「引流/变现/粉丝」过泛不收），插在 opinion 之后、case 之前。
3. 顺手修「评论区」污染：剔除后再匹配 OPINION，防「评论区运营」被「评论」误抢成 opinion。

## 影响
- 既有 7 类路由零回归（stash 对照验证）；全量套件失败均为存量（monitors 在途改动 + 缺 ctranslate2），与本次无关
- 新增 21 个测试 `tests/test_dissection_template.py`；RULES.md/AGENTS.md/scys-sop 已同步 8 模板清单
