# -*- coding: utf-8 -*-
"""把一篇已有笔记导出为自包含分享版 HTML（管线②路径 A：存量笔记）。

用法：
    python scripts/export_note_html.py "<md路径>"
    python scripts/export_note_html.py "<文件名>.md"        # 在 vault 里递归查找
    python scripts/export_note_html.py "<md路径>" -o "输出.html"

产物：与笔记同目录同名 .html（-o 可覆盖），CSS/Chart.js 全内联、零外链资源，
发给别人浏览器打开即看。转换是确定性代码（articles/html_export.py），不调 AI。
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from articles.html_export import export_note_html  # noqa: E402


def _resolve_note_path(raw: str) -> str:
    """绝对/相对路径直接用；仅文件名时在 vault（或 notes/）里递归找唯一匹配。"""
    if os.path.isfile(raw):
        return os.path.abspath(raw)
    name = os.path.basename(raw)
    roots = [r for r in [os.getenv("OBSIDIAN_VAULT_PATH", ""),
                         os.path.join(BASE_DIR, "notes")] if r and os.path.isdir(r)]
    hits = []
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            if name in files:
                hits.append(os.path.join(dirpath, name))
        if hits:
            break
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise SystemExit(f"❌ 文件名在库中有多处匹配，请给完整路径：\n" + "\n".join(hits[:10]))
    raise SystemExit(f"❌ 找不到笔记：{raw}（vault={roots[0] if roots else '未设置'}）")


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="把一篇笔记 md 导出为自包含分享版 HTML（同目录同名 .html）")
    ap.add_argument("note", help="笔记 md 路径（或 vault 内唯一文件名）")
    ap.add_argument("-o", "--out", default="", help="输出 HTML 路径（默认与笔记同目录同名）")
    args = ap.parse_args()

    note_path = _resolve_note_path(args.note)
    out = export_note_html(note_path, args.out or None)
    print(f"🌐 分享版 HTML 已生成：{out}")


if __name__ == "__main__":
    main()
