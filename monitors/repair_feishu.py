"""飞书补传（精准、幂等、零破坏）：只为双写缺口里「缺飞书」的笔记补建节点。

策略（绝不删除、绝不重写 Obsidian）：
- 遍历 REGEN_ITEMS（16 条已落盘成品的元数据）。
- 标题取子 Agent _summary_ 首个 H1（与 apply_pending_series 一致），用同一 generate_filename 推导文件名。
- 直接读 Obsidian 成品（VAULT + filename）作为权威 markdown（已含 frontmatter + #标签 + 作者行），保证飞书与 Obsidian 完全一致。
- 先查飞书对应容器下是否已存在同名节点；存在则跳过，不存在才 create。
- create 走 FeishuOutput.save（已内置频限指数退避重试）。

用法：python monitors/repair_feishu.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(ROOT, ".env"))

from articles.main import generate_filename  # noqa: E402
from articles.feishu import FeishuOutput  # noqa: E402
from _regen_items import REGEN_ITEMS  # noqa: E402

NOTES_DIR = os.path.join(ROOT, "notes")
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


def main():
    feishu = FeishuOutput()
    if not feishu.is_available():
        print("⚠️ 飞书不可用，退出")
        return
    if not VAULT:
        print("⚠️ 未配置 OBSIDIAN_VAULT_PATH，无法读取成品，退出")
        return

    created = 0
    skipped = 0
    failed = 0
    missing_obs = 0

    for item in REGEN_ITEMS:
        url = item.get("original_url", item.get("url", ""))
        publish_time = item.get("publish_time", 0)
        folder = item.get("folder", "")
        queue_title = item.get("title", "") or item.get("original_title", "")
        raw_file = item.get("raw_file", "")

        sp = summary_path_for(raw_file) if raw_file else ""
        h1 = ""
        if sp and os.path.exists(sp):
            with open(sp, "r", encoding="utf-8") as f:
                h1 = extract_h1(f.read())
        original_title = h1 if h1 and h1 != "Abstract" else (queue_title if queue_title != "Abstract" else (h1 or queue_title))

        fn = generate_filename(original_title, url, category="", publish_time=publish_time)
        if folder:
            fn = f"{folder}/{fn}"

        obs_path = os.path.join(VAULT, fn)
        if not os.path.exists(obs_path):
            print(f"  ⚠️ 无 Obsidian 成品，跳过：{original_title[:40]}  ({fn})")
            missing_obs += 1
            continue
        with open(obs_path, "r", encoding="utf-8") as f:
            obs_content = f.read()

        # 飞书是否已存在同名节点
        dirs = [p for p in (folder or "").replace("\\", "/").split("/") if p]
        parent = feishu.ensure_folder_path(dirs) if dirs else feishu.ensure_inbox_node()
        exists = False
        if parent:
            children = feishu.list_children(parent)
            title_match = os.path.splitext(os.path.basename(fn))[0]
            for node in children:
                if node.get("title") == title_match:
                    exists = True
                    break
        if exists:
            print(f"  ✓ 飞书已存在，跳过：{original_title[:40]}")
            skipped += 1
            continue

        if feishu.save(obs_content, fn, parent_token=parent if parent else None):
            print(f"  ✅ 已补传飞书：{original_title[:40]}")
            created += 1
        else:
            print(f"  ❌ 补传失败：{original_title[:40]}")
            failed += 1

    print(f"\n=== 飞书补传汇总 ===")
    print(f"已存在跳过：{skipped} 条")
    print(f"新创建：{created} 条")
    print(f"补传失败：{failed} 条")
    print(f"无 Obsidian 成品（异常）：{missing_obs} 条")


if __name__ == "__main__":
    main()
