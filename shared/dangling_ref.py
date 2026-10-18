"""shared/dangling_ref.py — 段落首悬空指代检测（机械 · 只读 · 零 AI）

来源：`docs/plans/PLAN-20261018-knowledge-layer.md` §4 / §6 A7（T4）。

为什么只查「机器可读性类」风格问题：我们的下游（writing-studio KB）按**段落**检索。
段落写成「这个方法能省一半时间」——孤立看就是残缺事实；下游拿到的不是一篇笔记，
是这一个片段。**悬空指代因此对下游是功能性缺陷**，不是文风偏好。

而「人味类」（破折号密度、翻案腔、长短句参差）我们明确不在意，且改写会丢限定词（§7）。

## 判据边界（踩过的教训不许重演）

本项目此前有两次机械信号翻车：括号密度假阳性 63.2%、字数比例带被证伪。故本模块：

- **只看「段落首」**——段中出现代词通常能在本段内找到先行词，不会被孤立切成片段。
- **只用白名单短语**，不用「出现代词即算」这类宽泛正则（`这/那` 单字几乎必然误报）。
- 结构性行（标题、标签行、表格、代码围栏、图片、来源链接）**一律不参与**。
- 区分 `para`（段首）与 `item`（列表项）两类，列表项可能由上一行引导，先分开统计，
  看完实际分布再决定要不要计入。

**本模块只统计，不判定、不拦截。** §3.3 的三级判据里它属于「报告项」：
先跑全库看分布，命中率高才考虑生成端预防（在 prompt 里加一条约束），
命中率低就证明我们在解决一个不存在的问题。
## 检索单元到底多大？（2026-10-18 读下游源码核实）

writing-studio `src/lib/knowledgePassages.ts:splitKnowledgePassages`：**先按行切**，
再把连续行打包成 ≤1200 字符的块，检索命中时额外补前后各 200 字符邻文。
所以：

- 真正的「孤立」风险在**段落/块首**（前面的 200 字符不一定够给出先行词）。
- 行首落在块中间时通常能被邻文救回，故行级命中只作辅助观察，**立项与否以 block 为准**。
- 没有空行的整篇笔记会被算成一个 block，故 block 计数偏保守（宁少不宁多）。
"""
from __future__ import annotations

import collections
import re

# ---------------------------------------------------------------- 白名单：段首悬空指代

# 每条 pattern 必须锚定到**开头**，且语义上「离开上文就不知道指什么」。
_PATTERN_SRC = (
    # ① 「这/那 + 量词/名词」型：指代的载体在上一段
    (r"这个(?:方法|做法|办法|方式|形式|逻辑|思路|策略|模式|机制|过程|流程|阶段|步骤|操作|动作|"
     r"问题|现象|情况|状态|事实|结论|观点|判断|推断|说法|结论|结果|效应|效果|收益|代价|成本|"
     r"数字|数据|比例|结构|关系|联系|差异|区别|套路|招|点|一点|方面|环节|部分|东西|事情|说法|"
     r"一类|两类|三类|四类|五类|两个|三个|四个|几个|几位|几种|)"
     , "指示名词"),
    (r"那种|那种情况|那样|那这样", "远指代词"),
    (r"这种(?:情况|现象|做法|方法|逻辑|思路|状态|感觉|人|事|东西|关系|市场|内容|说法|结论|结果)",
     "指示名词"),
    (r"这一(?:点|套|步|整套|部分|环节|阶段|层|类|整套逻辑|整套做法)", "指示名词"),
    (r"这些(?:情况|做法|方法|内容|数据|数字|结论|东西|变化|操作|工具|案例|建议|动作|要素)?",
     "指示名词"),
    (r"那些(?:人|事|情况|内容|东西|方法|做法|结论)?", "指示名词"),
    (r"这类(?:人|事|情况|内容|问题|东西|方式|说法|产品|账号|玩家|公司|内容)?", "指示名词"),
    (r"此类|此类情况|其他此类", "指示名词"),
    # ② 人称代词开头：段外才有先行词
    (r"(?:他|她|它|他们|她们|它们)(?:的|们|会|还|也|就|并|在|说|做|想|认为|觉得|把|给|对|与|和|以及)?",
     "人称代词"),
    # ③ 明确的回指短语
    (r"后者|前者|后者们", "回指短语"),
    (r"如上(?:所述|文|图|表)?|如上文所(?:述|提)|如前所述|前面提到|如前所提", "回指短语"),
    (r"同样的(?:道理|逻辑|情况|做法|问题)|同理(?:，|,)?|同样的",
     "回指短语"),
    (r"基于此|在此基础上|在此之上|据此|由此出发|以此为(?:基础|前提|起点)", "承接短语"),
    (r"这就是为什么|这也是(?:为什么)?", "承接短语"),
    (r"有了(?:这个|这些|上述|上面|它|他|她)(?:，|,)?", "承接短语"),
    # ④ 「上层 foo」型，常见于分点笔记，离开上层标题就不明所指
    (r"(?:上面|上文|前文|上述|以上)(?:提到|说的|讲的|列举|列出|说的这些)?(?:的)?"
     r"(?:内容|方法|问题|结论|几点|这些)?", "上层指代"),
)

