"""Migrate an existing Feishu Bitable project library to local Obsidian markdown.

Reads every record from the configured Bitable (FEISHU_BASE_TOKEN /
FEISHU_TABLE_ID, or a wiki link with ?table=), maps the Bitable field ids back to
logical fields, and writes one Markdown file per project under the local library
folder (PROJECT_LIBRARY_DIR, or OBSIDIAN_VAULT_PATH/开源项目).

This is a one-off migration helper. Run it once after switching the tool's storage
backend to local; afterwards `assets/main.py` writes new projects directly to
markdown and the Bitable is no longer used.

Usage:
    python migrate_feishu_to_local.py [--dry-run] [--limit N]
"""
import os
import sys
import json
import argparse

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.append(_BASE_DIR)

from assets.env_loader import load_root_env
load_root_env()
from assets.feishu_writer import resolve_feishu_target, _run_lark_cli, DEFAULT_FIELD_MAP
from assets.local_writer import (
    get_library_dir,
    owner_repo_to_filename,
    extract_raw_url,
    parse_owner_repo_from_url,
    platform_from_url,
    build_frontmatter,
    render_markdown,
)
from assets.tracker import append_to_imported_list

def _kind_of(logical: str) -> str:
    return {
        "tags": "tags",
        "community_score": "number",
        "doc_score": "number",
        "func_score": "number",
        "total_score": "number",
        "eval_date": "date",
        "project_type": "select",
        "run_form": "select",
        "target_user": "select",
        "domain": "select",
        "status": "select",
    }.get(logical, "text")


# field id -> (logical name, kind)
# kinds: text | select | tags | number | date
_INV_MAP = {fid: (logical, _kind_of(logical)) for logical, fid in DEFAULT_FIELD_MAP.items()}


def _unwrap(v):
    """Bitable matrix wraps single selects in a one-element list; unwrap them."""
    if isinstance(v, list):
        if len(v) == 1:
            return v[0]
        return v
    return v


def _parse_tags(v):
    if v is None:
        return []
    if isinstance(v, list):
        # flatten + split comma-joined strings
        out = []
        for item in v:
            out.extend(_split_commas(item))
        return out
    return _split_commas(v)


def _split_commas(s):
    if not s:
        return []
    return [p.strip() for p in str(s).replace("，", ",").split(",") if p.strip()]


def _to_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _fetch_all_records(base_token, table_id, page_limit=200):
    rows = []
    field_ids = []
    offset = 0
    while True:
        out = _run_lark_cli([
            "base", "+record-list",
            "--base-token", base_token,
            "--table-id", table_id,
            "--limit", str(page_limit),
            "--offset", str(offset),
            "--json",
        ])
        if not out or not out.get("ok"):
            print("  ❌ 读取飞书记录失败：", out)
            break
        data = out.get("data", {}) or {}
        batch = data.get("data", []) or []
        if not field_ids:
            field_ids = data.get("field_id_list", []) or []
        rows.extend(batch)
        if len(batch) < page_limit:
            break
        offset += page_limit
        if offset > page_limit * 50:  # 安全上限
            break
    return rows, field_ids


def migrate(dry_run: bool = False):
    lib = get_library_dir()
    if not lib:
        print("❌ 未配置本地项目库（PROJECT_LIBRARY_DIR / OBSIDIAN_VAULT_PATH 均未设置）。")
        return 0

    bt, tid = resolve_feishu_target()
    if not bt or not tid:
        print("❌ 未检测到飞书配置（FEISHU_BASE_TOKEN / FEISHU_TABLE_ID 未设置）。")
        return 0

    print(f"📡 读取飞书 Bitable（base={bt} table={tid}）…")
    rows, field_ids = _fetch_all_records(bt, tid)
    if not field_ids:
        print("❌ 未能获取字段列表，无法映射。")
        return 0
    print(f"   读到 {len(rows)} 条记录，字段 {len(field_ids)} 个。")

    os.makedirs(lib, exist_ok=True)
    written = skipped = failed = 0

    for row in rows:
        # map field_id -> value
        record = {}
        for i, fid in enumerate(field_ids):
            if i >= len(row):
                continue
            logical_kind = _INV_MAP.get(fid)
            if not logical_kind:
                continue
            logical, kind = logical_kind
            val = _unwrap(row[i])
            if kind == "tags":
                record[logical] = _parse_tags(val)
            elif kind == "number":
                record[logical] = _to_int(val)
            elif kind == "date":
                record[logical] = (val or "")[:19].replace("T", " ") if val else ""
            else:
                record[logical] = val if val is not None else ""

        git_url = extract_raw_url(record.get("git_url", ""))
        owner_repo = parse_owner_repo_from_url(git_url) or record.get("project_name", "")
        if not owner_repo:
            print(f"  ⏭ 跳过无 owner/repo 的记录：{record.get('project_name', '')}")
            skipped += 1
            continue

        fm = build_frontmatter(
            owner_repo=owner_repo,
            url=git_url,
            stars=0,
            project_type=record.get("project_type", ""),
            run_form=record.get("run_form", ""),
            target_user=record.get("target_user", ""),
            domain=record.get("domain", ""),
            summary=record.get("summary", ""),
            tags=record.get("tags", []),
            highlights=record.get("highlights", ""),
            doc_score=_to_int(record.get("doc_score")),
            func_score=_to_int(record.get("func_score")),
            community_score=_to_int(record.get("community_score")),
            total_score=_to_int(record.get("total_score")),
            source_kind="feishu-migration",
            imported_at=record.get("eval_date", ""),
            status=record.get("status", "已入库"),
        )

        path = os.path.join(lib, owner_repo_to_filename(owner_repo))
        if os.path.exists(path):
            skipped += 1
            continue
        if dry_run:
            print(f"  [dry] 将写入 {owner_repo_to_filename(owner_repo)}")
            written += 1
            continue
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(render_markdown(fm))
            append_to_imported_list(owner_repo)
            written += 1
        except OSError as e:
            print(f"  ❌ 写 {owner_repo} 失败：{e}")
            failed += 1

    print(f"\n✅ 迁移完成：写入 {written} 个，跳过 {skipped} 个（已存在/无 owner），失败 {failed} 个")
    print(f"   库目录：{lib}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印将要写入的文件，不实际写盘")
    args = ap.parse_args()
    sys.exit(migrate(dry_run=args.dry_run))
