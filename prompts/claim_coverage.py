"""prompts/claim_coverage.py — A5 / T5 论点级保留度判据（报告项，PLAN-20261018 §6 A5 / §12.3 #5）

**要补的盲区**：我们唯一的量化保留度指标是「硬锚点召回」，但 1298 条语料里 **63% 的源锚点数
低于阈值**（§2.1），判据不适用 —— 也就是「源讲了一件事、笔记没讲」这件事在大多数篇目上没人管。
T5 把判据从「数字/专名」下沉到**论点句**：从源里挑出「该被保留的断言」，再看笔记有没有它的落点。

**为什么我们能直接做**（而不像 writing-studio 要绕「压缩到 1/3」）：我们手里有源，
可以直接做「源抽论点 vs 笔记查落点」的双向比对。

**判据级别**：机械半只出**报告项**（不计分、不拦盘、不触发重交），交独立审核子 Agent 裁定 ——
§3.3 判据分级 + §12.3 #5「第一阶段＝报告项，先看分布」。宿主是 A3 落盘后异步审核
（`scripts/run_async_review.py --emit` 把机械结果注入派单），**不另起队列**，规模自然等于
「评估集 + 15% 抽检」。

**失真优先**（§12.2-6）：3 条真实失真（范围泛化/方向反转/案例替换）比精度损失更危险，故除候选清单外
另出两类机械嫌疑：**笔记里的数字（含单位）不在源里**（疑编造/换算）、**方向词反转**
（源说看好、笔记说看空，且两边互不出现）。

## 实测校准（2026-10-18，评估集 30 篇）——**机械覆盖率不能当指标用**

拿机械覆盖率对 A1 手工保留率做相关（同一批 30 篇）：
bigram≥0.30 时逐篇均值 0.68、与手工保留率 **r≈0.17**；断言核命中率口径 r≈0.20。
单篇更明显反向：A27 机械 0.17 / 手工 0.83，A23 机械 0.23 / 手工 0.08。

**结论**：机械半**只能产「该核什么」的清单**，不能产分数、不能进门禁、不能用于判「笔记是否合格」。
真正低误报、可机械用的是两类失真嫌疑（源外数字 10/30 篇、方向反转 1/30 篇），它们直接对 §12.2-6
「优先修 D 类失真」。
"""
from __future__ import annotations

import re

# 候选断言的可用范围：太短没信息量、太长是一整段
MIN_LEN, MAX_LEN = 12, 140
MIN_CANDIDATES = 3          # 少于该数视为「源本身没有可挑的断言」，判据不适用（宁可不判）
MAX_CANDIDATES = 30
# 「疑似无落点」的粗筛阈值：只用来把候选缩到**少数几条**交模型核（实测该口径与手工保留率
# 相关性很弱，见模块 docstring 的校准段 —— 这是**粗筛**，不是判据）。
SUSPECT_THR = 0.30

_SENT_SPLIT = re.compile(r"(?<=[。！？；!?])|\n+")
_NUM_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%|％|亿美元|美元|美金|万亿|亿|万元|万|千|百|元|倍)")
_LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.+#-]{1,}")
# 断言特征词：出现即更可能是「作者主张」而非叙述
_CLAIM_MARKERS = ("认为", "应该", "必须", "不要", "不能", "风险", "结论", "建议", "正确", "错误",
                  "关键", "核心", "本质", "原因", "因为", "所以", "意味着", "说明", "不值得", "值得")
# 寒暄/过渡（不含断言特征）：不当作候选
_FILLER_MARKERS = ("大家好", "我们下期", "点赞", "关注", "废话不多说", "接下来我们", "先说一下")
# 方向词对：源与笔记各持一端且互不出现 → 疑方向反转
_REVERSAL_PAIRS = (("看好", "看空"), ("上涨", "下跌"), ("增加", "减少"), ("利好", "利空"),
                   ("买入", "卖出"), ("加息", "降息"), ("支持", "反对"), ("高估", "低估"))
_WS_RE = re.compile(r"\s")


def _norm(s: str) -> str:
    return _WS_RE.sub("", s or "")


def _sentences(text: str) -> list:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s and s.strip()]


def _score(sent: str) -> int:
    score = 0
    score += 2 * len(_NUM_UNIT_RE.findall(sent))
    score += len(_LATIN_RE.findall(sent))
    score += sum(1 for m in _CLAIM_MARKERS if m in sent)
    return score


def _shares(a: str, b: str, n: int) -> bool:
    """a 与 b 是否有 n 字连续共享片段（去空白后）。"""
    x, y = _norm(a), _norm(b)
    if len(x) < n or len(y) < n:
        return False
    grams = {x[i:i + n] for i in range(len(x) - n + 1)}
    return any(g in y for g in grams)


def _bigram_coverage(a: str, b_bigrams: set) -> float:
    x = _norm(a)
    bg = {x[i:i + 2] for i in range(len(x) - 1)}
    return (len(bg & b_bigrams) / len(bg)) if bg else 0.0


