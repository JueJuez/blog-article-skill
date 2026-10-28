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

DISTILL_MARKER = "<!-- DISTILL_CONTRACT v6 -->"

# 与 shared/distill_pack.py 的枚举保持一致（单一真源在这里，pack 侧 import）
# `argument` 是 v4 起的**首选**粒度：一条 = 一个完整观点/论证，连同依据、案例、步骤、
# 条件、例外与数字归属；其余类型用于确实无法并入论证的原子信息。
ITEM_TYPES = ("argument", "viewpoint", "case", "step", "number", "resource",
              "condition", "counterexample")
# v6 三档选材权（单一真源，pack 侧 import）：
# trunk=主干（该节点用意成立所必需，必落点）；support=支撑材料（写作端按主线取舍）；
# drop=默认丢（重复表述、元信息、过程叙述、寒暄、与主线无关的岔题）。
ITEM_TIERS = ("trunk", "support", "drop")
STANCE_KINDS = ("source", "quote", "paraphrase")
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
# v4（2026-10-28，历史记录，已由 v5 取代）：完整论证单元 + `readings` 分列口径。
#   实测（N4）粒度确实降本，但模型转述仍会改数字归属/时序，且合并掉兼职/全职口径；
#   结论是不保留，`readings` 载体在 v5 退场。
# v5（N6）：模型仅返回位置/主题/关联/疑点；程序从同版源生成正文。
#   `readings_issues` 仅保留给 v4 旧 pack 的离线解读与示例自检，v5 回包不接受 readings。
# v6（2026-10-30，PLAN-20261030-intent-tree-summary）：意图树契约——
#   ① `mainline` 并入 `hierarchy` 树根（根 intent 可空，纯话题拼盘没有全局主线）；
#   ② `items` 挂 `node_id` 与 `tier` 三档（trunk/support/drop），把选材权还给摘要；
#   ③ 树形随源形态自适应：有显式章节用章节树，口播/直播按话题分段（回跳合并，
#      非连续区间照实分列；话题=有独立用意的最小单元且有推进，防碎化）；
#   ④ 唯一降级出口 `no_substance`（纯寒暄/无实质内容），「无结构」不降级；
#   ⑤ 忠实度边界：intent 是笔记者对作者用意的推断，只用于组织选材，不进稿。
CONTRACT_VERSION = 6

