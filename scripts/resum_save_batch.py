# -*- coding: utf-8 -*-
"""存量重做落盘（阶段C 起的批量重做唯一落盘入口）：把子 Agent 生成的临时 md 覆盖写回各自已有的 note_path。

设计（2026-09-17 重构）：
  in-place 覆盖能力已**下沉到生产函数** `articles.main.save_summarized_article(note_path=...)`，
  本脚本退化为薄壳，只做三件只有它知道的事：
    1. 遍历批次 JSON，定位临时稿 `<out_dir>/<batch_name>_<NN>.md`；
    2. 落盘前校验：主题词行存在（MISSING_TOPIC_LINE）+ 机械门禁 `verify_note_mechanical`；
    3. 调生产函数落盘（标签/来源链接/格式化/dedup 登记全部复用生产链路，不再复制实现）。

为什么以前这里复制了 4 个变换（extract_and_strip_topics / infer_semantic_tags /
format_note_with_prompt / verify_note_mechanical）：当初 `save_summarized_article` 只支持
「由 title+publish_time 重新推导文件名」，而重做必须写回既有精确路径。现在生产函数支持
`note_path`，复制实现即可删除——否则生产侧改标签规则时，重做出的笔记会与正常落盘漂移。

⚠️ 与 `overwrite=True` 的区别（易混淆，别再搞错）：
  `overwrite=True` 覆盖的是**同名**文件，而文件名仍是 `generate_filename` 推导出来的；
  标题一旦与磁盘现有名错配（阶段C 690 条里有 18 条）就扑空 → 照样生成 -N 副本。
  本脚本走 `note_path`，压根不推导文件名。

用法：
  python scripts/resum_save_batch.py <batch_NN.json> <out_dir> [--force]
  out_dir 下临时笔记命名约定：<batch_name>_<索引02d>.md（与子 Agent 产出一致）
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TOPIC_LINE_RE = re.compile(r"【核心主题词】\s*[:：]?\s*\S+")


def main() -> int:
    args = sys.argv[1:]
    if "--force" in args:
        args = [a for a in args if a != "--force"]
    if len(args) < 2:
        print("用法: resum_save_batch.py <batch.json> <out_dir> [--force]")
        return 2
    batch_path, out_dir = args[0], args[1]
    batch_name = os.path.splitext(os.path.basename(batch_path))[0]
    items = json.load(open(batch_path, encoding="utf-8"))

    from shared.env import load_env
    load_env(force_obsidian=True)
    from articles.main import save_summarized_article
    from prompts.verifier import verify_note_mechanical, count_note_words

    ok, failed, landed = 0, [], 0
    for i, it in enumerate(items):
        md = os.path.join(out_dir, f"{batch_name}_{i:02d}.md")
        title = it.get("title", "")
        if not os.path.exists(md):
            failed.append((i, "MISSING_FILE", title))
            continue
        with open(md, encoding="utf-8") as f:
            content = f.read()
        if not TOPIC_LINE_RE.search(content):
            failed.append((i, "MISSING_TOPIC_LINE", title))
            continue

        note_path = it.get("note_path", "")
        if not note_path:
            failed.append((i, "NOTE_PATH_EMPTY", title))
            continue
        # note_path 不存在 = 该笔记当初从未落盘（登记表悬空 / -1 副本未生成）。
        # 重做目标就是把它们补齐，因此「缺失即落地新建」而非跳过（写入的仍是 note_path，不产副本）。
        landed_new = not os.path.exists(note_path)

        url = it.get("url", "")
        note_type = it.get("note_type", "")
        author = it.get("author", "")

        # 门禁（与生产一致）：H1/来源链接/URL + 极端字数；不过就不覆盖旧版好笔记
        src = it.get("source_path", "")
        source_chars = 0
        if src and os.path.exists(src):
            try:
                source_chars = count_note_words(open(src, encoding="utf-8").read())
            except Exception:
                pass
        gate = verify_note_mechanical(content, note_type, source_url=url,
                                     source_chars=source_chars)
        if not gate["passed"]:
            failed.append((i, "GATE:" + ";".join(gate["issues"]), title))
            continue

        _vault = os.getenv("OBSIDIAN_VAULT_PATH", "")
        rel_dir = ""
        if _vault:
            try:
                rel_dir = os.path.relpath(os.path.dirname(note_path), _vault)
            except Exception:
                rel_dir = ""

        try:
            _formed, _fname = save_summarized_article(
                content,
                original_url=url,
                author=author,
                original_title=title,
                tags=[],
                note_type=note_type,
                publish_time=0,
                folder=rel_dir,
                obsidian=True,
                note_path=note_path,
            )
        except Exception as e:
            failed.append((i, f"SAVE_FAILED:{e}", title))
            continue

        ok += 1
        if landed_new:
            landed += 1
        if (i + 1) % 4 == 0 or i == len(items) - 1:
            _tag = " [NEW]" if landed_new else ""
            print(f"  ✓ [{i}] {title[:28]} -> {os.path.basename(note_path)} ({len(_formed)}字){_tag}")

    print(f"\n{batch_name}: 覆盖落盘 {ok}/{len(items)} (其中新建落地 {landed}) | 失败 {len(failed)}")
    for i, why, t in failed:
        print(f"  ✗ [{i}] {why} | {t[:34]}")
    return 0 if ok > 0 or len(items) == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
