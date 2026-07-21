"""scripts/convert_series_to_hierarchy.py — 把千刀千法系列课笔记按新层级结构批量重排。

背景：prompt 模板 KEY_POINTS_PROMPT 已固化「目录 / ##章节 / ###子论点 / ####细分 / 表格 / 引用块」
的层级结构。现有 15 篇（除已手工层级化的第02集）仍是旧式（**加粗**冒充标题、无目录、
作者为【作者未知】）。本脚本用规则把旧结构重排为新层级，保留 AI 已总结的内容。

不依赖外部 AI（当前环境无 provider）：纯正则转换，安全可重跑。

用法：
    python scripts/convert_series_to_hierarchy.py            # dry-run 全部（写 .new.md 不覆盖）
    python scripts/convert_series_to_hierarchy.py --apply     # 真正覆盖原文件
    python scripts/convert_series_to_hierarchy.py 某文件.md   # 单篇 dry-run
"""
import os
import re
import sys

AUTHOR = "奇衡DK-CAPITAL"
BASE = r"D:\Code\Skills\blog-article-skill\notes\千刀千法"

# 章节：关键词 -> 新标题（顺序即层级顺序）
SECTIONS = [
    ("核心论点", "一、核心论点"),
    ("金句摘录", "二、金句摘录"),
    ("可行动项", "三、可行动项"),
    ("适合谁看", "四、适合谁看"),
]

TOC = """## 目录

- [一、核心论点](#一核心论点)
- [二、金句摘录](#二金句摘录)
- [三、可行动项](#三可行动项)
- [四、适合谁看](#四适合谁看)
- [五、总结收束](#五总结收束)
"""


def _looks_like_header(line: str, kw: str) -> bool:
    s = line.strip()
    if kw not in s:
        return False
    if s.startswith("#") or s.startswith("**"):
        return True
    # 纯文本短行（无长句、非列表）
    if len(s) < 30 and "。" not in s and not s.startswith("-"):
        return True
    return False


def transform(text: str) -> str:
    lines = text.split("\n")
    out = []
    inserted_toc = False
    for line in lines:
        s = line.strip()
        # 1) 作者：【作者未知】-> 真实 UP主
        if "【作者未知】" in line:
            line = line.replace("【作者未知】", AUTHOR)
        # 2) 一句话核心结论 -> 引用块
        m = re.match(r"^\*\*一句话核心结论\*\*[：:]\s*(.*)$", line)
        if m and not line.startswith(">"):
            line = f"> **一句话核心结论**：{m.group(1)}"
        # 3) 章节标题
        new_sec = None
        for kw, repl in SECTIONS:
            if _looks_like_header(line, kw):
                new_sec = repl
                break
        if new_sec:
            line = f"## {new_sec}"
            if not inserted_toc:
                out.append("")
                out.append(TOC.rstrip("\n"))
                out.append("")
                inserted_toc = True
        # 4) 子论点：**N. 标题** -> ### N. 标题
        m2 = re.match(r"^\*\*(\d+)\.\s*([^*\n]+?)\*\*\s*$", line)
        if m2:
            line = f"### {m2.group(1)}. {m2.group(2).strip()}"
        out.append(line)
    # 5) 结尾加粗句 -> ## 五、总结收束
    for i in range(len(out) - 1, -1, -1):
        l = out[i].strip()
        if (l.startswith("**") and l.endswith("**")
                and not l.startswith(">")
                and not l.startswith("-")
                and "|" not in l
                and len(l) < 200):
            out.insert(i, "## 五、总结收束")
            out.insert(i + 1, "")
            break
    return "\n".join(out)


def _process(path: str, apply: bool):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new = transform(text)
    if apply:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        print(f"[apply] {os.path.basename(path)}")
    else:
        outp = path[:-3] + ".new.md"
        with open(outp, "w", encoding="utf-8") as f:
            f.write(new)
        print(f"[dry]   {os.path.basename(path)} -> {os.path.basename(outp)}")


def main():
    apply = "--apply" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--apply"]
    if args:
        for p in args:
            _process(p, apply)
        return
    for fn in sorted(os.listdir(BASE)):
        if not fn.startswith("第") or not fn.endswith(".md") or fn.endswith(".new.md"):
            continue
        if fn.startswith("第02集"):  # 已手工层级化，跳过
            continue
        if fn.startswith("00_"):
            continue
        _process(os.path.join(BASE, fn), apply)


if __name__ == "__main__":
    main()
