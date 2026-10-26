"""prompts/extract_checklist.py — T15 长源提取清单（A4「清单内化」，PLAN-20261018 §6 A4 / §12.3 #11-a）

**要解决的问题**：A1 基线的 52 条丢失里 27% 是「整块信息未进笔记」，集中在长源压缩取舍与
「无落点」的散落信息点。提示词已写明「硬事实必须带走」，仍丢 —— 缺的不是指令，是**清单对账**。
T15 把「要保留的东西」变成逐条可核对的清单，T6 让它先于成文（先列要点、再成文）。

**为什么内化进条目 prompt（#11-a）**：主路径没有外部 provider（`FORCE_AGENT_MODE=1`），
生成由执行模型按条目 prompt 完成，没有「1 次低温调用」可插。故清单指令按源长条件拼入 prompt，
执行模型在同一次输出里先列清单、再成文；代码侧把清单**剥离出正文**、逐条机械校验「原句能在源里
命中」，命中不了即丢弃（机械可验），再对成文做机械对账。

**为什么每条必须带 `‖原句`**：§3.3 第 1 步的验收是「每条必须能在源里原样命中」。只判
「清单像不像源」等于没有判据；把原句带进来才能机械判。

**清单是检查表，不是大纲**（`content_signals.py` 注释已记此坑）：清单只用于覆盖自查，
正文仍按模板类型组织 —— prompt 里明写，防「按清单顺序扩写」驱动编造。

**判据级别**：本模块产出的一切都是**报告项**（§3.3 判据分级）——不计分、不拦盘、不触发重交；
未命中项随落盘 meta 存档并入异步审核队列观察（§12.3 #13）。
"""
from __future__ import annotations

import json
import os
import re
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE_DIR = os.path.join(_ROOT, "notes", "_meta", "extract_checklists")

SCHEMA_VERSION = 1
# 触发阈值：源长 > 该值才启用清单（§2.1 数据支撑 + §12.1 「悬崖在 10-20k」）；<= 时 prompt 不拼入
EXTRACT_MIN_CHARS = 10000
# T0「源撑不起」事前判断的靶：0-2k 桶失控率 18%（明显高于 2-5k 的 5.4%），只对该档给报告钩
THIN_MAX_CHARS = 2000
# 与 §3.3 第 1 步四分类对齐，另加 §12.2 定的「散落信息点」「流程内推广」
CATEGORIES = ("数字", "专名", "引述", "亲历", "判断", "假设", "论点", "散落", "推广")
SOURCE_THIN_TAG = "SOURCE_THIN"

BEGIN = "<!-- EXTRACT_CHECKLIST -->"
END = "<!-- /EXTRACT_CHECKLIST -->"

_ITEM_RE = re.compile(r"^\s*[-*]\s*\[(?P<cat>[^\[\]]{1,16})\]\s*(?P<rest>\S.*?)\s*$")
_WS_RE = re.compile(r"\s")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
_LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.+#-]{1,}")
_ELLIPSIS_RE = re.compile(r"(?:\.{2,}|…+)")
_JUNK_NUMS = {"666", "6666", "888", "8888", "999", "9999", "123", "1234", "520", "111", "222", "333"}
_EN_STOP = {
    "the", "and", "for", "with", "you", "are", "but", "not", "all", "can", "has", "was", "our",
    "out", "one", "new", "get", "how", "why", "what", "when", "this", "that", "from", "they",
    "have", "will", "your", "his", "her", "its", "about", "into", "over", "more", "some", "than",
    "then", "them", "were", "been", "said", "does", "did", "just", "only", "also", "very", "much",
    "app", "web", "pro", "max", "min", "top", "api", "url", "id", "ai", "ok", "okay",
}


def _norm(s: str) -> str:
    """只去空白（保留标点/数字原样），与 content_signals.nows 同口径。"""
    return _WS_RE.sub("", s or "")


# ---------------------------------------------------------------- ① prompt 侧