_PATTERNS = [(re.compile("^(?:" + pat + ")"), kind) for pat, kind in _PATTERN_SRC]

# 结构性行：不参与风格统计
_SKIP_LINE = re.compile(
    r"^\s*(?:"
    r"#{1,6}\s"                       # 标题
    r"|[-*+]\s\[[ xX]\]"              # 任务列表
    r"|[-*+]\s"                       # 列表标记本身（内容另行判断）
    r"|\d+\.\s"                       # 有序列表
    r"|>"                             # 引用/callout
    r"|\|"                            # 表格
    r"|!\["                           # 图片
    r"|```"                           # 代码围栏
    r"|\[!?[A-Za-z]"                  # Obsidian callout/嵌入
    r")"
)
# 系统权威追加区（与 content_signals.strip_note 同口径，另补链接/链接行）
_TAGLINE_RE = re.compile(r"^\s*#[^\n]*\n")
_SRCLINK_RE = re.compile(r"^.*来源链接.*$\n?", re.M)
_FRONT_RE = re.compile(r"^---\n.*?\n---\n", re.S)
_CODE_RE = re.compile(r"```.*?```", re.S)

MIN_PARA_WORDS = 8          # 过短的过渡段不代表「不可自包含」，不参与统计


def clean_note_body(raw: str) -> str:
    """去掉 frontmatter、代码块、标签行、来源链接行 → 只留可能进入检索的正文。"""
    body = _FRONT_RE.sub("", raw or "", count=1)
    body = _CODE_RE.sub("", body)
    body = _TAGLINE_RE.sub("", body, count=1)
    return _SRCLINK_RE.sub("", body)


def _strip_markers(line: str) -> str:
    """去掉列表符号 / 粗体标记，取真正的内容开头。"""
    line = re.sub(r"^\s*[-*+]\s+", "", line)
    line = re.sub(r"^\s*\d+\.\s+", "", line)
    return line.strip().lstrip("*").strip()


def hit_kind(line: str) -> str:
    """判断一行是否以悬空指代开头，返回类别（无则 ""）。"""
    text = _strip_markers(line)
    if not text:
        return ""
    for rx, kind in _PATTERNS:
        if rx.match(text):
            return kind
    return ""


def scan_lines(raw: str) -> dict:
    """行级扫描（辅助观察）：任何内容行以悬空指代开头都计数。

    ⚠️ 这不是主判据 —— 下游会把连续行打包成 ~1200 字符的块并补前后邻文，
    位于块中间的行首通常能被上下文救回。只有当 block 级也偏高时才有意义。
    """
    from prompts import content_signals as CS

    body = clean_note_body(raw)
    hits, counted = [], 0
    for i, line in enumerate(body.splitlines(), 1):
        if not line.strip() or _SKIP_LINE.match(line):
            continue
        if len(CS.nows(_strip_markers(line))) < MIN_PARA_WORDS:
            continue
        counted += 1
        kind = hit_kind(line)
        if kind:
            hits.append({"line": i, "kind": kind, "text": CS.nows(_strip_markers(line))[:60]})
    return {"lines": counted, "hits": hits}


