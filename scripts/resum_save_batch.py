# -*- coding: utf-8 -*-
"""阶段C 重跑落盘：把子 Agent 生成的临时 md 直接覆盖写回各自已有的 note_path。

为什么不用 `_save_summary_from_file.py`：
  那个脚本按 BVID 在 `monitors/pending_summaries.json` 里查条目，但 690 条重跑项根本没进该队列
  （build_resum_batches 只产出批次文件）。而且它走 `save_summarized_article` → `generate_filename`，
  文件名由 `publish_time`+`title` **重新推导**（不是复用磁盘上已有的那个名字）；其中 18 条 basename
  标题与队列 title 不一致 → 推导出的是「新文件名」→ 产生 -N 副本而非覆盖（正是阶段C 要消灭的坑）。

  补充（2026-09-17 订正，别被旧说法误导）：`save_summarized_article(overwrite=True)` 已能「覆盖同名旧文件」
  （2026-09-15 修复；此前默认改名逻辑在全库累积过 50 个 -N 副本）。但它**只覆盖同名**——文件名仍是重新推导的，
  标题一旦错配（或归一化差异如 `30%`→`30％`）就扑空，副本照产。所以 in-place 重做的本质不是「允许覆盖」，
  而是「写入目标 = 既有精确路径，压根不走 generate_filename」。

本脚本改用「直写 note_path」：
  - 复用生产链路的全部变换（extract_and_strip_topics / infer_semantic_tags / format_note_with_prompt）
    与机械门禁（verify_note_mechanical），标签与来源链接格式与正常落盘完全一致；
  - 但写入目标 = 批次条目里记录的 `note_path`（既有文件，相对 vault 的精确路径），in-place 覆盖，
    绝不会产生新文件名、绝不出现 -N 副本。
  - 门禁不过 → 不写（保留旧版好笔记），记录到失败清单，交由后续修正。

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

VAULT = os.getenv("OBSIDIAN_VAULT_PATH") or r"D:\Code\Obsidian\obsidian-path\AI 总结笔记"
TOPIC_LINE_RE = re.compile(r"【核心主题词】\s*[:：]?\s*\S+")


def load_env():
    p = os.path.join(ROOT, ".env")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    os.environ["OBSIDIAN_WRITE"] = "1"
    os.environ["DISABLE_FEISHU_SYNC"] = "1"


def _guess_source(url: str) -> str:
    u = url or ""
    if "bilibili.com" in u or "b23.tv" in u:
        return "bilibili"
    if "mp.weixin.qq.com" in u:
        return "wechat"
    if "scys.com" in u:
        return "scys"
    return ""


def main() -> int:
    args = sys.argv[1:]
    if "--force" in args:
        force = True
        args = [a for a in args if a != "--force"]
    else:
        force = False
    if len(args) < 2:
        print("用法: resum_save_batch.py <batch.json> <out_dir> [--force]")
        return 2
    batch_path, out_dir = args[0], args[1]
    batch_name = os.path.splitext(os.path.basename(batch_path))[0]
    items = json.load(open(batch_path, encoding="utf-8"))

    load_env()
    from shared.note_classify import extract_and_strip_topics, infer_semantic_tags
    from prompts.templates import format_note_with_prompt
    from prompts.verifier import verify_note_mechanical, count_note_words
    try:
        from articles import dedup
        _HAVE_DEDUP = True
    except Exception:
        _HAVE_DEDUP = False

    ok, skipped, failed, landed = 0, 0, [], 0
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
        # 阶段C 目标就是把 690 条全部补齐，因此「缺失即落地新建」而非跳过——
        # 写入目标就是批次记录的 note_path，不会产生 -N 副本（不调用 generate_filename）。
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

        # —— 复用生产链路变换（与 save_summarized_article 同序）——
        content, _block_topics = extract_and_strip_topics(content)
        effective_topics = _block_topics if (_block_topics and len(_block_topics) > 0) else None
        folder_dir = os.path.dirname(note_path)
        try:
            semantic = infer_semantic_tags(content, folder=folder_dir, author=author,
                                          note_type=note_type, url=url, topics=effective_topics)
        except Exception as e:
            semantic = []
            print(f"  ⚠️ [{i}] 语义标签生成失败（非致命，跳过标签）：{e}")
        tags = []
        _seen = set()
        for _t in (semantic or []):
            if _t not in _seen:
                tags.append(_t)
                _seen.add(_t)
        # 方案 A 收口：命名空间值去冗余裸标签 + 去裸作者标签
        _ns_values = {_t.split("/", 1)[1] for _t in tags if "/" in _t}
        if _ns_values:
            tags = [_t for _t in tags if not ("/" not in _t and _t in _ns_values)]
        _an = (author or "").strip()
        if _an:
            tags = [_t for _t in tags if _t != _an]

        formatted = format_note_with_prompt(content, author=author, url=url,
                                            tags=tags, add_metadata=True, publish_time=0)

        os.makedirs(os.path.dirname(note_path), exist_ok=True)
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(formatted)

        if _HAVE_DEDUP:
            try:
                rel = os.path.relpath(note_path, VAULT)
                dedup.mark_summarized(url=url, title=title, filename=rel,
                                     folder=folder_dir, note_type=note_type,
                                     source=_guess_source(url))
            except Exception:
                pass

        ok += 1
        if landed_new:
            landed += 1
        if (i + 1) % 4 == 0 or i == len(items) - 1:
            _tag = " [NEW]" if landed_new else ""
            print(f"  ✓ [{i}] {title[:28]} -> {os.path.basename(note_path)} ({len(formatted)}字, {len(tags)}标签){_tag}")

    print(f"\n{batch_name}: 覆盖落盘 {ok}/{len(items)} (其中新建落地 {landed}) | 失败 {len(failed)}")
    for i, why, t in failed:
        print(f"  ✗ [{i}] {why} | {t[:34]}")
    return 0 if ok > 0 or len(items) == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