def rules_text() -> str:
    """清单块的两阶段指令（版本受 `prompt_sha256` 追踪，改这里等于换清单版本）。"""
    cats = "、".join(f"`{c}`" for c in CATEGORIES)
    return (
        "# 附：长源提取清单（两阶段 · 本篇源长超过 "
        f"{EXTRACT_MIN_CHARS} 字才启用）\n\n"
        "本篇源较长，一次成文容易在压缩中丢信息。**先列清单，再成文**，两阶段在同一次输出里完成。\n\n"
        "## 阶段一 · 列清单（放在正文最前面）\n"
        f"在 `{BEGIN}` 与 `{END}` 两行之间逐条列出源里「丢了不行」的东西。每条格式固定为：\n\n"
        "```\n- [类别] 一句话陈述 ‖ 源里对应的原句片段\n```\n\n"
        f"- 类别只用这几个：{cats}。\n"
        "  - `数字`（金额/百分比/年份/倍数**及口径**）、`专名`（公司/产品/人名/工具/平台）、"
        "`引述`（值得保留的原话）、`亲历`（作者亲历与自述依据）、`判断`（作者的判断/推断/结论，"
        "**含方向与强度**）、`假设`（源里明说的假设、举例）、`论点`（主线论点）、"
        "`散落`（不在主线但可能有帮助的独立信息点：持仓/个股点评/人物评价/时事等）、"
        "`推广`（嵌在流程/SOP 步骤里的推广或引流；**孤立的纯推广不列**）。\n"
        "  - **容易漏的三类，专门交代**：① 作者的**推理机制/因果链**（不只记结论——"
        "「为什么会导致什么、什么时候会被压回来」这类链条也要列，归 `论点`/`判断`）；"
        "② 作者对自己说法的**限定与未验证声明**（「这个我没验」「这里估不准」「本期不支持」"
        "——归 `判断`，这类是方向/可信度的边界，丢了最危险）；"
        "③ 作者对**非主线行业/公司/时事**的点评，哪怕只有一两句（归 `散落`）。\n"
        "- `‖` 后面必须是**从源里原样拷贝**的片段（不改写、不翻译、不补字、不合并两处）——"
        "它会被机械校验，命中不了源的条目会被丢弃；片段要覆盖这条的关键数字与专名。\n"
        "- 覆盖优先：拿不准算不算，先列。建议 10～40 条，宁多勿漏；但**源里没有的一个字都不许写进来**。\n\n"
        "## 阶段二 · 成文（清单之后）\n"
        "- 正文按本篇模板类型正常组织，**不要按清单顺序写**，也不要把清单当大纲扩写 —— 清单只是自查表。\n"
        "- 逐条核对：清单里每条都要在正文里有落点（数字带口径、专名不换泛称、条件/反例/方向强度不丢）。\n"
        "- ⚠️ **落点 ≠ 逐条照搬**：清单只用来查「有没有丢」，正文该合并的合并、该按模板重组的重组；"
        "禁止为「每条都有落点」把多条塞进一句、或把正文写成条目流水账 —— "
        "**单句仍不超过 140 字、段落仍要自包含**（下游按段落检索，压碎句与超长段等于没写）。\n"
        "- ⚠️ **写长不是目标**：覆盖指「源里的信息都在」，不是「把清单抄进正文」。同一件事讲一次就够，"
        "不要「清单一条 + 正文一段」地讲两遍。\n"
        "- 正文里**不许出现**上面的清单标记或清单本身（系统会剥离并单独存档）。\n"
        f"- 若源确实撑不起本篇模板所需的必备模块（例如极短/极空的一集），在清单最前面加一行 "
        f"`- [{SOURCE_THIN_TAG}] <缺什么>`，正文只写源支持的，不补骨架。\n"
    )


def thin_rules_text() -> str:
    """T0 短源「源撑不起」事前判断（报告项 · §12.3 #3）：只观察，不拦盘。

    现状对「源撑不起却硬写」只有**事后**兜底（`MIN_WORDS<300` + 失控上限）；本块要的是**事前**
    信号 —— 模型自报「这个源撑不起」，好让我们把它与事后失控率对上（0-2k 桶 18%）。
    """
    return (
        f"# 附：源量事前判断（本篇源长不足 {THIN_MAX_CHARS} 字）\n\n"
        "- 成文前先判断：这个源撑得起本篇模板所需的必备模块吗？\n"
        "- 撑不起就**只写源支持的**：允许模块缺失、允许成稿很短；"
        "**不许补骨架、不许注水、不许把一句话铺成一段**，也**不许**因为源短就推说「内容不足」而少写源里确实有的东西。\n"
        f"- **只有判断为「撑不起」时**，在正文最前面加这个块（够写就完全不出现）：\n\n"
        f"{BEGIN}\n"
        f"- [{SOURCE_THIN_TAG}] <撑不起的是哪些模块/为什么>\n"
        f"{END}\n\n"
        "（系统会剥离并存档，正文里不要重复）\n"
    )


