"""批量落盘 migrate 重抓队列的 article entry（消费 staging 已抓正文的标准落盘入口）。

用法（在仓库根目录下用托管 Python 运行）：
  python scripts/land_migrate_entry.py --workdir notes/_scraped/migrate/_oz_work/articles --indices 0-7
  python scripts/land_migrate_entry.py --workdir <dir> --all

契约：
  - entry_<n>.json 已由拆分脚本生成（含 url/title/folder/raw_file/prompt 等）
  - out_<n>.md 由子 Agent 按 prompt 总结好的笔记正文（纯 markdown，无 frontmatter/无 H1）
  - 逐条调 save_summary_only(obsidian=True)，自动去重 + 过机械门禁 + 写 Obsidian
  - skipped 时额外校验磁盘文件是否存在，标注「幻影」让主 Agent 收尾时 force 补齐
退出码：有失败项则 2（供编排层感知）
"""
import os
import sys
import json
import argparse
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
# 加载 .env 确保 OBSIDIAN_VAULT_PATH / OBSIDIAN_WRITE 等落盘环境变量可用
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:
    pass
VAULT = os.environ.get("OBSIDIAN_VAULT_PATH", "")


def parse_idx(spec: str, total: int):
    idx = set()
    for p in spec.split(","):
        p = p.strip()
        if not p:
            continue
        if "-" in p:
            a, b = p.split("-", 1)
            idx.update(range(int(a), int(b) + 1))
        else:
            idx.add(int(p))
    return sorted(i for i in idx if 0 <= i < total)


def _file_exists(vault: str, filename: str) -> bool:
    if not filename:
        return False
    full = filename if os.path.isabs(filename) else os.path.join(vault, filename)
    return os.path.isfile(full)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--indices", default="")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    if not VAULT:
        print("❌ 未配置 OBSIDIAN_VAULT_PATH（.env 也未提供），无法做幻影检查，退出")
        sys.exit(1)

    wd = args.workdir
    entries = sorted(
        int(f.split("_")[1].split(".")[0])
        for f in os.listdir(wd)
        if f.startswith("entry_") and f.endswith(".json")
    )
    total = len(entries)
    idx = list(range(total)) if args.all else parse_idx(args.indices, total)
    print(f"workdir={wd} entry 总数={total}，本次处理索引：{idx}")

    from articles.main import save_summary_only

    ok = skip = skip_phantom = fail = 0
    for n in idx:
        ep = os.path.join(wd, f"entry_{n}.json")
        op = os.path.join(wd, f"out_{n}.md")
        if not os.path.exists(ep):
            print(f"  [{n}] 缺 entry 文件，跳过")
            fail += 1
            continue
        if not os.path.exists(op):
            print(f"  [{n}] 缺 out 文件 {op}，跳过")
            fail += 1
            continue
        e = json.load(open(ep, encoding="utf-8"))
        note = open(op, encoding="utf-8").read()
        tags = e.get("tags")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags or "[]")
            except Exception:
                tags = []
        if not isinstance(tags, list):
            tags = []
        res = save_summary_only({
            "summarized_content": note,
            "original_url": e.get("url", ""),
            "author": e.get("author", ""),
            "tags": tags,
            "original_title": e.get("title", ""),
            "publish_time": e.get("publish_time", 0),
            "folder": e.get("folder", ""),
            "note_type": e.get("note_type", ""),
            "obsidian": True,
        })
        title = str(e.get("title", ""))[:24]
        if res.get("success"):
            if res.get("skipped"):
                fn = res.get("filename", "")
                if _file_exists(VAULT, fn):
                    print(f"  [{n}] {title} => SKIPPED(已存在) {fn}")
                    skip += 1
                else:
                    print(f"  [{n}] {title} => SKIPPED(幻影-磁盘无文件) {fn}")
                    skip_phantom += 1
            else:
                print(f"  [{n}] {title} => OK {res.get('filename', '')}")
                ok += 1
        else:
            print(f"  [{n}] {title} => FAIL {res.get('message', '')}")
            fail += 1

    print(f"\n汇总：OK={ok} SKIP_已存在={skip} SKIP_幻影={skip_phantom} FAIL={fail}")
    if fail or skip_phantom:
        sys.exit(2)


if __name__ == "__main__":
    main()
