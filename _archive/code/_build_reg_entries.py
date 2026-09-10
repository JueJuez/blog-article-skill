"""Rebuild staging entries for the 9 summarized-but-unregistered 【监控】 notes,
then they will be moved into done_queue by `consume_migrate_queue.py --clean`.
Idempotent: these urls are already in dedup index, so --clean will register (not refetch).
"""
import json, os

REG = json.load(open("notes/_meta/summary_registry.json", encoding="utf-8"))
reg_items = list(REG.values()) if isinstance(REG, dict) else REG
done = json.load(open("notes/_scraped/migrate/done_queue.json", encoding="utf-8"))
done_urls = {e.get("url", "") for e in done}
VAULT = r"D:\Code\Obsidian\obsidian-path\AI 总结笔记"

# 1) collect the 9 unregistered entries
targets = []
for v in reg_items:
    if isinstance(v, dict) and v.get("source_url") and "【监控】" in (v.get("filename") or ""):
        if v.get("source_url") not in done_urls:
            targets.append(v)

def parse_frontmatter(path):
    """Return dict of yaml frontmatter (between leading --- lines)."""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    if not lines or lines[0].strip() != "---":
        return out
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        if ":" in ln:
            k, _, val = ln.partition(":")
            out[k.strip()] = val.strip().strip('"').strip("'")
    return out

entries = []
for v in targets:
    fn = v.get("filename", "")
    p = os.path.join(VAULT, *fn.split("/"))
    fm = parse_frontmatter(p)
    folder = "/".join(fn.split("/")[:-1])  # drop filename
    entry = {
        "url": v.get("source_url", ""),
        "title": fm.get("title") or fm.get("original_title") or os.path.basename(fn).split("_", 1)[-1].rsplit(".md", 1)[0],
        "author": fm.get("author", ""),
        "note_type": fm.get("note_type", "structured"),
        "tags": [t for t in (fm.get("tags") or "").split(",") if t] if isinstance(fm.get("tags"), str) else (fm.get("tags") or []),
        "publish_time": int(fm.get("publish_time", 0) or 0),
        "folder": folder,
        "raw_file": "",  # video already landed; raw may be gone, not needed for registration
        "prompt": "",
        "queued_at": 0,
        "kind": "video",
        "old_paths": [],
        "old_titles": [],
    }
    entries.append(entry)

# 2) write to staging (overwrite current empty staging)
json.dump(entries, open("notes/_scraped/migrate/pending_summaries.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("staging 写入条数:", len(entries))
for e in entries:
    print(f"  {e['folder'].split('/')[-1]:12s} | {e['title'][:30]} | url={e['url'][-22:]}")
