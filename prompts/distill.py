# -*- coding: utf-8 -*-
"""prompts/distill.py — S7 独立提炼契约（prompt 侧）。

PLAN-20261026-low-cost-quality §9.3–§9.4（2026-10-27）。**候选实现，未接生产派单**。

定位：
- 与同轮 A4 提取清单（`prompts/extract_checklist.py`）的关系：A4 是**同一次派工内**
  的清单自查（清单块随后被剥离正文）；本模块是**独立提炼步骤**的产物契约——
  先按原文提炼出主线/条目/关系（结构化 JSON，`shared/distill_pack.py` 落盘校验），
  写作步骤再按模板组织。清单帮助对账，不充当大纲；提炼包同理——它是**取材底座**，
  不是唯一标准答案（§9.5）。
- 自包含任务（§9.3.5）：完整源文本 + 三类产物定义 + 输出 JSON 契约一次给全，
  隔离无关会话与旧稿修改史。长源分块与分段提炼留待真实需要（S8 配对时按源决定），
  首轮整篇提炼。
- 边界（与 S5 输出契约一致）：**先按原文提炼**——源里有的信息不因「模板没有栏目」
  而丢；不做延伸分析（不新增模型自建的洞察/反方/类比/迁移）；原话与摘意区分，
  原话须可机检命中（校验在 `shared/distill_pack.py`）。
- `DISTILL_MARKER` 用于测试隔离断言：本契约不得拼接进九类写作模板。
"""
from __future__ import annotations

from prompts.provenance import text_hash

DISTILL_MARKER = "<!-- DISTILL_CONTRACT v1 -->"

# 与 shared/distill_pack.py 的枚举保持一致（单一真源在这里，pack 侧 import）
ITEM_TYPES = ("viewpoint", "case", "step", "number", "resource", "condition", "counterexample")
STANCE_KINDS = ("quote", "paraphrase")
RELATION_KINDS = ("supports", "depends", "limits", "contradicts")

# 提炼 prompt 版本号：改下面任何契约文案即视为换版本（distill_prompt_sha256 会变）
CONTRACT_VERSION = 1

_SCHEMA_EXAMPLE = """```json
{
  "mainline": {
    "question": "源要回答的核心问题（作者视角，不替作者扩大）",
    "progression": ["推进过程按顺序 3-8 条，每条一句话"],
    "conclusions": ["作者的判断与结论，含方向与强度（确定/可能/仅单一案例）"]
  },
  "items": [
    {"id": "i1", "type": "viewpoint", "stance": "quote",
     "content": "原话原句（quote 时逐字，不做任何改写）",
     "source_lines": [12, 12]},
    {"id": "i2", "type": "number", "stance": "paraphrase",
     "content": "8000 元/月，2019 年首单报价（口径与期间随条目保留）",
     "source_lines": [15, 16]}
  ],
  "relations": [
    {"from": "i2", "to": "i1", "kind": "supports", "note": "证据支持观点"}
  ]
}
```"""


def _types_line() -> str:
    zh = {
        "viewpoint": "观点/判断（含强度）", "case": "案例/事件", "step": "步骤/方法",
        "number": "数字/口径", "resource": "资源/工具/链接", "condition": "条件/前提/适用边界",
        "counterexample": "反例/例外/失败",
    }
    return "、".join(f"`{k}`（{v}）" for k, v in zh.items())


def rules_text() -> str:
    """提炼契约正文（自包含三类产物定义与边界；改这里 = 换 distill_prompt_sha256）。"""
    return f"""{DISTILL_MARKER}
# 独立提炼任务（先按原文提炼，不写作）

你是**提炼者**，不是作者也不是笔记写手。任务：把下面给出的完整源文本提炼成三类结构化产物，输出**一个 JSON 对象**。提炼结果服务后续写作与复核的回源取材——**先按原文提炼**：源里有的信息不因「下游模板可能没有对应栏目」而丢掉；也不要因为「看起来像延伸」就删掉源内已有的分析、反驳、类比、方法或练习（它们是源信息，归属作者）。

## 硬边界

- **不做延伸分析**：不新增你自己的洞察、反方论证、类比、迁移建议、行动项或评价。你的产出里只应有源里讲过的东西。
- **原话与摘意区分**：`stance` 只取 `quote`（逐字原话，`content` 必须与源完全一致，不得改写）或 `paraphrase`（摘意/转述）。拿不准就写 paraphrase。
- **来源位置**：每条 `source_lines` 给 1-based 行号范围 `[起, 止]`（按你收到的源文本逐行计）。指向空行或编造行号都会被机械校验标出。
- **忠实属性**：说话者归属、时间、数字口径/单位/期间、亲历/转述/预测/假设、结论强度，随条目保留，不在关系里偷偷升格。
- **覆盖优先于整洁**：重要观点、论证/因果过程、案例、步骤、数字口径、资源、条件、反例都该有条目；重复表达可合并，但每处独有事实不随合并消失。
- 源没讲清、你读不到的部分：明确留空并在 `notes` 里说明，**不凭常识补齐**。

## 三类产物

1. **主线地图**（`mainline`）：`question` 源要回答的核心问题；`progression` 推进过程（按顺序）；`conclusions` 作者的判断与结论（含强度）。
2. **信息条目**（`items`）：`type` 取 {_types_line()}。每条给 `id`（i1、i2…）、`content`、`stance`、`source_lines`。
3. **关系说明**（`relations`）：`kind` 取 `supports`（证据/论据支持哪个观点）、`depends`（步骤/结论依赖什么前提）、`limits`（条件/例外限制什么）、`contradicts`（源内的矛盾/张力）。`from`/`to` 引用条目 `id`，不悬空。

## 输出格式（只输出 JSON，不要包代码围栏外的其他文字）

{_SCHEMA_EXAMPLE}

条目数量由源的真实信息量决定：只有 3 条就写 3 条，不为凑数拆分；长源几十条也照实写。
"""


def render_distill_prompt(title: str, source_text: str, material_hint: str = "") -> str:
    """渲染自包含提炼任务。material_hint：S6 材料索引的一句话提示（可空）。"""
    lines = text_hash  # noqa: F841  （保持 import 一致性）
    parts = [
        f"源标题：{title or '（未提供）'}",
        "",
        "下面是完整源文本，行号从 1 起逐行计（你引用 source_lines 时按这个计）：",
        "",
        "<<<SOURCE_BEGIN>>>",
        source_text or "",
        "<<<SOURCE_END>>>",
        "",
    ]
    if material_hint:
        parts += [f"材料索引提示：{material_hint}", ""]
    parts += [rules_text()]
    return "\n".join(parts)


def prompt_hash(title: str, source_text: str, material_hint: str = "") -> str:
    """提炼 prompt 版本 hash（随 pack 记录；改契约文案即换版本）。"""
    return text_hash(render_distill_prompt(title, source_text, material_hint))
