"""短源篇幅取向：按源长关停「分析层与元层」小节。

依据 [PLAN-20261019-short-source-trim](../../docs/plans/PLAN-20261019-short-source-trim.md)
（用户 2026-10-19 定稿）。**只做生成端的 prompt 级约束**：短源（≤ `SHORT_SOURCE_MAX_CHARS`）
不写复习型元层与分析层，只保留「重组 / 条件与前提 / 有信号反例 / 作者私房术语」；长源一行不动。

**不改 `SECTION_CONTRACTS`、不改任何门禁签名**：`分层速览` 是 structured/general/reading 的
`required`，短源**保留**它（它是最便宜的压缩 gist），故 `structure_missing` 无需感知源长。

实测依据（A4 正式轮 30 篇，见计划 §1）：短源新稿/源长 2.95、事实密度 −37%，而长源 90% 的
新增是正当的源内容展开 → 只治短源。**加一句「写精简些」无效**（A4 已证 prompt 修订压不住变长），
本改动靠**关停具体小节**（有清单、可机械自报）。
"""
import argparse
import re

# 短源模式阈值（源文本去空白字数；口径 = prompts.templates.source_chars_of）。
# 5,000 是数据分界：≥5k 的稿长/源长已 ≤1.15，≤5k 的超标（0-2k 2.95、2-5k 1.83）。
SHORT_SOURCE_MAX_CHARS = 5000

# 块头标记：`scripts/build_round.py --prompt-profile baseline` 靠它定位并剥离本块。
BLOCK_MARKER = "附：短源篇幅取向（SHORT_SOURCE_BUDGET）"

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
        "孤立纯推广照既有口径处理（删 / 或标【推广】），**不为引流单设小节**。",
        "禁止为凑字数展开复述、加过渡性铺陈或总结性收尾；字数不设目标，覆盖优先。",
    ]
    return "\n".join(lines)


def describe(source_chars, note_type: str = "structured") -> str:
    """一行自报：源长 → 是否短源模式 → 关停清单。`--check` 与排障用。"""
    on = in_short_source_mode(source_chars)
    banned = blacklist_for(note_type) if on else []
    return (f"源长={source_chars or 0} 阈值={SHORT_SOURCE_MAX_CHARS} "
            f"短源模式={'是' if on else '否'} 类型={note_type} "
            f"关停={('、'.join(banned) if banned else '（无）')}")


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