_SCHEMA_EXAMPLE = """```json
{
  "hierarchy": {
    "intent": "",
    "children": [
      {"id": "n1", "label": "报价与交付条件", "intent": "先讲清接单的价格形成机制，为后文涨价铺垫",
       "source_spans": [[7, 12]], "children": []},
      {"id": "n2", "label": "适用边界", "intent": "划定方法成立的前提，防止读者照搬",
       "source_spans": [[25, 27]], "children": [
         {"id": "n2a", "label": "自备货源前提", "intent": "说明该模式的适用对象",
          "source_spans": [[25, 26]], "children": []}
       ]}
    ]
  },
  "items": [
    {"id": "i1", "type": "argument", "tier": "trunk", "node_id": "n1", "label": "报价与交付条件",
     "source_spans": [[7, 12], [18, 20]],
     "notes": "前后片段共同说明条件，非连续原文须分别标界；话题中途跳回也归同节点"},
    {"id": "i2", "type": "condition", "tier": "support", "node_id": "n2a", "label": "适用边界",
     "source_spans": [[25, 27]],
     "notes": "此处读数含混，保留原写法，不确定处按需回查"},
    {"id": "i3", "type": "case", "tier": "drop", "node_id": "n2", "label": "开场寒暄",
     "source_spans": [[3, 3]], "notes": "纯寒暄过程叙述，默认丢"}
  ],
  "relations": [{"from": "i2", "to": "i1", "kind": "limits", "note": "适用条件"}],
  "deferred": []
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


def _node_issues(nodes: list, prefix: str, source_text: str,
                 seen_ids: set) -> list:
    """hierarchy 节点的机械自检（示例与真实回包共用口径——单一真源）。

    - 节点 id 唯一且非空；非根节点 `intent` 必填（填不出用意的不配成节点）；
    - `source_spans` 照素材口径校验（越界/倒序/空行即报）；
    - 子节点递归。
    """
    issues: list = []
    for k, node in enumerate(nodes or []):
        if not isinstance(node, dict):
            issues.append(f"{prefix}children[{k}] 须为对象")
            continue
        nid = str(node.get("id") or "")
        if not nid:
            issues.append(f"{prefix}children[{k}] 缺少 id")
        elif nid in seen_ids:
            issues.append(f"{prefix}{nid}: 节点 id 重复")
        seen_ids.add(nid)
        if not str(node.get("intent") or "").strip():
            issues.append(f"{prefix}{nid or k + 1}: 节点缺 intent（作者用意一句话，必填）")
        issues += [f"{prefix}{nid or k + 1}: {m}"
                   for m in (source_spans_issues(node.get("source_spans"), source_text)
                             if source_text is not None else [])]
        issues += _node_issues(node.get("children") or [], f"{prefix}{nid}.",
                               source_text, seen_ids)
    return issues


def source_spans_issues(spans, source_text: str) -> list:
    """提炼回包侧的 span 形状/越界校验（无源文本时只查形状）。

    与 `shared.distill_pack.source_span_issues` 的分工：本函数在**没有源文本**也可用
    （示例自检/契约测试），有源文本时逐条给出与 pack 侧一致的判定。
    """
    if not isinstance(spans, list) or not spans:
        return ["source_spans 缺失或为空"]
    issues = []
    for span in spans:
        if (not isinstance(span, list) or len(span) != 2
                or not all(type(x) is int for x in span)):
            issues.append("source_spans 须为 [起,止] 整数行区间")
            continue
        a, b = span
        if a < 1 or a > b:
            issues.append(f"原文位置越界或倒序：{span}")
        elif source_text is not None and b > len((source_text or "").split("\n")):
            issues.append(f"原文位置越界或倒序：{span}")
    return issues


def example_issues() -> list:
    """示例自身的机械自检——示例不得教反向做法。返回 issue 列表（空=自洽）。

    检查点（§8.5 第 1 条第 2 点，v6 口径）：
    1. `quote` 条目不得带模型注解（命中 `ANNOTATION_MARKERS` 就要改判 paraphrase）；
    2. 条目 id 唯一且非空、`type`/`tier` 在枚举内、`source_spans` 形状合法；
    3. 关系的 `from`/`to` 不悬空、`kind` 合法；
    4. hierarchy 节点 id 唯一、非根节点 intent 必填、节点 span 形状合法；
    5. 条目 `node_id` 引用存在；trunk 至少一条。
    """
    try:
        ex = example_schema()
    except Exception as exc:          # 示例连 JSON 都不是 → 直接报
        return [f"示例 JSON 解析失败：{exc}"]
    issues: list = []
    hier = ex.get("hierarchy")
    if not isinstance(hier, dict):
        issues.append("示例缺 hierarchy（v6 树形为必填产物）")
        node_ids: set = set()
    else:
        if "intent" not in hier:
            issues.append("示例 hierarchy 根缺 intent 键（根 intent 可空但键必须在）")
        node_ids = set()
        issues += _node_issues(hier.get("children") or [], "示例", None, node_ids)
    items = ex.get("items") or []
    ids = []
    trunk_count = 0
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
        if it.get("tier") not in ITEM_TIERS:
            issues.append(f"{iid}: 示例 tier 非法（{it.get('tier')!r}，须 {ITEM_TIERS}）")
        elif it.get("tier") == "trunk":
            trunk_count += 1
        nid = it.get("node_id")
        if not nid:
            issues.append(f"{iid}: 示例条目缺 node_id（v6 条目必须挂节点）")
        elif nid not in node_ids:
            issues.append(f"{iid}: 示例 node_id 引用悬空：{nid}")
        spans = it.get("source_spans")
        if (not isinstance(spans, list) or not spans
                or any(not isinstance(x, list) or len(x) != 2
                       or not all(isinstance(v, int) for v in x) or x[0] > x[1]
                       for x in spans)):
            issues.append(f"{iid}: 示例 source_spans 须为升序整数行区间列表（{spans!r}）")
        content = str(it.get("content") or "")
        if stance_marker_in(content):
            issues.append(f"{iid}: 示例正文含模型注解（v6 条目不提交 content，注解写 notes）")
    if items and not trunk_count:
        issues.append("示例无 trunk 条目（主干为空不合法）")
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


def stance_marker_in(content: str) -> bool:
    """content 里是否带模型注解（v6 起条目不提交 content，此检查仅用于示例自检）。"""
    return any(m in (content or "") for m in ANNOTATION_MARKERS)


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
    """提炼契约正文（自包含产物定义与边界；改这里 = 换 distill_prompt_sha256）。"""
    return f"""{DISTILL_MARKER}
# 独立提炼任务：意图树选材与编排（契约 v6）

你是素材选取者。通读本任务的全部源文本，先给源搭一棵**意图树**（作者为什么写、按什么
结构推进），再把原文素材挂到树上并分档。输出一个 JSON 对象，**不输出素材正文**、
不逐条重述原文。素材正文**由程序**按你提交的 `source_spans` 从同版源提取。

