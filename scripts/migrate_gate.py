"""迁移内容门禁（2026-09-07 创建，2026-09-08 转 v3 重抓方案）。

v3 语义（用户拍板）：**检测出清单、重抓替换**，不做正文级修复——
  1. dedup    剥 frontmatter 后正文 sha256 指纹去重（CRLF 归一化），保留无 -N 后缀者，
              其余移出 vault 归档（备份/归档绝不留在 vault 内，避免被 Obsidian 索引）。
  2. fm_sync  frontmatter title 与文件名 stem 不一致 → 同步为 stem。
  3. scan     只读检测 5 类异常（来源块重复/残留行/迁移文缺 source_url/多链接冲突/
              集数串位）+ 链接指纹分组（同 main_url 文件聚组，解决同链接不同标题盲区）。
  4. queue    把 scan 结果折叠成重抓队列（url/kind/folder_hint/old_paths/old_titles），
              只生成不执行；重抓由 articles.skill_main / videos 管线消费，
              成功落盘后才删旧文（用户拍板顺序），失败旧文原地保留停留清单。
  5. verify   三层核验：正文非空 / 集数一致 / fm title == stem，红绿报告。

原 realign(H1 改名)已删除：H1 不可靠（有的缺、有的是总结生成、有的是原标题），
串位文件直接进重抓队列，重抓产物文件名天然规范。B站重传场景 URL（BV 号）会变，
队列条目带 old_titles 供后续标题+内容相似度匹配，不能只凭 URL 比较。

默认 dry-run 只报告并输出异常清单（JSON+MD）到归档目录；--apply 才动文件
（dedup 归档移出 + fm_sync 同步）。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional, Sequence

_EP_IN_TEXT = re.compile(r"^#(?!#)\s*第\s*(\d+)\s*集", re.MULTILINE)
_EP_IN_STEM = re.compile(r"第\s*(\d+)\s*集")
_TITLE_LINE = re.compile(r"^title:\s*(.*?)\s*$", re.MULTILINE)
_MOVE_SUFFIX = re.compile(r"-\d+$")


def split_fm(text: str) -> tuple[str, str]:
    """拆 frontmatter 与正文；未闭合的 --- 整体视为正文，不误剥。"""
    if not text.startswith("---"):
        return "", text
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm = "\n".join(lines[: i + 1]) + "\n"
            body = "\n".join(lines[i + 1 :])
            return fm, body
    return "", text


def h1_episode(body: str) -> Optional[str]:
    """从正文第一个 H1 行提取「第N集」的 N；无集数返回 None。"""
    m = _EP_IN_TEXT.search(body)
    return m.group(1) if m else None


def stem_episode(stem: str) -> Optional[str]:
    """从文件名 stem 提取「第N集」的 N；无集数（如 20260519_日更）返回 None。"""
    m = _EP_IN_STEM.search(stem)
    return m.group(1) if m else None


def _unquote(v: str) -> str:
    """剥掉 YAML 值两侧成对引号：迁移产物 fm title 统一写法是 title: "..."，比较前必须剥掉。"""
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _iter_md(vault: str | Path, roots: Optional[Sequence[str]] = None) -> list[Path]:
    """roots 为 None 时全 vault 扫描；给定顶层目录名列表时只扫这些目录（管线范围守卫）。"""
    base = Path(vault)
    if not roots:
        return sorted(base.rglob("*.md"))
    out: list[Path] = []
    for r in roots:
        d = base / r
        if d.is_dir():
            out.extend(d.rglob("*.md"))
    return sorted(out)


def _body_fingerprint(text: str) -> str:
    _, body = split_fm(text)
    return hashlib.sha256(body.replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


def _keep_key(p: Path) -> tuple[int, str]:
    has_suffix = 1 if _MOVE_SUFFIX.search(p.stem) else 0
    return (has_suffix, str(p))


def dedup_step(
    vault: str, archive_dir: str, apply: bool = True, roots: Optional[Sequence[str]] = None
) -> dict:
    """正文指纹去重：同文组件保留无 -N 后缀者优先，其余移出 vault 归档。"""
    by_fp: dict[str, list[Path]] = defaultdict(list)
    for p in _iter_md(vault, roots):
        by_fp[_body_fingerprint(p.read_text(encoding="utf-8"))].append(p)
    groups: list[list[str]] = []
    moved: list[str] = []
    for paths in by_fp.values():
        if len(paths) < 2:
            continue
        ordered = sorted(paths, key=_keep_key)
        groups.append([str(p) for p in ordered])
        for p in ordered[1:]:
            if apply:
                dest = Path(archive_dir) / p.relative_to(vault)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(dest))
            moved.append(str(p))
    return {"groups": groups, "moved": moved}


# ---- v3 异常检测与重抓队列（2026-09-08）----

_URL_RE = re.compile(r"https?://[^\s\)\]）】」\"']+")
_BILI_VIDEO_RE = re.compile(r"(?:bilibili\.com/video/|b23\.tv/)", re.IGNORECASE)
_FM_URL_RE = re.compile(r"^source_url:\s*(\S+)\s*$", re.MULTILINE)
_SOURCE_LINK = "**来源链接**"
_RESIDUAL_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^<title>.*?</title>\s*$", re.MULTILINE),
    re.compile(r"^##\s*source_url:", re.MULTILINE),
    re.compile(r"^(?:freshness|published_at):\s*\S", re.MULTILINE),
)


def _norm_url(url: str) -> str:
    """链接归一化：剥 query/fragment/尾斜杠。BV 号大小写敏感，绝不改写大小写。"""
    url = url.strip()
    if not url:
        return ""
    for sep in ("#", "?"):
        i = url.find(sep)
        if i != -1:
            url = url[:i]
    return url.rstrip("/")


def _extract_urls(text: str) -> list[str]:
    """提取正文中的全部链接：归一化后去重（保出现顺序）。"""
    seen: dict[str, None] = {}
    for raw in _URL_RE.findall(text):
        u = _norm_url(raw)
        if u:
            seen.setdefault(u, None)
    return list(seen)


def _fm_url(fm: str) -> Optional[str]:
    """取 frontmatter 的 source_url 原始值（剥引号，不归一化）。"""
    m = _FM_URL_RE.search(fm)
    return _unquote(m.group(1)) if m else None


def _main_url(fm: str, body: str) -> tuple[Optional[str], list[str]]:
    """权威链接 = fm source_url 优先、正文链接兜底；返回 (权威链接, 全部唯一链接)。"""
    f_url = _fm_url(fm)
    urls = _extract_urls(body)
    seen: dict[str, None] = {}
    for u in ([f_url] if f_url else []) + urls:
        seen.setdefault(_norm_url(u), None)
    uniq = list(seen)
    main = _norm_url(f_url) if f_url else (uniq[0] if uniq else None)
    return main, uniq


def scan_step(vault: str, roots: Optional[Sequence[str]] = None) -> dict:
    """只读检测 5 类异常 + 链接指纹分组（同 main_url 文件聚组），输出重抓依据。

    5 类异常：dup_source_blocks（来源块≥2）/ residual_lines（残留行）/
    missing_fm_url（仅迁移文）/ multi_urls（链接集合≥2）/ episode_mismatch（串位）。
    H1 缺失不算异常——不为它单独花一次重抓。
    """
    by_url: dict[str, list[Path]] = defaultdict(list)
    issues: list[dict] = []
    counts: dict[str, int] = {
        "total": 0,
        "dup_source_blocks": 0,
        "residual_lines": 0,
        "missing_fm_url": 0,
        "multi_urls": 0,
        "episode_mismatch": 0,
    }
    for p in _iter_md(vault, roots):
        counts["total"] += 1
        text = p.read_text(encoding="utf-8")
        fm, body = split_fm(text)
        kinds: list[str] = []
        if body.count(_SOURCE_LINK) >= 2:
            kinds.append("dup_source_blocks")
        if any(rx.search(body) for rx in _RESIDUAL_RES):
            kinds.append("residual_lines")
        f_url = _fm_url(fm)
        main, urls = _main_url(fm, body)
        if "migrated_from" in fm and not f_url:
            kinds.append("missing_fm_url")
        if len(urls) >= 2:
            kinds.append("multi_urls")
        he, se = h1_episode(body), stem_episode(p.stem)
        if he is not None and se is not None and he != se:
            kinds.append("episode_mismatch")
        if main:
            by_url[main].append(p)
        if kinds:
            for k in kinds:
                counts[k] += 1
            issues.append({"path": str(p), "kinds": kinds, "urls": urls, "fm_url": f_url})
    link_groups = [
        {"url": u, "paths": [str(q) for q in ps]} for u, ps in sorted(by_url.items()) if len(ps) >= 2
    ]
    counts["dup_link_groups"] = len(link_groups)
    return {"issues": issues, "link_groups": link_groups, "counts": counts}


def regen_queue_from_scan(vault: str, scan: dict) -> list[dict]:
    """把 scan 结果折叠成重抓队列（只生成不执行）。

    链接重复组内文件合并为一条（一次重抓替换一组）；其余异常文件按链接聚合，
    无链接 → manual_no_url 等人工补链接。old_titles 取 fm title（剥引号），
    为 B站重传「标题+内容相似度匹配」留数据（URL 会变，不能只凭 URL 比较）。
    """
    vault_root = Path(vault).resolve()
    queue: list[dict] = []
    keyed: dict[str, dict] = {}

    def _entry_for(url: Optional[str], kind: str) -> dict:
        key = url or "__manual__"
        if key not in keyed:
            keyed[key] = {
                "url": url,
                "kind": kind,
                "folder_hint": "",
                "old_paths": [],
                "old_titles": [],
            }
            queue.append(keyed[key])
        return keyed[key]

    def _absorb(path_str: str, entry: dict) -> None:
        p = Path(path_str)
        entry["old_paths"].append(str(p))
        fm, _ = split_fm(p.read_text(encoding="utf-8"))
        m = _TITLE_LINE.search(fm)
        if m:
            entry["old_titles"].append(_unquote(m.group(1)))
        if not entry["folder_hint"]:
            try:
                entry["folder_hint"] = p.parent.resolve().relative_to(vault_root).as_posix()
            except ValueError:
                entry["folder_hint"] = p.parent.as_posix()

    grouped: set[str] = set()
    for g in scan["link_groups"]:
        entry = _entry_for(g["url"], "bili_video" if _BILI_VIDEO_RE.search(g["url"]) else "article")
        for path_str in g["paths"]:
            _absorb(path_str, entry)
            grouped.add(path_str)
    for issue in scan["issues"]:
        if issue["path"] in grouped:
            continue
        url = issue["urls"][0] if issue["urls"] else None
        kind = "manual_no_url" if not url else ("bili_video" if _BILI_VIDEO_RE.search(url) else "article")
        entry = _entry_for(url, kind)
        _absorb(issue["path"], entry)
    return queue


def fm_sync_step(vault: str, apply: bool = True, roots: Optional[Sequence[str]] = None) -> dict:
    """frontmatter title 与文件名 stem 不一致时，把 title 同步为 stem。

    仅对带「第N集」集数标记的系列笔记生效；日期命名等历史规范文件不动，
    fm title 保留原始抓取标题（2026-09-07 决策，避免覆盖 94 篇历史规范标题）。
    比较按 YAML 值剥引号（迁移产物统一 title: "..."），回写保留原引号风格。
    """
    synced: list[str] = []
    for p in _iter_md(vault, roots):
        if stem_episode(p.stem) is None:
            continue
        text = p.read_text(encoding="utf-8")
        fm, body = split_fm(text)
        if not fm:
            continue
        m = _TITLE_LINE.search(fm)
        if not m or _unquote(m.group(1)) == p.stem:
            continue
        raw = m.group(1)
        new_val = f'"{p.stem}"' if _unquote(raw) != raw else p.stem
        new_fm = fm[: m.start(1)] + new_val + fm[m.end(1) :]
        if apply:
            p.write_text(new_fm + body, encoding="utf-8", newline="")
        synced.append(str(p))
    return {"synced": synced}


def verify_step(vault: str, roots: Optional[Sequence[str]] = None) -> dict:
    """三层核验：正文非空 / 集数一致（两侧都有集数时）/ fm title == stem（仅集数标记文件）。"""
    failed: list[dict] = []
    for p in _iter_md(vault, roots):
        text = p.read_text(encoding="utf-8")
        fm, body = split_fm(text)
        if not body.strip():
            failed.append({"path": str(p), "reason": "正文为空"})
            continue
        he, se = h1_episode(body), stem_episode(p.stem)
        if he is not None and se is not None and he != se:
            failed.append({"path": str(p), "reason": f"集数错位: 文件名第{se}集 vs 正文第{he}集"})
        if fm and se is not None:
            m = _TITLE_LINE.search(fm)
            if m and _unquote(m.group(1)) != p.stem:
                failed.append({"path": str(p), "reason": "fm title 与文件名不一致"})
    return {"passed": not failed, "failed": failed}


def run_gate(
    vault: str, apply: bool, archive_dir: str, roots: Optional[Sequence[str]] = None
) -> dict:
    """v3 门禁串接：dedup → fm_sync → scan → queue → verify；roots 限定管线目录，None=全 vault。"""
    dedup = dedup_step(vault, archive_dir, apply=apply, roots=roots)
    fm_sync = fm_sync_step(vault, apply=apply, roots=roots)
    scan = scan_step(vault, roots=roots)
    queue = regen_queue_from_scan(vault, scan)
    verify = verify_step(vault, roots=roots)
    return {
        "dedup": dedup,
        "fm_sync": fm_sync,
        "scan": scan,
        "queue": queue,
        "verify": verify,
        "passed": verify["passed"],
        "apply": apply,
    }


def _write_scan_reports(archive: str, vault: str, apply: bool, rep: dict) -> tuple[str, str]:
    """把异常清单写成 JSON（机器消费）+ MD（人读）到归档目录，返回两个文件路径。"""
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    Path(archive).mkdir(parents=True, exist_ok=True)
    json_path = Path(archive) / f"migrate_scan_{stamp}.json"
    md_path = Path(archive) / f"migrate_scan_{stamp}.md"
    scan, queue = rep["scan"], rep["queue"]
    payload = {"generated_at": stamp, "vault": vault, "apply": apply, "scan": scan, "queue": queue}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# 迁移门禁扫描清单 {stamp}",
        "",
        f"- vault: {vault}",
        f"- 模式: {'APPLY' if apply else 'DRY-RUN'}",
        f"- 扫描文件 {scan['counts']['total']}，异常文件 {len(scan['issues'])}，"
        f"链接重复组 {scan['counts']['dup_link_groups']}，重抓队列 {len(queue)} 条",
        "",
        "## 异常明细",
        "",
        "| 文件 | 异常类型 | 正文链接 | fm 链接 |",
        "|---|---|---|---|",
    ]
    for it in scan["issues"]:
        lines.append(f"| {it['path']} | {','.join(it['kinds'])} | {len(it['urls'])} | {it['fm_url'] or ''} |")
    lines += [
        "",
        "## 链接重复组（同链接不同总结，正文指纹识别不了的盲区）",
        "",
    ]
    for g in scan["link_groups"]:
        lines.append(f"- {g['url']} × {len(g['paths'])}")
        lines.extend(f"  - {p}" for p in g["paths"])
    lines += [
        "",
        "## 重抓队列（只生成不执行；重抓成功落盘后才删旧文）",
        "",
        "| url | kind | folder_hint | old_paths | old_titles |",
        "|---|---|---|---|---|",
    ]
    for q in queue:
        lines.append(
            f"| {q['url'] or ''} | {q['kind']} | {q['folder_hint']} | "
            f"{'<br>'.join(q['old_paths'])} | {' / '.join(q['old_titles'])} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(json_path), str(md_path)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="迁移内容门禁 v3：去重/fm同步/异常扫描/重抓队列/核验（默认 dry-run 出清单）"
    )
    ap.add_argument("--vault", default=os.environ.get("OBSIDIAN_VAULT_PATH", ""), help="Obsidian vault 根目录")
    ap.add_argument("--apply", action="store_true", help="实际执行修复；默认只报告不动文件")
    ap.add_argument("--archive", default=None, help="去重归档与清单输出目录（默认 vault 同级 _migrate_gate_archive）")
    ap.add_argument(
        "--scope",
        default="AI 总结笔记",
        help="限定扫描的 vault 顶层目录，逗号分隔多个；传空串=全 vault（默认 AI 总结笔记）",
    )
    args = ap.parse_args()
    if not args.vault:
        print("错误: 未指定 --vault，且环境变量 OBSIDIAN_VAULT_PATH 未设置")
        return 2
    archive = args.archive or str(Path(args.vault).parent / "_migrate_gate_archive")
    roots = [s.strip() for s in args.scope.split(",") if s.strip()] or None
    rep = run_gate(args.vault, apply=args.apply, archive_dir=archive, roots=roots)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== 迁移门禁 v3 [{mode}] vault={args.vault} ===")
    d = rep["dedup"]
    print(f"[1/5 dedup] 重复组 {len(d['groups'])}，移出归档 {len(d['moved'])} 个文件")
    for g in d["groups"]:
        print("  同文组: " + " | ".join(g))
    f = rep["fm_sync"]
    print(f"[2/5 fm_sync] 同步 title {len(f['synced'])} 个文件")
    scan = rep["scan"]
    c = scan["counts"]
    print(
        f"[3/5 scan] 扫描 {c['total']}，异常 {len(scan['issues'])}（来源块重复 {c['dup_source_blocks']}"
        f" / 残留行 {c['residual_lines']} / 缺fm链接 {c['missing_fm_url']} / 多链接 {c['multi_urls']}"
        f" / 集数串位 {c['episode_mismatch']}）"
    )
    for it in scan["issues"]:
        print(f"  异常: {it['path']} ({','.join(it['kinds'])})")
    print(f"[3/5 scan] 链接重复组 {c['dup_link_groups']} 组")
    for g in scan["link_groups"]:
        print(f"  组: {g['url']} × {len(g['paths'])}")
        for p in g["paths"]:
            print(f"    - {p}")
    queue = rep["queue"]
    print(f"[4/5 queue] 重抓队列 {len(queue)} 条（只生成不执行）")
    for q in queue:
        print(f"  [{q['kind']}] {q['url'] or '(无链接)'} old_paths={len(q['old_paths'])} -> {q['folder_hint']}")
    v = rep["verify"]
    print(f"[5/5 verify] {'PASS' if v['passed'] else 'FAIL'}，未通过 {len(v['failed'])} 项")
    for item in v["failed"]:
        print(f"  未通过: {item['path']} ({item['reason']})")
    json_path, md_path = _write_scan_reports(archive, args.vault, args.apply, rep)
    print(f"清单已写入: {json_path}")
    print(f"清单已写入: {md_path}")
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
