"""短源篇幅取向：按源长关停「分析层与元层」小节，并用**凭据表**约束内容小节的层。

依据 [PLAN-20261019-short-source-trim](../../docs/plans/PLAN-20261019-short-source-trim.md)
（用户 2026-10-19 定稿）。**只做生成端的 prompt 级约束**：短源（≤ `SHORT_SOURCE_MAX_CHARS`）
不写复习型元层与分析层，只保留「重组 / 条件与前提 / 有信号反例 / 作者私房术语」；长源一行不动。

内容小节的层**不禁**，改**按凭据准入**（2026-10-19 实测定案，见计划 §4.7–§4.9）：
背景/因果/证据/结论/原则-启示 各要一个能在源里指认的凭据，没有就不立层；同时写明
**必留四类（数字与口径 / 条件与前提 / 反例与例外 / 作者原话）优先于「少写一层」**——
一刀切禁令在 0-2k 高密度短源上会挤掉条件与原则（A04 实测 100%→67%）。

**不改 `SECTION_CONTRACTS`、不改任何门禁签名**：`分层速览` 是 structured/general/reading 的
`required`，短源**保留**它（它是最便宜的压缩 gist），故 `structure_missing` 无需感知源长。
本块里的任何字数说法都是**生成端取向**，不是门禁阈值——门禁仍只有 `<300` 与 `>max(8000, 源长×3)`。

实测依据（A4 正式轮 30 篇，见计划 §1）：短源新稿/源长 2.95、事实密度 −37%，而长源 90% 的
新增是正当的源内容展开 → 只治短源。**加一句「写精简些」无效**（A4 已证 prompt 修订压不住变长），
本改动靠**关停具体小节**与**层的准入凭据**（都有清单、可机械自报）。
"""
import argparse
import os
import re

# 短源模式阈值（源文本去空白字数；口径 = prompts.templates.source_chars_of）。
# 5,000 是数据分界：≥5k 的稿长/源长已 ≤1.15，≤5k 的超标（0-2k 2.95、2-5k 1.83）。
SHORT_SOURCE_MAX_CHARS = 5000

# 块头标记：`scripts/build_round.py --prompt-profile baseline` 靠它定位并剥离本块。
BLOCK_MARKER = "附：短源篇幅取向（SHORT_SOURCE_BUDGET）"

# 「内容粒度」子块的开关（默认关＝生产态不加）。
# 背景：关停小节只治「层」，0-2k 的膨胀主体在 required 内容小节**内部**的并列铺开。
# **2026-10-19 实测后否决**（`PLAN-20261019` §4.7）：0-2k 能压到 1.49，但 A04 丢了
# 「条件/前提」与「原则」——正是条款自己列的必留项 → 一刀切禁令在「源短且信息密」时过多。
# 生产改走「按凭据准入分层」（见 `render_section_budget` 的表格段），本开关**默认保持关**，
# 仅作为 §4.7 的证据留档与将来复跑用。
GRANULARITY_ENV = "NOTE_SHORT_GRANULARITY"
GRANULARITY_MARKER = "附：短源内容粒度（SHORT_SOURCE_GRANULARITY）"

# (自报名, 模板关键词)。关键词用于判断该笔记类型的模板里**是否真有这一节**——
# 只对确实存在的小节下禁令，避免对没有该节的类型说废话。
_BLACKLIST = (
    ("延伸思考 + 我的想法", "延伸思考"),
    ("自测三问（检索练习）", "自测三问"),
    ("讲出来（费曼技巧）", "讲出来"),
    ("笔记者分析（思维模型透镜）", "笔记者分析"),
    ("小结闭环", "小结闭环"),
)

# 短源**保留**的小节（写进块里，免得模型顺着「去水分」把速览也删了）。
_KEEP = (
    "分层速览（TL;DR）",
    "源里确有依据的模块：核心公式 / 骨架、对比矩阵、金句摘录、可行动项等",
    "关键术语卡——仅当源里作者自定义了某词，且只写「与常识用法的差异」一行",
)


