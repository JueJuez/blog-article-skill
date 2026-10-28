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

DISTILL_MARKER = "<!-- DISTILL_CONTRACT v4 -->"

# 与 shared/distill_pack.py 的枚举保持一致（单一真源在这里，pack 侧 import）
# `argument` 是 v4 起的**首选**粒度：一条 = 一个完整观点/论证，连同依据、案例、步骤、
# 条件、例外与数字归属；其余类型用于确实无法并入论证的原子信息。
ITEM_TYPES = ("argument", "viewpoint", "case", "step", "number", "resource",
              "condition", "counterexample")
STANCE_KINDS = ("quote", "paraphrase")
RELATION_KINDS = ("supports", "depends", "limits", "contradicts")

# 「模型自己加的补充说明」标记——命中即说明 content 不是逐字原话。用于示例自检
# （见 `example_issues`）与复核时的提示；**不能**用它放宽真实回包的 quote 机检
# （§8.5：不为放过注解而取消机检）。
ANNOTATION_MARKERS = (
    "疑似", "存疑", "推测", "不得写成", "不得换算", "（源", "原文此处", "听不清",
    "存有两说", "两说并存", "据我判断",
)

# 一条 `readings` 至少要带的区分字段（v4「不混口径」的机械下限）。
READING_DISCRIMINATORS = ("object", "unit", "period", "basis")


def readings_issues(prefix: str, readings) -> list:
    """读数结构性校验（提炼示例、pack 装配与校验共用——单一真源）。

    - `readings` 必须是数组，每项是对象且 `value` 非空；
    - 完全相同的读数（value 与全部区分字段一致）视为重复，报出来不静默合并；
    - **两个及以上读数**时，每一项都至少要有 对象/单位/期间/基准 之一，
      否则就是把不同口径并成一句含糊读数（v4 契约的硬边界）。
    """
    if readings is None:
        return []
    if not isinstance(readings, list):
        return [f"{prefix}: readings 须为数组"]
    issues, seen = [], set()
    for k, r in enumerate(readings, 1):
        if not isinstance(r, dict):
            issues.append(f"{prefix}: readings[{k}] 须为对象")
            continue
        value = str(r.get("value") or "").strip()
        if not value:
            issues.append(f"{prefix}: readings[{k}] 缺少 value")
        key = tuple(str(r.get(f) or "").strip()
                    for f in ("value",) + READING_DISCRIMINATORS)
        if value and key in seen:
            issues.append(f"{prefix}: readings[{k}] 与前一条读数完全重复（{value}）")
        seen.add(key)
    if len(readings) >= 2:
        for k, r in enumerate(readings, 1):
            if isinstance(r, dict) and not any(
                    str(r.get(f) or "").strip() for f in READING_DISCRIMINATORS):
                issues.append(f"{prefix}: readings[{k}] 未声明口径"
                              "（对象/单位/期间/基准至少一项）")
    return issues

# 提炼 prompt 版本号：改下面任何契约文案即视为换版本（distill_prompt_sha256 会变，
# 旧 pack 自动失效不被消费——版本绑定见 `shared/distill_dispatch.load_valid_pack_for_source`）
#
# v2（2026-10-27，S9 真实抽样后）：针对「数字固化」失真补三条硬边界——
#   ① 数字逐行成条（不同口径不许合并）；② 不确定读数保留原写法、不许补精度或二选一；
#   ③ 引用前重新确认行号（长源末段易漂，S9-B 回包 end 多算 5 行被机械校验拦下的教训）。
#   ④ >40k 字源分块提炼：源文本按行前缀真实行号下发（`NNN| 文本`），拼回来仍是同一份源，
#      `source_lines` 直接报全局行号，不需要模型做算术还原。
#
# v3（2026-10-27，S9 §8.5 第 1 条第 2 点）：修「quote 规则与 `_SCHEMA_EXAMPLE` 自相矛盾」——
#   示例 i4 曾标 `quote` 却带「（源此处疑似读数断字…）」这类**模型注解**，而 quote 的契约是
#   逐字可机检：注解让 content 不再是原话，机检必然报错，示例却在教反向做法。规则加一条：
#   **content 里带注解（存疑/推测/换算提示等补充说明）的条目只能 paraphrase**，要留逐字原话
#   就另起一条 quote。真实回包的 quote 机检不放松——不许为放行注解而取消机检。
#
# v4（2026-10-28，PLAN-20261028-long-source-summary N1）：换**粒度与交接协议**——
#   ① 条目以完整论证单元（`argument`）为单位，观点+依据/案例/步骤/条件/例外/数字归属同条，
#      不再「数字逐行成条」（v2 那条会制造大量细条目与关系维护成本，是 S9-B 398 条的主因）；
#   ② 单元内多个口径不同的读数走 `readings` 数组，逐项保留对象/单位/期间/基准/不确定性；
#      两个读数没有任何区分字段即「混口径」，机械校验拦下；
#   ③ 段内关系直接写进单元正文，只有必要的跨单元依赖/冲突/例外才进 `relations`；
#   ④ 分块任务不得替全篇下结论，跨块事项写 `deferred`，由全篇统稿处理。
CONTRACT_VERSION = 4