# 薄清单只列这几类：漏了就失真，且不至于把正文写成条目流水账
MID_CATEGORIES = ("判断", "步骤", "条件", "数字", "专名")
THIN_CHECKLIST_ENV = "NOTE_THIN_CHECKLIST"


def thin_checklist_enabled() -> bool:
    """实验开关 `NOTE_THIN_CHECKLIST`（默认关；只影响 2k–10k 这一档）。"""
    raw = (os.environ.get(THIN_CHECKLIST_ENV) or "0").strip().lower()
    return raw in ("1", "on", "true", "yes")


def mid_thin_rules_text() -> str:
    """S2 中短源薄清单（PLAN-20261026 §4）：2k–10k 档的**受控试验块**，默认不注入。

    与长源清单的区别只在「要列多少、列什么」：长源清单要 10～40 条覆盖整篇，
    薄清单只要 6～15 条、只列「漏了会改变结论或条件」的点 —— 中短源的失败模式是
    「丢一条判断/一个例外」，不是「整块没进笔记」，清单太长反而会把正文写成流水账。
    剥离、原句机检、对账全部复用长源那套（同一对 `BEGIN/END` 标记）。
    """
    cats = "、".join(f"`{c}`" for c in MID_CATEGORIES)
    return (
        f"# 附：中短源薄清单（两阶段 · 本篇源长 {THIN_MAX_CHARS}–{EXTRACT_MIN_CHARS} 字）\n\n"
        "本篇源不算长，但一次成文仍可能丢掉关键判断与条件。**先列薄清单，再成文**，同一次输出完成。\n\n"
        "## 阶段一 · 列薄清单（放在正文最前面）\n"
        f"在 `{BEGIN}` 与 `{END}` 之间逐条列出**漏了会改变结论或条件**的东西，只列 6～15 条：\n\n"
        "```\n- [类别] 一句话陈述 ‖ 源里对应的原句片段\n```\n\n"
        f"- 类别只用这几个：{cats}。\n"
        "  - `判断`（作者的观点/结论，**含方向与强度**）、`步骤`（可照着做的操作顺序，别只留结论）、"
        "`条件`（成立前提、例外、失效条件，以及作者自己说的「我没验」「这里估不准」）、"
        "`数字`（金额/百分比/年份**及口径**）、`专名`（公司/产品/人名/工具）。\n"
        "- ⚠️ **不要写成大纲**：不列「第几节讲什么」，不按原文顺序抄句子，只列漏了就算失真的点；"
        "凑不满 6 条就少列，宁可短。\n"
        "- ⚠️ 超过 15 条通常说明在复述全文，回头砍掉次要的。\n"
        "- `‖` 后面必须是**从源里原样拷贝**的片段（不改写、不补字、不合并两处）；"
        "命中不了源的条目会被机械丢弃。\n\n"
        "## 阶段二 · 成文（清单之后）\n"
        "- 正文按本篇模板类型正常组织，**不要按清单顺序写**，也不要把清单当大纲扩写。\n"
        "- 逐条核对落点：判断带方向强度、步骤不缺环节、条件/例外不丢、数字带口径。\n"
        "- ⚠️ **落点 ≠ 逐条照搬**：该合并的合并、该按模板重组的重组；"
        "**禁止为「每条都有落点」把多条塞进一句、或把正文写成条目流水账** —— "
        "单句仍不超过 140 字、段落仍要自包含。\n"
        "- 正文里**不许出现**上面的清单标记或清单本身（系统会剥离并单独存档）。\n"
    )


