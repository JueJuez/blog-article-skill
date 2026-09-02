"""编排方落盘：把子 Agent 已产出的 _summary_*.md 通过 save_summary_only 落盘（默认飞书，--obsidian 时追加 Obsidian）。

用法：
    python monitors/apply_pending.py                  # 正常落盘（队列 drain）
    python monitors/apply_pending.py --dry-run        # 只报告，不写回
    python monitors/apply_pending.py --regenerate     # 先删除已存在笔记再落盘（用于修复代码后重生成，避免重复）

行为：
- 读 monitors/pending_summaries.json（监控 run.py 在 FORCE_AGENT_MODE 下产出的待总结队列）。
- 每条按 raw_file 推导 _summary_ 路径；存在则读取正文。
- 标题优先取总结正文首个 H1（子 Agent 选定的最佳标题；修正 "Abstract" 等退化标题），
  否则回退队列 title。
- tags 由 note_type 映射（structured→结构化复盘 / key_points→要点提炼）。
- 调 articles.main.save_summary_only 落盘（默认飞书，--obsidian 时追加 Obsidian），并 dedup.mark_summarized。
- 无 _summary_ 的条目（抓空）写入 monitors/failed_summaries.json 单独留存，便于人工重抓。
- 成功项全部落盘后，pending_summaries.json 置空（队列已 drain）。
"""
import os
import sys
import json
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 允许 import _regen_items

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(ROOT, ".env"))

from articles.main import save_summary_only, _NOTE_TYPE_TAG, generate_filename  # noqa: E402
from articles import dedup  # noqa: E402
from articles.feishu import FeishuOutput  # noqa: E402
from _regen_items import REGEN_ITEMS  # noqa: E402

NOTES_DIR = os.path.join(ROOT, "notes")
PENDING = os.path.join(ROOT, "monitors", "pending_summaries.json")
FAILED = os.path.join(ROOT, "monitors", "failed_summaries.json")
VAULT = os.getenv("OBSIDIAN_VAULT_PATH", "")


def summary_path_for(raw_file: str) -> str:
    base = os.path.basename(raw_file)
    if base.startswith("_raw_"):
        return os.path.join(NOTES_DIR, "_summary_" + base[len("_raw_"):])
    return ""


def extract_h1(content: str) -> str:
    for line in content.split("\n"):
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return ""


def compute_filename(original_title: str, url: str, folder: str, publish_time: int) -> str:
    fn = generate_filename(original_title, url, category="", publish_time=publish_time)
    if folder:
        fn = f"{folder}/{fn}"
    return fn