def _template_text(note_type: str) -> str:
    """该笔记类型的模板正文。延迟 import 以免与 `prompts.templates` 循环依赖。"""
    try:
        from prompts.templates import NOTE_TEMPLATES
    except Exception:
        return ""
    t = NOTE_TEMPLATES.get(note_type) or NOTE_TEMPLATES.get("structured") or {}
    return t.get("prompt") or ""


def blacklist_for(note_type: str = "structured") -> list:
    """该类型模板里**确实存在**、且属黑名单的小节自报名（供块文本与自报共用）。"""
    text = _template_text(note_type)
    if not text:
        return [name for name, _ in _BLACKLIST]
    return [name for name, kw in _BLACKLIST if kw in text]


def granularity_on() -> bool:
    """内容粒度条款是否开启（`NOTE_SHORT_GRANULARITY=1`）。默认关＝生产态不加。"""
    return os.environ.get(GRANULARITY_ENV, "0").strip() in ("1", "true", "True", "yes", "on")


def render_granularity_block(note_type: str = "structured") -> str:
    """「禁止并列铺开」条款。

    ⚠️ 这是**待验假设**（`PLAN-20261019` §4.7 A/B）：禁止的是**分层**，不是信息——
    条款里配了「必留四类」，就是为了让保留率能证伪它：若禁止后保留率掉，说明分层承载了内容，
    该改成限量（每点 ≤2 层）而不是一刀切。
    """
    lines = [
        "", "",
        f"# {GRANULARITY_MARKER}",
        "",
        "短源的膨胀不在字数，在**层数**：把源里一个点拆成 背景 / 因果 / 证据 / 结论 四块并列写满。"
        "本篇**不要**这样写。",
        "",
        "- **一条主张 = 一段**：先用一句话给主张，再跟一段依据（源里原话或直接转述）。"
        "**不要**设「背景」「因果」「证据」「结论」「原则」「启示」这类并列子标题；"
        "真要分层，在段内用一句话交代，不单独立小节。",
        "- **必留四类一条都不能省**（禁止的是分层，不是信息）："
        "① 源里的数字与口径 ② 适用条件 / 前提 ③ 反例 / 例外 / 否定 ④ 作者原话（金句）。",
        "- 源没给背景就**不要补背景**；一句话讲得完的不铺成三段。",
        "- 字数不设目标，覆盖优先。",
    ]
    return "\n".join(lines)


def in_short_source_mode(source_chars) -> bool:
    """源长 ≤ 阈值即短源模式。0 / None / 非法值**不进入**——宁可不动，不误伤。"""
    try:
        n = int(source_chars or 0)
    except (TypeError, ValueError):
        return False
    return 0 < n <= SHORT_SOURCE_MAX_CHARS