_SCHEMA_EXAMPLE = """```json
{
  "mainline": {
    "question": "源要回答的核心问题（作者视角，不替作者扩大）",
    "progression": ["推进过程按顺序 3-8 条，每条一句话"],
    "conclusions": ["作者的判断与结论，含方向与强度（确定/可能/仅单一案例）"]
  },
  "items": [
    {"id": "i1", "type": "argument", "stance": "paraphrase",
     "content": "作者用首单报价与次年涨价说明定价随交付成熟度上调；前提是客户自备货源、否则不接。",
     "source_lines": [7, 12],
     "readings": [
       {"value": "8000 元/月", "object": "首单", "unit": "元/月",
        "period": "2019 年", "basis": "月费", "certainty": "确定"},
       {"value": "12000 元/月", "object": "复购单", "unit": "元/月",
        "period": "2021 年", "basis": "月费", "certainty": "确定"}
     ]},
    {"id": "i2", "type": "viewpoint", "stance": "quote",
     "content": "原话原句（quote 时逐字，不做任何改写）",
     "source_lines": [12, 12]},
    {"id": "i3", "type": "number", "stance": "paraphrase",
     "content": "利率从4.67上涨到了4.7 一 三（源此处疑似读数断字，读数存疑，不得写成 4.713%）",
     "source_lines": [43, 43]},
    {"id": "i4", "type": "number", "stance": "quote",
     "content": "利率从4.67上涨到了4.7 一 三",
     "source_lines": [43, 43]}
  ],
  "relations": [
    {"from": "i1", "to": "i2", "kind": "supports", "note": "案例支持观点"}
  ],
  "deferred": ["跨块才看得清的指代/条件/冲突，写这里交给统稿；整篇任务通常为空"]
}
```"""


def strip_fences(text: str) -> str:
    """剥掉 ``` fences（回包/示例都可能带围栏）。

    **单一真源**：`shared/distill_dispatch.py` 也从这里 import——提炼回包与示例解析
    必须是同一套口径，两份实现会被 `scripts/pipeline_gate.py` 门禁拦下。
    """
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def example_schema() -> dict:
    """取出 `_SCHEMA_EXAMPLE`（去代码围栏后解析）供核对与测试。"""
    import json
    return json.loads(strip_fences(_SCHEMA_EXAMPLE))


def example_issues() -> list:
    """示例自身的机械自检——示例不得教反向做法。返回 issue 列表（空=自洽）。

    三个检查点（§8.5 第 1 条第 2 点）：
    1. `quote` 条目不得带模型注解（命中 `ANNOTATION_MARKERS` 就要改判 paraphrase）；
    2. 条目 id 唯一且非空、`type`/`stance` 在枚举内、`source_lines` 是升序二元组；
    3. 关系的 `from`/`to` 不悬空、`kind` 合法。
    """
    try:
        ex = example_schema()
    except Exception as exc:          # 示例连 JSON 都不是 → 直接报
        return [f"示例 JSON 解析失败：{exc}"]
    issues: list = []
    ml = ex.get("mainline") or {}
    if not any(ml.get(f) for f in ("question", "progression", "conclusions")):
        issues.append("示例主线地图为空")
    items = ex.get("items") or []
    ids = []
    for it in items:
        iid = it.get("id") or ""
        if not iid:
            issues.append("示例含无 id 条目")
            continue
        if iid in ids:
            issues.append(f"示例 id 重复：{iid}")
        ids.append(iid)
        if it.get("type") not in ITEM_TYPES:
            issues.append(f"{iid}: 示例 type 非法（{it.get('type')!r}）")
        stance = it.get("stance")
        if stance not in STANCE_KINDS:
            issues.append(f"{iid}: 示例 stance 非法（{stance!r}）")
        sl = it.get("source_lines") or []
        if (not isinstance(sl, list) or len(sl) != 2
                or not all(isinstance(x, int) for x in sl) or sl[0] > sl[1]):
            issues.append(f"{iid}: 示例 source_lines 须为升序整数二元组（{sl!r}）")
        content = str(it.get("content") or "")
        if stance == "quote" and any(m in content for m in ANNOTATION_MARKERS):
            issues.append(f"{iid}: 示例标 quote 却带模型注解 {[m for m in ANNOTATION_MARKERS if m in content]}，"
                          "按「带注解不得标 quote」规则应改 paraphrase（逐字原句另起一条 quote）")
        issues += readings_issues(f"{iid}: 示例", it.get("readings"))
    deferred = ex.get("deferred")
    if deferred is not None and not isinstance(deferred, list):
        issues.append("示例 deferred 须为字符串数组")
    for r in ex.get("relations") or []:
        if r.get("kind") not in RELATION_KINDS:
            issues.append(f"示例关系 kind 非法（{r.get('kind')!r}）")
        for side in ("from", "to"):
            ref = r.get(side)
            if ref and ref not in ids:
                issues.append(f"示例关系 {side} 引用悬空：{ref}")
    return issues