def render_extraction_rules(source_chars: int) -> str:
    """按源长分档返回要拼进条目 prompt 的附加块（空串＝不注入）。

    - 源长 > `EXTRACT_MIN_CHARS`：T15/T6 长源提取清单（含 T0 的 SOURCE_THIN 行）；
    - 0 < 源长 < `THIN_MAX_CHARS`：T0 短源事前判断（报告项）；
    - `THIN_MAX_CHARS` ≤ 源长 ≤ `EXTRACT_MIN_CHARS`（2k–10k）：默认**不注入**；
      仅当实验开关 `NOTE_THIN_CHECKLIST=1` 时注入薄清单（S2 受控试验，PLAN-20261026 §4）。
    """
    if source_chars and source_chars > EXTRACT_MIN_CHARS:
        return "\n\n" + rules_text()
    if source_chars and source_chars < THIN_MAX_CHARS:
        return "\n\n" + thin_rules_text()
    if source_chars and THIN_MAX_CHARS <= source_chars <= EXTRACT_MIN_CHARS:
        if thin_checklist_enabled():
            return "\n\n" + mid_thin_rules_text()
    return ""


def rules_hash() -> str:
    """清单块版本号（prompt 变更需可追踪，与 provenance 同口径的文本 hash）。三个分档都算在内。"""
    import hashlib
    blob = rules_text() + "\n---\n" + thin_rules_text() + "\n---\n" + mid_thin_rules_text()
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def has_block(note: str) -> bool:
    return BEGIN in (note or "") and END in (note or "")


# ---------------------------------------------------------------- ② 解析

def parse_checklist(block: str) -> list:
    """解析块内条目。格式不符的行**不静默丢**：记成 `malformed` 进报告（可审计）。

    Returns: [{"cat","text","quote","malformed":bool}]；清单为空时返回 []。
    """
    items = []
    for line in (block or "").splitlines():
        if not line.strip() or line.strip().startswith("```"):
            continue
        m = _ITEM_RE.match(line)
        if not m:
            if line.strip().startswith(("-", "*")):
                items.append({"cat": "", "text": line.strip()[:120], "quote": "",
                              "malformed": True})
            continue
        cat = m.group("cat").strip()
        rest = m.group("rest")
        text, sep, quote = rest.partition("‖")
        if cat == SOURCE_THIN_TAG:
            items.append({"cat": cat, "text": rest.strip(), "quote": "", "malformed": False,
                          "thin": True})
            continue
        items.append({"cat": cat, "text": text.strip(), "quote": quote.strip() if sep else "",
                      "malformed": not bool(sep and quote.strip())})
    return items


def strip_block(note: str) -> tuple:
    """剥离清单块。Returns: (clean_note, block_text)；无块时 block_text=""。"""
    text = note or ""
    if BEGIN not in text or END not in text:
        return text, ""
    head, _, tail = text.partition(BEGIN)
    block, _, rest = tail.partition(END)
    return (head.rstrip() + "\n\n" + rest.lstrip()).strip() + "\n", block


# ---------------------------------------------------------------- ③ 机械校验（对源）

def machine_tokens(text: str) -> set:
    """可从文本机械提取、可跨写法核对的 token：数字（含小数）与拉丁词。"""
    out = set()
    for m in _NUM_RE.finditer(text or ""):
        tok = m.group(0)
        if tok in _JUNK_NUMS:
            continue
        out.add(tok)
        out.add(tok.replace(",", ""))
    for m in _LATIN_RE.finditer(text or ""):
        tok = m.group(0).lower()
        if len(tok) < 2 or tok in _EN_STOP:
            continue
        out.add(tok)
    return out


_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")


def item_tokens(item: dict) -> set:
    """条目的可机械核对 token：数字/拉丁词；`专名` 类**另收中文专名**（§12.3 #4 要求机械核对专名）。

    中文没有词边界，只在 `专名` 类别上收 CJK 连续段 —— 那类条目的陈述本身就基本是专名，
    误报风险可接受；其他类别收 CJK 会把普通词组当 token，制造假「未命中」。
    """
    toks = machine_tokens(item.get("text", ""))
    if item.get("cat") == "专名":
        for run in _CJK_RUN_RE.findall(item.get("text", "")):
            if run not in _EN_STOP:
                toks.add(run)
    return toks