def render_section_budget(source_chars, note_type: str = "structured") -> str:
    """短源模式返回约束块；长源 / 未知源长返回空串（长源一行不动）。"""
    if not in_short_source_mode(source_chars):
        return ""
    n = int(source_chars)
    banned = blacklist_for(note_type)
    lines = [
        "", "",
        f"# {BLOCK_MARKER}",
        "",
        f"本篇源为**短源**（约 **{n}** 字）。源短不等于值得展开——**不要把源里的一句话铺成一段**。",
    ]
    if banned:
        lines += ["", "本篇**不写**下列小节（写了即不合格）：", ""]
        lines += [f"- {b}" for b in banned]
    lines += ["", "保留并照常写："] + [f"- {k}" for k in _KEEP]
    lines += [
        "",
        "本篇只做下列扩展，且**每条须能在源里找到依据**（找不到就不写）：",
        "",
        "- **重组**：把源里散落的数据 / 条目重排为表、矩阵或时间线；只重排，不改写、不引申。",
        "- **条件与前提**：源给了做法但未交代适用边界的，补齐并标注来源或「（笔记者推断）」。",
        "- **反例 / 失效场景**：**仅当源里确有**反例、例外或否定表述时才写；不得为填节编造。",
        "- **作者私房术语**：仅当源里作者自定义了某词时，写一行「与常识用法的差异」。",
        "",
        # 2026-10-19 定：**不禁分层**，改为「按凭据准入」。依据见 `PLAN-20261019` §4.7/§4.8：
        # 一刀切禁令在 2-5k 上更优、在 0-2k 高密度短源上会挤掉「条件/原则」——同一个操作符
        # 在两类源上正负相反，说明真正的变量不是「铺不铺」，而是「这一层有没有源里的东西」。
        "**分层看凭据，不看篇幅**：一条主张下面要不要再分小节，由「源里有没有对应的东西」决定。",
        "",
        "| 想起的层 | 必须先能在源里指认 |",
        "|---|---|",
        "| 背景 | 源里写过当时的情境 / 时间 / 前因；纯观点文没有 → **不写背景** |",
        "| 因果 | 作者自己写了推理链（因为 / 所以 / 导致 / 于是），不是你替他补的 |",
        "| 证据 | 源里有具体数字、案例或原话引用 |",
        "| 结论 | 结论比主张**多出**判断或限定；只是换个说法 → 并进主张那一段 |",
        "| 原则 / 启示 | 作者明确说过「应该 / 最好 / 别」这类规范表述 |",
        "",
        "指认不出来的层就不要。**层多不是问题，空层才是。**",
        "",
        "**必留四类（优先级高于「少写一层」）**：① 源里的数字与口径 ② 适用条件 / 前提 "
        "③ 反例 / 例外 / 否定 ④ 作者原话（金句）。这四类**冲突时优先**——宁可多一层，也不许丢。",
        "",
        "孤立纯推广照既有口径处理（删 / 或标【推广】），**不为引流单设小节**。",
        "禁止为凑字数展开复述、加过渡性铺陈或总结性收尾；字数不设目标，覆盖优先。",
    ]
    if granularity_on():
        lines += [render_granularity_block(note_type)]
    return "\n".join(lines)


def describe(source_chars, note_type: str = "structured") -> str:
    """一行自报：源长 → 是否短源模式 → 关停清单（＋内容粒度条款开关）。`--check` 与排障用。"""
    on = in_short_source_mode(source_chars)
    banned = blacklist_for(note_type) if on else []
    return (f"源长={source_chars or 0} 阈值={SHORT_SOURCE_MAX_CHARS} "
            f"短源模式={'是' if on else '否'} 类型={note_type} "
            f"关停={('、'.join(banned) if banned else '（无）')} "
            f"粒度条款={'开' if granularity_on() else '关'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="短源篇幅取向自报（SHORT_SOURCE_BUDGET）")
    ap.add_argument("--check", action="store_true", help="打印阈值、各笔记类型的关停清单与边界自检")
    ap.add_argument("--source-chars", type=int, default=0, help="源字数（配合 --note-type 单篇自报）")
    ap.add_argument("--note-type", default="structured")
    a = ap.parse_args(argv)

    if a.source_chars:
        print(describe(a.source_chars, a.note_type))
        return 0

    from prompts.templates import NOTE_TEMPLATES
    print(f"SHORT_SOURCE_MAX_CHARS = {SHORT_SOURCE_MAX_CHARS}")
    print(f"BLOCK_MARKER = {BLOCK_MARKER!r}")
    print(f"内容粒度条款：{GRANULARITY_ENV}={'1' if granularity_on() else '0'}"
          f"（默认 0＝生产不启用；A/B 见 --prompt-profile noblocks）")
    print("\n边界自检：")
    for n, want in ((4999, True), (5000, True), (5001, False), (0, False), (None, False)):
        got = in_short_source_mode(n)
        print(f"  source_chars={n!r:>6} → 短源模式={got} {'✓' if got == want else '✗ 期望 ' + str(want)}")
    print("\n各笔记类型的关停清单（只列模板里确实存在的小节）：")
    for k in sorted(NOTE_TEMPLATES):
        banned = blacklist_for(k)
        print(f"  {k:12s} {('、'.join(banned) if banned else '（无可关停小节）')}")
    print("\n块文本样例（源 3000 字 / structured）：")
    print(render_section_budget(3000, "structured"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
