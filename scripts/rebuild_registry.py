"""rebuild_registry：vault 扫描 → 统一登记表 bootstrap（PLAN-20260908 阶段 2）。

真源 = vault 成品笔记；默认 dry-run 只读扫描，--apply 才写登记表：
- 排除 1：路径任一段 `_` 前缀目录（_meta/_scraped/_migrate_gate_archive）不扫描
- 排除 2：命中重抓队列 old_paths 的文件不登记（D2：异常内容完全不登记）
- 排除 3：文件名与父目录同名的疑似容器页单列报告、不登记（--include-container 强制登记）
URL 三级兜底复用 migrate_gate._main_url（fm source_url → 正文来源链接 → 标题指纹 title_fp）。
幂等：重复跑键集合一致，已有人工字段（link 等）靠 register merge 语义保留。
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from articles import dedup  # noqa: E402

import scripts.migrate_gate as mg  # noqa: E402

_H1_LINE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def load_queue_old_paths(archive_dir: "str | Path") -> set:
    """读重抓队列最新 scan JSON 的 old_paths，归一化为可比较集合。

    取字典序最新的 migrate_scan_*.json（时间戳命名，字典序即时间序）；
    无目录 / 无文件 / 坏 JSON 一律返回空集——排除 2 失效但不阻塞 bootstrap。
    """
    d = Path(archive_dir)
    if not d.is_dir():
        return set()
    scans = sorted(d.glob("migrate_scan_*.json"))
    if not scans:
        return set()
    try:
        payload = json.loads(scans[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    out: set = set()
    for item in payload.get("queue", []):
        for p in item.get("old_paths", []):
            out.add(os.path.normcase(os.path.abspath(str(p))))
    return out


def _extract_title(fm: str, body: str, stem: str) -> str:
    """标题三级兜底：fm title → 正文第一个 H1 → 文件名 stem。"""
    m = mg._TITLE_LINE.search(fm)
    if m:
        t = mg._unquote(m.group(1).strip())
        if t:
            return t
    m = _H1_LINE.search(body)
    if m:
        return m.group(1).strip()
    return stem


def scan_vault(vault: "str | Path", old_paths: Optional[set] = None,
               include_containers: Optional[Iterable[str]] = None,
               exclude_dirs: Optional[Iterable[str]] = None) -> dict:
    """只读扫描 vault，返回计数与 entries；不写登记表。

    Returns:
        {scanned, registered, excluded_underscore_dirs, excluded_queue,
         containers, title_fp, entries}
        entries 项 = {key, key_type, url, title, folder}
    """
    base = Path(vault).resolve()
    old_paths = old_paths or set()
    includes = set(include_containers or [])
    excludes = set(exclude_dirs or [])
    res = {
        "scanned": 0,
        "registered": 0,
        "excluded_underscore_dirs": 0,
        "excluded_queue": 0,
        "containers": [],
        "title_fp": 0,
        "entries": [],
    }
    if not base.is_dir():
        return res
    for p in sorted(base.rglob("*.md")):
        rel = p.relative_to(base)
        parts = rel.parts
        dirs = parts[:-1]
        if excludes and any(seg in excludes for seg in dirs):
            continue
        if any(seg.startswith("_") for seg in dirs):
            res["excluded_underscore_dirs"] += 1
            continue
        if os.path.normcase(str(p)) in old_paths:
            res["excluded_queue"] += 1
            continue
        res["scanned"] += 1
        if len(parts) >= 2 and p.stem == parts[-2] and p.stem not in includes:
            res["containers"].append(str(p))
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        fm, body = mg.split_fm(text)
        url = mg._main_url(fm, body)[0] or ""
        title = _extract_title(fm, body, p.stem)
        prefix, h = dedup._key_for(url=url, title=title)
        folder = rel.parent.as_posix()
        if folder == ".":
            folder = ""
        res["entries"].append({
            "key": h, "key_type": prefix, "url": url, "title": title,
            "folder": folder,
        })
        if prefix == "title_fp":
            res["title_fp"] += 1
    res["registered"] = len(res["entries"])
    return res


def rebuild(vault: "str | Path", archive_dir: "str | Path | None" = None,
            apply: bool = False) -> dict:
    """组合：读重抓队列排除集 → 扫描 → apply 时逐条登记（幂等 merge）。"""
    base = Path(vault).resolve()
    if archive_dir is None:
        archive_dir = base.parent / "_migrate_gate_archive"
    old_paths = load_queue_old_paths(archive_dir)
    res = scan_vault(str(base), old_paths=old_paths)
    if apply:
        for e in res["entries"]:
            dedup.register(url=e["url"], title=e["title"], folder=e["folder"])
    return res


def _dup_title_groups(entries: list) -> list:
    """同标题多键疑似：标题非空且对应多于一个不同 key 的分组。"""
    by_title: dict = {}
    for e in entries:
        if e["title"]:
            by_title.setdefault(e["title"], set()).add(e["key"])
    return [(t, ks) for t, ks in by_title.items() if len(ks) > 1]


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description="vault 扫描 → 统一登记表 bootstrap（默认 dry-run 只读）")
    ap.add_argument("--vault", required=True, help="Obsidian vault 路径")
    ap.add_argument("--apply", action="store_true", help="写登记表（缺省只扫描）")
    ap.add_argument("--include-container", action="append", default=[],
                    help="强制登记同名容器页（可多次）")
    ap.add_argument("--exclude", action="append", default=[],
                    help="额外排除的目录段名（可多次）")
    args = ap.parse_args(argv)

    base = Path(args.vault).resolve()
    archive_dir = base.parent / "_migrate_gate_archive"
    old_paths = load_queue_old_paths(archive_dir)
    res = scan_vault(str(base), old_paths=old_paths,
                     include_containers=args.include_container,
                     exclude_dirs=args.exclude)

    print("== rebuild_registry 扫描报告 ==")
    print(f"vault: {base}")
    print(f"扫描 {res['scanned']} | 登记 {res['registered']} | "
          f"指纹键 {res['title_fp']}")
    print(f"排除：_ 目录 {res['excluded_underscore_dirs']} | "
          f"重抓队列 {res['excluded_queue']} | 容器页 {len(res['containers'])}")
    if res["containers"]:
        print("容器页清单（不登记，人工核对）：")
        for c in res["containers"]:
            print(f"  - {c}")
    dups = _dup_title_groups(res["entries"])
    if dups:
        print("同标题多键疑似（人工核对）：")
        for t, ks in dups[:10]:
            print(f"  - {t} -> {len(ks)} 键")
    if args.apply:
        for e in res["entries"]:
            dedup.register(url=e["url"], title=e["title"], folder=e["folder"])
        print(f"已登记 {res['registered']} 条 -> 统一登记表")
    else:
        print("dry-run：未写登记表（--apply 生效）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