def _types_line() -> str:
    zh = {
        "argument": "完整论证单元（首选：观点+依据/案例/步骤/条件/例外/数字归属同条）",
        "viewpoint": "单独的观点/判断（含强度）", "case": "单独的案例/事件",
        "step": "单独的步骤/方法", "number": "单独的数字/口径",
        "resource": "资源/工具/链接", "condition": "条件/前提/适用边界",
        "counterexample": "反例/例外/失败",
    }
    return "、".join(f"`{k}`（{v}）" for k, v in zh.items())


def rules_text() -> str:
    """提炼契约正文（自包含三类产物定义与边界；改这里 = 换 distill_prompt_sha256）。"""
    return f"""{DISTILL_MARKER}
# 独立提炼任务（先按原文提炼，不写作）

你是**提炼者**，不是作者也不是笔记写手。任务：把下面给出的源文本提炼成结构化产物，输出**一个 JSON 对象**。提炼结果服务后续写作与复核的回源取材——**先按原文提炼**：源里有的信息不因「下游模板可能没有对应栏目」而丢掉；也不要因为「看起来像延伸」就删掉源内已有的分析、反驳、类比、方法或练习（它们是源信息，归属作者）。

## 硬边界

- **不做延伸分析**：不新增你自己的洞察、反方论证、类比、迁移建议、行动项或评价。你的产出里只应有源里讲过的东西。
- **原话与摘意区分**：`stance` 只取 `quote`（逐字原话，`content` 必须与源完全一致，不得改写）或 `paraphrase`（摘意/转述）。拿不准就写 paraphrase。
- **带注解的内容不得标 quote**：你在 `content` 里加的补充说明（「读数存疑」「疑似听错」「不得写成 4.713%」等）已经让它不是逐字原话，这类条目只能 paraphrase；确实要保留原句就**另起一条 quote**、注解那条并列为 paraphrase。机检会逐个比对 quote 内容能否在源里逐字命中，命中不了会有明确报错。
- **以完整论证单元为条（首选 `argument`）**：一条 = 一个完整观点或论证，连同它的依据、案例、步骤、条件、例外与数字归属写在同一条里。**不把一个论证拆成多条，也不为每个数字单列**。只有确实无法并入任何论证的原子信息（孤立链接、独立条件）才用其它 `type` 单列。条目数量由源的真实论证数决定，不由数字个数决定。
- **数字随单元保留、不混口径**：单元里出现多个口径不同的读数（不同对象 / 单位 / 期间 / 基准）时，用 `readings` 数组逐项列出：每项给 `value`，并至少写 `object`（对象）、`unit`（单位）、`period`（期间）、`basis`（基准）之一作为区分字段，`certainty` 标确定/存疑。**两个读数没有任何区分字段就是混口径**，机械校验会拦下。不许把多个口径并成一句含糊读数，也不许替作者挑一个写。
- **不确定读数不许补精度**：源里的断字、听不清、同一指标两说并存的读数（如 ASR 把 4.713 读成「4.7 一 三」、同一指标一会儿说 11 个点一会儿说 4.78），`content` **照抄源的原写法**并标「读数存疑」/「两说并存」——**不得换算、拼合或补写成精确值**（不许把「4.7 一 三」写成 4.713%），也不得替作者二选一。看不见、听不清的就留空并在 `notes` 说明。
- **来源位置**：每条 `source_lines` 给 1-based 行号范围 `[起, 止]`，取值就是**源文本每行开头标注的那个数字**（照抄即可，不要另行编号）。**先定位到该句所在行再写行号**，不要凭印象估算；长源靠末尾的段落尤其容易数错，**止行不得超过你收到的最后一行行号**。指向空行或越界的行号都会被机械校验拦下。
- **段内关系直接表达，跨单元只记必要关系**：因果、支撑、条件限制优先写进单元正文，不要求为每条信息建关系边。只有跨单元才成立的依赖、限制、冲突或例外，才写进 `relations`；没有就留空数组。
- **局部任务不替全篇下结论**：如果你的任务是节选（会标明「第 k/N 块（源第 a–b 行）」），只提炼本块范围。跨块才看得清的指代、条件、结论强度、相反读数或冲突，写进 `deferred`（字符串数组）交给全篇统稿；`mainline` 也只总结本块，不要把节选当全文下结论。
- **忠实属性**：说话者归属、时间、数字口径/单位/期间、亲历/转述/预测/假设、结论强度，随条目保留，不在关系里偷偷升格。
- **覆盖优先于整洁**：重要观点、论证/因果过程、案例、步骤、数字口径、资源、条件、反例都该有条目；重复表达可合并，但每处独有事实不随合并消失。
- 源没讲清、你读不到的部分：明确留空并在 `notes` 里说明，**不凭常识补齐**。

## 产物（一个 JSON 对象）

1. **主线地图**（`mainline`）：`question` 源要回答的核心问题；`progression` 推进过程（按顺序）；`conclusions` 作者的判断与结论（含强度）。节选任务只覆盖本块。
2. **信息条目**（`items`）：`type` 取 {_types_line()}。每条给 `id`（i1、i2…）、`content`、`stance`、`source_lines`；有多个读数时加 `readings`。
3. **关系说明**（`relations`）：`kind` 取 `supports`（证据/论据支持哪个观点）、`depends`（步骤/结论依赖什么前提）、`limits`（条件/例外限制什么）、`contradicts`（源内的矛盾/张力）。只记必要的跨单元关系，`from`/`to` 引用条目 `id`，不悬空。
4. **跨段交接**（`deferred`）：本任务无法定论、须由全篇统稿处理的指代/条件/冲突，字符串数组；整篇任务通常为空。

## 输出格式（只输出 JSON，不要包代码围栏外的其他文字）

{_SCHEMA_EXAMPLE}

条目数量由源的真实论证数决定：只有 3 个论证就写 3 条，不为凑数拆分，也不为每个数字单列；长源几十条也照实写。
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
                          chunk_label: str = "", start_line: int = 1,
                          core_start_line: int = 0, core_end_line: int = 0,
                          handoff: str = "") -> str:
    """渲染自包含提炼任务。material_hint：S6 材料索引的一句话提示（可空）。

    `chunk_label`：非整篇时写明「第 k/N 块」并带核心区间，让提炼者知道自己看的是节选，
    因此**不要替全篇下结论**——只提炼核心区间内的信息（§9.3.5：分段不许切断指代与条件）。
    `start_line`：下发片段在整篇里的起始行（默认 1）；行号按整篇全局编号下发。
    `core_start_line`/`core_end_line`：本块**负责提炼**的行区间；下发范围可能包含前一块
    尾部的重叠上下文，只用于消解指代，不得为它建条目（N2 核心/上下文分离）。
    `handoff`：块边界说明（重叠上下文、结构单元接续/未结束）。
    """
    lines = (source_text or "").split("\n")
    scope = f"（本任务是整篇，共 {start_line - 1 + len(lines)} 行）"
    if chunk_label:
        scope = (f"（本任务处理{chunk_label}；**只提炼核心区间，不得替全篇下结论**——"
                 "跨块才看得清的指代、条件、相反读数与冲突写进 `deferred`）")
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
    if core_start_line and core_end_line and chunk_label:
        parts += [f"**核心区间：第 {core_start_line}–{core_end_line} 行**。"
                  "只为核心区间内的内容建条目，`source_lines` 必须落在核心区间内"
                  "（机械校验会拦越界）；下发范围里的重叠上下文只用于消解指代与条件，"
                  "不要重复提炼。", ""]
    if handoff:
        parts += [f"本块边界说明：{handoff}", ""]
    if material_hint:
        parts += [f"材料索引提示：{material_hint}", ""]
    parts += [rules_text()]
    return "\n".join(parts)


def chunk_prompt_hash(title: str, source_text: str, chunk_label: str = "",
                      start_line: int = 1, core_start_line: int = 0,
                      core_end_line: int = 0, handoff: str = "") -> str:
    """分块块的 prompt 版本 hash（分块提炼时每块各记一个；含核心区间与接续说明）。"""
    return text_hash(render_distill_prompt(
        title, source_text, chunk_label=chunk_label, start_line=start_line,
        core_start_line=core_start_line, core_end_line=core_end_line, handoff=handoff))


def prompt_hash(title: str, source_text: str, material_hint: str = "") -> str:
    """提炼 prompt 版本 hash（随 pack 记录；改契约文案即换版本）。"""
    return text_hash(render_distill_prompt(title, source_text, material_hint))