## 意图树（hierarchy）

- **树形随源形态自适应**：有显式章节的源用「章节 → 小节 → 节点」；没有显式章节的
  口播/直播/随笔用**话题分段**——聊到哪说到哪，每个话题一个节点，树退化为话题列表。
- **话题的判定**：有独立用意的最小单元，且对前文有推进；纯承接过渡、寒暄转场不算话题。
- 每个节点一句 `intent`：作者在这一层的**用意/目的**（如「用自身踩坑建立可信度」）。
  这是笔记者的推断，只用于组织选材，**不进稿、不得写成作者的明确主张**。
- 根节点 `intent` 可空（纯话题拼盘的源没有全局主线）；**其余节点 intent 必填**——
  填不出用意的内容不配成节点。
- **话题回跳**（A→B→A′）：同一话题的两段归同一个节点，`source_spans` 照实分列
  非连续区间，不伪装成连续原话。
- 节点的 `source_spans` 是该节点覆盖的源行区间（1-based，照抄行号，升序）。

## 素材条目（items）与三档选材权

- 每条素材挂 `node_id`（指向树上的节点），并标 `tier` 三档：
  - `trunk`：该节点用意成立所必需的**主干**内容（核心论点/方法/结论级），写作端必落点；
  - `support`：支撑材料（案例、数字、步骤细节），写作端按主线取舍，丢了不需理由；
  - `drop`：**默认丢**——重复表述、元信息（点赞/排版/「本文共」类）、过程叙述、寒暄、
    与主线无关的岔题。标 drop 不删除原文位置，只是告诉写作端不写。
- **完整论证单元，不为每个数字单列**：同一观点及必要依据、案例、条件归成一项；
  素材可跨几个自然段，但必须能独立理解。主线外有独立价值的信息也保留。
- **不按压缩率或条数砍主干**：trunk 该有就有；support/drop 该丢就丢，丢得有意。
- 条目粒度与硬边界沿用：完整论证单元（`{_types_line()}`）、`label` 简短、`notes` 只写
  定位疑点/ASR 不确定性，不带注解的正文要求；不确定读数不补精度、不混口径。
- **忠实度边界**：条目正文逐字取自同版源（程序提取）；intent/label 只是导航与用意
  推断，不是作者事实。

## 无实质内容出口（唯一降级出口）

- **「无结构」不降级**：口播/直播没有章节，照样按话题分段建树。
- 只有当全文**确实没有实质内容**（纯寒暄、纯流程性问候、无任何观点/方法/事实）时，
  才输出 `{{"no_substance": true, "reason": "一句话说明为何无实质内容"}}`，不硬造树。
  只要有任何实质内容，就必须正常输出树与条目。

## 硬边界（沿用 v5，逐条不变）

- **原文位置**：`source_spans` 是一组 1-based `[起行,止行]`，照抄源行号，升序、互不重叠。
  连续内容用一个区间；不连续内容各列区间（话题回跳也如此）。选中指代、条件、例外、
  数字对象/单位/期间/基准所需上下文，不能只摘结论或数字。
- **只选取，不改写**：不提交 `content` 或结构化读数。保留“通常”“如”“至少”等限定；
  全职/兼职、薪酬/工作价值等不同口径不可合并——不混口径靠保留能说明口径的原文。
- **导航与原文分开**：`label` 仅写简短主题；`notes` 只写定位疑点、需要回查的指代或
  ASR 不确定性，不写新的事实结论。
- **范围与覆盖分开**：处理完整源不等于素材无遗漏；先检查每个有独立价值的部分是否有
  位置，再决定 trunk/support/drop。信息不足如实留疑点，不补外部知识。
- **必要关联**：只在必要时标 `relations`，`from/to` 是素材 id；没有关系留空。

## 其他产物

- `relations`：必要的 supports/depends/limits/contradicts 关系；没有就空数组。
- `deferred`：必须留到其他块或统稿处理的事项；没有就空数组。
- 分块任务只为本块核心区间建条目，不替全篇下结论；跨块事项写 `deferred`。

只输出 JSON；不把原文再次抄写到回包中：
{_SCHEMA_EXAMPLE}
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
        "你填写 `source_spans` 时直接照抄这个数字，不要自己重新编号或估算：",
        "",
        "<<<SOURCE_BEGIN>>>",
        numbered_source(source_text or "", start_line),
        "<<<SOURCE_END>>>",
        "",
    ]
    if core_start_line and core_end_line and chunk_label:
        parts += [f"**核心区间：第 {core_start_line}–{core_end_line} 行**。"
                  "只为核心区间内的内容建条目，`source_spans` 必须落在核心区间内"
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
