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

DISTILL_MARKER = "<!-- DISTILL_CONTRACT v2 -->"

# 与 shared/distill_pack.py 的枚举保持一致（单一真源在这里，pack 侧 import）
ITEM_TYPES = ("viewpoint", "case", "step", "number", "resource", "condition", "counterexample")
STANCE_KINDS = ("quote", "paraphrase")
RELATION_KINDS = ("supports", "depends", "limits", "contradicts")

# 提炼 prompt 版本号：改下面任何契约文案即视为换版本（distill_prompt_sha256 会变，
# 旧 pack 自动失效不被消费——版本绑定见 `shared/distill_dispatch.load_valid_pack_for_source`）
#
# v2（2026-10-27，S9 真实抽样后）：针对「数字固化」失真补三条硬边界——
#   ① 数字逐行成条（不同口径不许合并）；② 不确定读数保留原写法、不许补精度或二选一；
#   ③ 引用前重新确认行号（长源末段易漂，S9-B 回包 end 多算 5 行被机械校验拦下的教训）。
#   ④ >40k 字源分块提炼：源文本按行前缀真实行号下发（`NNN| 文本`），拼回来仍是同一份源，
#      `source_lines` 直接报全局行号，不需要模型做算术还原。CONTRACT_VERSION = 2
CONTRACT_VERSION = 2

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
     "source_lines": [15, 16]},
    {"id": "i3", "type": "number", "stance": "paraphrase",
     "content": "12000 元/月，2021 年报价（期间不同 → 另起一条，不与 i2 合并）",
     "source_lines": [18, 18]},
    {"id": "i4", "type": "number", "stance": "quote",
     "content": "利率从4.67上涨到了4.7 一 三（源此处疑似读数断字，读数存疑，不得写成 4.713%）",
     "source_lines": [43, 43]}
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
- **来源位置**：每条 `source_lines` 给 1-based 行号范围 `[起, 止]`，取值就是**源文本每行开头标注的那个数字**（照抄即可，不要另行编号）。**先定位到该句所在行再写行号**，不要凭印象估算；长源靠末尾的段落尤其容易数错，**止行不得超过你收到的最后一行行号**。指向空行或越界的行号都会被机械校验拦下。
- **数字逐行成条**：同一句里出现多个不同口径的读数（不同期间 / 对象 / 单位 / 基准，如「目标价由 12 元上调到 15 元」），每个口径单独一条 `number` 条目，`content` 里带上口径要素（单位、期间、归属、基准）。不许把多个口径并成一条，也不许替作者挑一个写。
- **不确定读数不许补精度**：源里的断字、听不清、同一指标两说并存的读数（如 ASR 把 4.713 读成「4.7 一 三」、同一指标一会儿说 11 个点一会儿说 4.78），`content` **照抄源的原写法**并标「读数存疑」/「两说并存」——**不得换算、拼合或补写成精确值**（不许把「4.7 一 三」写成 4.713%），也不得替作者二选一。看不见、听不清的就留空并在 `notes` 说明。
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


def numbered_source(source_text: str, start_line: int = 1) -> str:
    """给源每行加 `NNN| ` 行号前缀（`start_line` = 1-based 起始行，默认 1）。

    作用：提炼者看到的数字就是**全局行号**，回包写 `source_lines` 无需换算；
    这也是 >40k 源分块提炼后能拼回同一坐标的前提（分块只是切 `start_line` 区间）。
    """
    lines = (source_text or "").split("\n")
    width = len(str(start_line + len(lines)))
    return "\n".join(f"{start_line + i:{width}d}| {ln}" for i, ln in enumerate(lines))


def render_distill_prompt(title: str, source_text: str, material_hint: str = "",
                          chunk_label: str = "", start_line: int = 1) -> str:
    """渲染自包含提炼任务。material_hint：S6 材料索引的一句话提示（可空）。

    `chunk_label`：非整篇时写明「第 k/N 块（源第 a–b 行）」，让提炼者知道自己看的是节选，
    因此**不要替全篇下结论**——只提炼本块范围内的信息（§9.3.5：分段不许切断指代与条件）。
    `start_line`：分块时该片段在整篇里的起始行（默认 1）。借此下发全局行号，
    分块提炼后无需换算即可拼回同一坐标。
    """
    lines = (source_text or "").split("\n")
    scope = f"（本任务是整篇，共 {start_line - 1 + len(lines)} 行）"
    if chunk_label:
        scope = f"（本任务处理{chunk_label}）"
    parts = [
        f"源标题：{title or '（未提供）'}",
        "",
        f"下面是源文本{scope}，每行开头的数字就是**该行在整篇源里的行号**——"
        "你引用 `source_lines` 时直接照抄这个数字，不要自己重新编号或估算：",
        "",
        "<<<SOURCE_BEGIN>>>",
        numbered_source(source_text or "", start_line),
        "<<<SOURCE_END>>>",
        "",
    ]
    if material_hint:
        parts += [f"材料索引提示：{material_hint}", ""]
    parts += [rules_text()]
    return "\n".join(parts)


def chunk_prompt_hash(title: str, source_text: str, chunk_label: str = "",
                      start_line: int = 1) -> str:
    """分块块的 prompt 版本 hash（分块提炼时每块各记一个）。"""
    return text_hash(render_distill_prompt(title, source_text, chunk_label=chunk_label,
                                           start_line=start_line))


def prompt_hash(title: str, source_text: str, material_hint: str = "") -> str:
    """提炼 prompt 版本 hash（随 pack 记录；改契约文案即换版本）。"""
    return text_hash(render_distill_prompt(title, source_text, material_hint))