def _quote_segments(quote: str) -> list:
    """原句按省略号切成若干段（模型可能用 `…` 略去中间）；段太短则忽略。"""
    parts = [p for p in _ELLIPSIS_RE.split(_norm(quote)) if len(p) >= 2]
    return parts or ([_norm(quote)] if len(_norm(quote)) >= 2 else [])


def verify_items(items: list, source_text: str) -> dict:
    """逐条机械校验「原句/关键 token 能在源里命中」；命中不了丢弃（§3.3 第 1 步验收）。

    判定（任一成立即通过）：
      - **原句命中**：原句（按省略号分段后）每段都能在源里原样找到；
      - **token 命中**：条目文本里的数字/拉丁 token 全部能在源里找到。

    Returns: {"kept":[...], "dropped":[...], "thin": [...]}，条目上补 `verified` / `weak` / `drop_reason`。
    """
    src = _norm(source_text)
    kept, dropped, thin = [], [], []
    for it in dict_items(items):
        if it.get("thin"):
            it["verified"] = True
            thin.append(it)
            continue
        if it["malformed"]:
            it["verified"] = False
            it["drop_reason"] = "malformed"
            dropped.append(it)
            continue
        segs = _quote_segments(it["quote"])
        quote_hit = bool(segs) and all(s in src for s in segs)
        toks = machine_tokens(it["text"])
        token_hit = bool(toks) and all(t in src for t in toks)
        if quote_hit or token_hit:
            it["verified"] = True
            it["weak"] = len(_norm(it["quote"])) < 6 and not token_hit
            kept.append(it)
        else:
            it["verified"] = False
            it["drop_reason"] = "quote_not_in_source" if it["quote"] else "no_quote"
            dropped.append(it)
    return {"kept": kept, "dropped": dropped, "thin": thin}


def dict_items(items: list) -> list:
    """返回浅拷贝，避免调用方在共享列表上改状态。"""
    return [dict(it) for it in items or []]


# ---------------------------------------------------------------- ④ 成文对账（§12.3 #4）

def reconcile(note_text: str, items: list) -> dict:
    """机械核对清单条目是否进了成文（只对 token 可判的条目下结论）。

    数字/专名可机械判：token 全在成文里 = `hit`，部分在 = `partial`，一个都不在 = `miss`；
    无可机械 token 的语义条目 = `needs_model`（交模型/子 Agent 自查，本模块不下结论）。
    未命中集（partial/miss）就是「可能要报给审核」的观察项。
    """
    note = _norm(note_text)
    rows = []
    for it in items or []:
        toks = item_tokens(it)
        if not toks:
            status = "needs_model"
            miss = set()
        else:
            miss = {t for t in toks if t not in note}
            status = "hit" if not miss else ("miss" if len(miss) == len(toks) else "partial")
        rows.append({"cat": it.get("cat", ""), "text": it.get("text", ""),
                     "quote": it.get("quote", ""), "status": status,
                     "missing_tokens": sorted(miss)})
    def _n(s):
        return sum(1 for r in rows if r["status"] == s)
    return {
        "rows": rows,
        "total": len(rows),
        "hit": _n("hit"), "partial": _n("partial"), "miss": _n("miss"),
        "needs_model": _n("needs_model"),
        "unmatched": _n("partial") + _n("miss"),
        "unmatched_items": [r for r in rows if r["status"] in ("partial", "miss")],
        "model_check_items": [r for r in rows if r["status"] == "needs_model"],
    }


# ---------------------------------------------------------------- ⑤ 持久化契约（§12.3 #12）

def _digest(text: str) -> str:
    import hashlib
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def store_path(source_sha256: str, store_dir: str = "") -> str:
    d = store_dir or STORE_DIR
    key = (source_sha256 or "unknown")[:16]
    return os.path.join(d, f"{key}.json")


