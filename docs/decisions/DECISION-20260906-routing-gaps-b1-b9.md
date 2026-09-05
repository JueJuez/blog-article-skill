# 决策记录：路由缺口 B1-B9 收口（【待归类】复发根治）

> 日期：2026-09-06 | 关联：`shared/routing.py` + `articles/main.py` + `articles/run.py` + `videos/main.py` + `scripts/` + DECISION-20260905（autoroute 守卫）

## 现象
飞书【待归类】滞留 270 篇、Obsidian 95 篇。全入口审计发现 9 处缺口：迁移脚本 item 缺 category、
视频 finalize 不 autoroute、系列/播放列表不传 folder（容器挂根）、scys 批量硬编码路径、
系列运维 verify 只读却会新建根容器、场景2 丢 original_url 且本地文件被 --url 抢占、
「文章总结/转载」等系统标签被误当分类。

## 根因
tags 不参与路由（设计如此）但调用方没折算成 category；folder 计算散在调用侧靠记性；
只读运维复用了带建副作用的容器接口。

## 修复（TDD：先写 tests/test_routing_gaps_20260905.py 28 个失败测试再改）
- 分类单一真源：`shared/routing.py:CATEGORY_SKIP_TAGS` + `category_from_tags(tags)`；`autoroute_folder`
  加 category 参数并全链路透传。
- 系列路径单点：`videos/main.py:series_folder()`，系列容器/总览 3 处调用统一传 folder。
- `series_maintenance.py` verify 改逐层 list_children 只查不建；reland/regen 补传 folder。
- `articles/run.py` 场景2 补传 original_url；本地文件优先于 --url 判定。
- `land_scys_batch.py` / `migrate_obsidian_vault.py` 全部改走 resolve_folder。

## 回归
新增 28 passed；全量 412 passed。迁移执行记录：飞书 270/270、Obsidian 95/95 全部成功。