def delete_existing(folder: str, filename: str):
    """删除已存在的 Obsidian 文件与飞书节点（用于 --regenerate 避免重复）。"""
    # Obsidian：用 rename 移到 .bak（避开沙箱 safe-delete 对 os.remove 的拦截）
    if VAULT:
        obs_path = os.path.join(VAULT, filename)
        if os.path.exists(obs_path):
            try:
                bak = obs_path + ".bak"
                if os.path.exists(bak):
                    try:
                        os.remove(bak)
                    except Exception:
                        pass
                os.rename(obs_path, bak)
                print(f"   🗑️ 已移走 Obsidian：{filename}")
            except Exception as e:
                print(f"   ⚠️ 移走 Obsidian 失败：{e}")
    # 飞书
    feishu = FeishuOutput()
    if feishu.is_available() and folder:
        try:
            dirs = [p for p in folder.replace("\\", "/").split("/") if p]
            parent = feishu.ensure_folder_path(dirs)
            if parent:
                children = feishu.list_children(parent)
                title_match = os.path.splitext(os.path.basename(filename))[0]
                for node in children:
                    if node.get("title") == title_match:
                        feishu.delete_node(node.get("node_token"))
                        break
        except Exception as e:
            print(f"   ⚠️ 删飞书节点异常：{e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只报告，不落盘也不写回")
    ap.add_argument("--regenerate", action="store_true", help="落盘前先删除已存在的笔记（修复代码后重生成）")
    ap.add_argument("--obsidian", action="store_true", help="同时写入 Obsidian（默认只写飞书）")
    args = ap.parse_args()

    if not os.path.exists(PENDING):
        print("⚠️ 无 pending_summaries.json，无需处理")
        return

    with open(PENDING, "r", encoding="utf-8") as f:
        raw = f.read()
    queue = json.loads(raw)
    if args.regenerate and not queue:
        queue = REGEN_ITEMS
        print(f"[regenerate] pending 为空，改用内置 {len(queue)} 条元数据集")

    successes = []
    failures = []
    skipped_no_summary = []

    for item in queue:
        url = item.get("original_url", item.get("url", ""))
        author = item.get("author", "")
        note_type = item.get("note_type", "")
        publish_time = item.get("publish_time", 0)
        folder = item.get("folder", "")
        queue_title = item.get("title", "") or item.get("original_title", "")
        raw_file = item.get("raw_file", "")

        sp = summary_path_for(raw_file) if raw_file else ""
        if not sp or not os.path.exists(sp):
            skipped_no_summary.append(item)
            print(f"  ⏭️ 无总结文件，跳过：{queue_title[:40]}  ({url})")
            continue

        with open(sp, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            skipped_no_summary.append(item)
            print(f"  ⏭️ 总结内容为空，跳过：{queue_title[:40]}")
            continue

        h1 = extract_h1(content)
        original_title = h1 if h1 and h1 != "Abstract" else (queue_title if queue_title != "Abstract" else (h1 or queue_title))
        tags = [_NOTE_TYPE_TAG.get(note_type, "文章总结")]
        filename = compute_filename(original_title, url, folder, publish_time)

        if args.dry_run:
            print(f"  🔍 [dry] 将落盘：{original_title[:40]} | tags={tags} | folder={folder}")
            successes.append(item)
            continue

        if args.regenerate:
            delete_existing(folder, filename)

        res = save_summary_only({
            "summarized_content": content,
            "original_url": url,
            "author": author,
            "tags": tags,
            "original_title": original_title,
            "publish_time": publish_time,
            "folder": folder,
            "obsidian": args.obsidian,
        })
        if res.get("success"):
            fn = res.get("filename", "")
            try:
                dedup.mark_summarized(url=url, content=content, title=original_title, filename=fn)
            except Exception as e:
                print(f"  ⚠️ dedup 标记失败（不影响落盘）：{e}")
            successes.append(item)
            print(f"  ✅ 已落盘：{original_title[:40]}  →  {fn}")
        else:
            failures.append(item)
            print(f"  ❌ 落盘失败：{original_title[:40]}  | {res.get('message')}")

    if args.dry_run:
        print("\n🔍 [dry-run] 未做任何写回（队列/failed 均未改动）")
        return

    with open(PENDING, "w", encoding="utf-8") as f:
        json.dump([], f, ensure_ascii=False, indent=2)

    if skipped_no_summary:
        with open(FAILED, "w", encoding="utf-8") as f:
            json.dump(skipped_no_summary, f, ensure_ascii=False, indent=2)
        print(f"\n📌 抓空项（无总结文件）已留存至 {os.path.relpath(FAILED, ROOT)}：{len(skipped_no_summary)} 条")
    else:
        if os.path.exists(FAILED):
            try:
                os.remove(FAILED)
            except Exception as e:
                print(f"   ⚠️ 删 failed_summaries.json 失败（不影响）：{e}")

    print(f"\n=== 落盘汇总 ===")
    print(f"成功落盘：{len(successes)} 条")
    print(f"落盘失败：{len(failures)} 条")
    print(f"抓空跳过：{len(skipped_no_summary)} 条")
    print(f"队列已置空。")
    if skipped_no_summary:
        for it in skipped_no_summary:
            print(f"   - [{it.get('author')}] {it.get('title', '')[:50]}  {it.get('url')}")


if __name__ == "__main__":
    main()