def scan_note(raw: str) -> dict:
    """扫单篇笔记（block 级为主判据，同时附带行级观察）。

    Returns {paragraphs, hits[{line_block,kind,text}], by_kind{}, line_scan:{...}}
    只取**block 首行**；列表块首项标 `item`，与其它类型分列统计。
    """
    from prompts import content_signals as CS

    body = clean_note_body(raw)
    hits, by_kind = [], collections.Counter()
    paragraphs = 0
    line_no = 0
    for block in re.split(r"\n\s*\n", body):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        first = lines[0]
        if _SKIP_LINE.match(first) and not first.lstrip().startswith(("-", "*", "+")):
            continue        # 结构性块（表格/标题/引用…）整体跳过
        content = CS.nows(first)
        if len(content) < MIN_PARA_WORDS:
            continue
        paragraphs += 1
        line_no += 1
        kind = hit_kind(first)
        if kind:
            # 列表项 vs 普通段落首行
            k = "item" if re.match(r"^\s*[-*+]\s+", first) else "para"
            hits.append({"line_block": line_no, "kind": f"{kind}/{k}",
                         "text": content[:60]})
            by_kind[f"{kind}/{k}"] += 1
    return {"paragraphs": paragraphs, "hits": hits, "by_kind": dict(by_kind),
            "line_scan": scan_lines(raw)}


def aggregate(results: list) -> dict:
    """汇总多篇 → 全局分布与 per-note 排序。"""
    total_paras = sum(r["paragraphs"] for r in results)
    total_hits = sum(len(r["hits"]) for r in results)
    notes_with = [r for r in results if r["hits"]]
    by_kind = collections.Counter()
    for r in results:
        for h in r["hits"]:
            by_kind[h["kind"]] += 1
    hit_rates = [len(r["hits"]) / r["paragraphs"] for r in results if r["paragraphs"]]
    # 行级（辅助观察，不用于立项判断，理由见模块 docstring）
    total_lines = sum((r.get("line_scan") or {}).get("lines", 0) for r in results)
    line_hits = sum(len((r.get("line_scan") or {}).get("hits", [])) for r in results)
    return {
        "notes": len(results),
        "paragraphs": total_paras,
        "hits": total_hits,
        "notes_with_hits": len(notes_with),
        "paras_with_hits_pct": round(total_hits / total_paras, 4) if total_paras else 0.0,
        "notes_with_hits_pct": round(len(notes_with) / len(results), 4) if results else 0.0,
        "mean_per_note_rate": round(sum(hit_rates) / len(hit_rates), 4) if hit_rates else 0.0,
        "by_kind": dict(by_kind.most_common()),
        "line_scan": {
            "lines": total_lines,
            "hits": line_hits,
            "rate": round(line_hits / total_lines, 4) if total_lines else 0.0,
        },
    }


def verdict(agg: dict, live_threshold: float = 0.02) -> str:
    """给一句结论：命中率是否高到值得立项（§4：低就是在解决不存在的问题）。

    阈值默认 2% —— 低于它说明「段落不自包含」在我们的语料里不成系统性问题。
    """
    rate = agg["paras_with_hits_pct"]
    if rate >= live_threshold:
        return (f"段落命中率 {rate:.2%} ≥ {live_threshold:.0%}：够得上立项，"
                f"但只能走生成端预防（prompt 加一条约束），不建独立改写轮。")
    return (f"段落命中率 {rate:.2%} < {live_threshold:.0%}：现有语料里不成系统性问题，"
            f"不建议为它改 prompt 或建任何改写环节（§7）。")