def claim_candidates(source: str, limit: int = MAX_CANDIDATES) -> list:
    """从源里挑「该被保留的断言」候选（机械、零模型成本）。

    口径：句长在可用区间、含断言特征（数字/专名/判断词之一）、非寒暄；按特征密度打分取前 N，
    并按 8 字共享去近重复。**取完按源内顺序返回**（便于审核者对照源上下文）。
    """
    cands = []
    for idx, sent in enumerate(_sentences(source)):
        if not (MIN_LEN <= len(_norm(sent)) <= MAX_LEN):
            continue
        if any(m in sent for m in _FILLER_MARKERS):
            continue
        sc = _score(sent)
        if sc <= 0:
            continue
        cands.append({"i": idx, "text": sent, "score": sc})
    cands.sort(key=lambda c: -c["score"])
    picked = []
    for c in cands:
        if any(_shares(c["text"], p["text"], 8) for p in picked):
            continue
        picked.append(c)
        if len(picked) >= limit:
            break
    return sorted(picked, key=lambda c: c["i"])


def claim_coverage(note: str, source: str, limit: int = MAX_CANDIDATES) -> dict:
    """源侧断言候选 vs 笔记落点。报告项：只出清单与统计，不下「笔记不合格」的结论。

    覆盖率在候选不足时判「不适用」；但**失真嫌疑独立计算**（源外数字 / 方向反转）——
    它们不依赖候选条数，漏报的代价比覆盖率大（§12.2-6）。
    """
    n_src = {m.group(1) for m in _NUM_UNIT_RE.finditer(source or "")}
    bad_nums = []
    for m in _NUM_UNIT_RE.finditer(note or ""):
        if m.group(1) not in n_src and m.group(1) not in {"0", "1", "2", "3", "100"}:
            bad_nums.append(f"{m.group(1)}{m.group(2)}")
    rev = []
    for a, b in _REVERSAL_PAIRS:
        if a in (source or "") and b in (note or "") and a not in (note or "") and b not in (source or ""):
            rev.append(f"源说「{a}」，笔记只出现「{b}」")

    cands = claim_candidates(source, limit=limit)
    note_bg = None
    if cands:
        nx = _norm(note)
        note_bg = {nx[i:i + 2] for i in range(len(nx) - 1)}
    suspect = []
    for c in cands:
        if note_bg is not None and _bigram_coverage(c["text"], note_bg) < SUSPECT_THR:
            suspect.append(c["text"])
    return {
        "applicable": len(cands) >= MIN_CANDIDATES,
        "reason": "" if len(cands) >= MIN_CANDIDATES else "源无可挑断言",
        "candidates": len(cands),
        "suspect_missed": suspect,
        "numbers_not_in_source": sorted(set(bad_nums)),
        "reversal_suspects": rev,
    }


def format_report_line(rep: dict) -> str:
    if not rep or not rep.get("applicable"):
        return ""
    line = (f"论点候选 {rep['candidates']}｜疑似无落点 {len(rep['suspect_missed'])}（待核）")
    if rep.get("numbers_not_in_source"):
        line += f"｜源外数字 {len(rep['numbers_not_in_source'])}"
    if rep.get("reversal_suspects"):
        line += f"｜疑方向反转 {len(rep['reversal_suspects'])}"
    return line


def review_reasons(rep: dict) -> list:
    """报告项 → 异步审核理由标签（不触发重交）。**不给覆盖率上分**（实测与手工保留率无关）。"""
    out = []
    if not rep or not rep.get("applicable"):
        return out
    if rep.get("suspect_missed"):
        out.append(f"claim_suspect:{len(rep['suspect_missed'])}")
    if rep.get("numbers_not_in_source"):
        out.append(f"number_not_in_source:{len(rep['numbers_not_in_source'])}")
    if rep.get("reversal_suspects"):
        out.append(f"reversal_suspect:{len(rep['reversal_suspects'])}")
    return out


def render_review_block(rep: dict) -> str:
    """给审核子 Agent 的机械前置块：把「该核哪些断言」变成具体条目（先机械后语义）。

    ⚠️ 机械结果**不是结论**：同义改写会误报成未命中（实测机械粗筛与手工保留率几乎不相关），
    所以这里只提供候选与嫌疑，判定权在审者。
    """
    if not rep or not rep.get("applicable"):
        return ""
    L = ["## 机械前置（A5 论点级判据，**待你裁定**，机械结果不是结论）", "",
         f"- 源侧断言候选 {rep['candidates']} 条；下列 {len(rep['suspect_missed'])} 条**机械疑似无落点**。",
         "- 机械粗筛与人工判定相关性很弱（同义改写会误报），所以请**逐条在笔记里找**："
         "确认同义改写（→ 误报，忽略）还是真丢（→ 写进 `issues`）。真丢才是缺陷。", ""]
    if rep["suspect_missed"]:
        L += [f"{i}. {t}" for i, t in enumerate(rep["suspect_missed"], 1)]
    else:
        L.append("（无）")
    if rep.get("numbers_not_in_source"):
        L += ["", "**笔记里出现、但源里找不到的数字（疑编造/换算，须核口径）**：",
              "- " + "、".join(rep["numbers_not_in_source"])]
    if rep.get("reversal_suspects"):
        L += ["", "**疑方向反转（源与笔记各执一端）**："] + [f"- {t}" for t in rep["reversal_suspects"]]
    return "\n".join(L) + "\n"