def store(record: dict, store_dir: str = "") -> str:
    """写清单存档（按源 hash 归档，重生成时旧版进 `superseded`）。失败返回 ""，**永不抛异常**。"""
    try:
        path = store_path(record.get("source_sha256", ""), store_dir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        prev = load_file(path)
        if prev and prev.get("note_sha256") and prev.get("note_sha256") != record.get("note_sha256"):
            hist = list(prev.get("superseded") or [])
            hist.insert(0, {k: prev.get(k) for k in
                            ("ts", "note_sha256", "prompt_sha256", "items", "report")})
            record["superseded"] = hist[:3]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=1)
        return path
    except Exception:                              # 存档是观测，不许影响落盘
        return ""


def load_file(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_for_source(source_sha256: str, store_dir: str = "") -> dict:
    return load_file(store_path(source_sha256, store_dir))


def load_for_note(note_sha256: str, store_dir: str = "") -> dict:
    """按笔记 hash 反查（A5 复用 / A3 异步审核用）：扫目录找携带该 note_sha256 的最新记录。"""
    d = store_dir or STORE_DIR
    if not note_sha256 or not os.path.isdir(d):
        return {}
    for name in sorted(os.listdir(d), reverse=True):
        if not name.endswith(".json"):
            continue
        rec = load_file(os.path.join(d, name))
        if rec.get("note_sha256") == note_sha256:
            return rec
        for old in rec.get("superseded") or []:
            if old.get("note_sha256") == note_sha256:
                return {**rec, "superseded_hit": True, "items": old.get("items"),
                        "report": old.get("report")}
    return {}


# ---------------------------------------------------------------- ⑥ 生产入口

def process(note: str, source_text: str, *, note_type: str = "", title: str = "",
            source_path: str = "", source_chars: int = 0, store_dir: str = "",
            persist: bool = True) -> dict:
    """生产入口：剥离清单块 → 机械校验 → 对账 →（可选）存档。**永不抛异常**。

    Returns: {"note","has_block","items","dropped","thin","report","store_path",
              "report_line"}；无块时 `has_block=False`、其余为空，调用方原样用 note。
    """
    out = {"note": note or "", "has_block": False, "items": [], "dropped": [], "thin": [],
           "report": {}, "store_path": "", "report_line": ""}
    try:
        clean, block = strip_block(note or "")
        if not block:
            return out
        out["note"] = clean
        out["has_block"] = True
        parsed = parse_checklist(block)
        checked = verify_items(parsed, source_text or "")
        report = reconcile(clean, checked["kept"])
        report["dropped_unverifiable"] = len(checked["dropped"])
        report["source_thin"] = len(checked["thin"]) > 0
        report["thin_notes"] = [it["text"] for it in checked["thin"]]
        out.update({"items": checked["kept"], "dropped": checked["dropped"],
                    "thin": checked["thin"], "report": report})
        if persist:
            rec = {
                "schema_version": SCHEMA_VERSION,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "title": title or "",
                "note_type": note_type or "",
                "source_path": source_path or "",
                "source_chars": source_chars or 0,
                "source_sha256": _digest(source_text) if source_text else "unknown",
                "note_sha256": _digest(clean),
                "prompt_sha256": rules_hash(),
                "items": checked["kept"],
                "dropped": checked["dropped"],
                "report": report,
            }
            out["store_path"] = store(rec, store_dir)
        out["report_line"] = format_report_line(report)
    except Exception as exc:                        # 保险丝：清单处理绝不影响落盘
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["note"] = note or ""
    return out


def format_report_line(report: dict) -> str:
    if not report:
        return ""
    bits = [f"清单 {report.get('total', 0)} 条",
            f"命中 {report.get('hit', 0)}",
            f"部分 {report.get('partial', 0)}",
            f"未进 {report.get('miss', 0)}",
            f"待模型判 {report.get('needs_model', 0)}"]
    if report.get("dropped_unverifiable"):
        bits.append(f"校验丢弃 {report['dropped_unverifiable']}")
    if report.get("source_thin"):
        bits.append("SOURCE_THIN")
    return "｜".join(bits)


def review_reasons(report: dict) -> list:
    """报告项 → 异步审核理由标签（§12.3 #13：进队列观察，不触发重交）。"""
    out = []
    if not report:
        return out
    if report.get("unmatched"):
        out.append(f"checklist_miss:{report['unmatched']}")
    if report.get("source_thin"):
        out.append("source_thin")
    return out
