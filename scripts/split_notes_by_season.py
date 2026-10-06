"""把已落盘在【日更】的 UP 笔记按官方合集（ugc_season）分拣迁移。

背景（2026-10-06）：UP 全量补齐线此前不做合集识别，全部笔记落
【监控】/<平台>/<账号>/日更；up_seasons + fetch_up_range 补齐合集识别后，
本脚本一次性把存量笔记按「官方合集→BV→标题匹配」迁到
【监控】/<平台>/<账号>/<合集名>/，未命中合集的留在【日更】。

机械五步（线性主干，无隐藏分支）：
  A. 加载 .env → vault 根（OBSIDIAN_VAULT_PATH）
  B. 合集映射（up_seasons.fetch_seasons_map，带缓存）
  C. 视频列表缓存（bili_<uid>_videos.json）→ bvid→(title, created)
  D. 扫【日更】目录，文件名去日期前缀后与视频标题 difflib 匹配（≥0.60）
  E. dry-run 打印迁移清单；--apply 执行移动 + 回写 summary_registry.folder

默认 dry-run，只读；--apply 才动文件。匹配不上/目标重名的一律不移动并
显式列出，绝不静默丢弃。
"""
import argparse
import difflib
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

REGISTRY_PATH = os.path.join(ROOT, "notes", "_meta", "summary_registry.json")
DATE_PREFIX_RE = re.compile(r"^\d{8}_")
MATCH_RATIO = 0.60


def step_a_load_vault() -> str:
    from shared.env import load_env
    load_env()
    vault = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
    if not vault:
        raise SystemExit("❌ OBSIDIAN_VAULT_PATH 未配置")
    return vault


def step_c_video_index(uid: int) -> dict:
    p = os.path.join(ROOT, "notes", "_scraped", f"bili_{uid}_videos.json")
    items = json.load(open(p, encoding="utf-8"))["items"]
    return {it["bvid"]: {"title": it.get("title", ""), "created": it.get("created", 0)}
            for it in items}


def step_d_match_files(daily_dir: str, video_index: dict,
                       season_map: dict) -> list:
    """返回 [{file, bvid, season, score, title}]，每文件一条。

    匹配两级：①登记表 filename 反查（精确、确定，解决同名标题歧义——
    如两集同名《温故而知新》对应不同 BV，difflib 会错认）；②difflib 标题
    相似度兜底（≥MATCH_RATIO，登记表未覆盖的手贴笔记用）。
    """
    reg = json.load(open(REGISTRY_PATH, encoding="utf-8"))
    by_filename = {}
    if isinstance(reg, dict):
        for e in reg.values():
            if isinstance(e, dict) and e.get("filename") and e.get("source_url"):
                by_filename[os.path.basename(e["filename"])] = e["source_url"]
    titles = [(bv, v["title"]) for bv, v in video_index.items() if v["title"]]
    results = []
    for fn in sorted(os.listdir(daily_dir)):
        if not fn.endswith(".md"):
            continue
        url = by_filename.get(fn, "")
        bv = url.rsplit("/", 1)[-1] if url else ""
        score = 1.0 if bv else 0.0
        if not bv:
            stem = DATE_PREFIX_RE.sub("", fn[:-3])
            for b, title in titles:
                r = difflib.SequenceMatcher(None, stem, title).ratio()
                if r > score:
                    bv, score = b, r
            if not bv or score < MATCH_RATIO:
                bv = ""
        if not bv:
            results.append({"file": fn, "bvid": "", "season": "",
                            "score": score, "title": ""})
            continue
        results.append({"file": fn, "bvid": bv,
                        "season": season_map.get(bv, ""),
                        "score": score,
                        "title": video_index.get(bv, {}).get("title", "")})
    return results


def _reg_key(url: str) -> str:
    """登记表键 = sha256(url)[:16]（与 articles.dedup._key_for 同口径）。"""
    import hashlib
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:16]


def step_e_apply(plan: list, daily_dir: str, author_dir: str, platform: str,
                 author: str, dry_run: bool) -> dict:
    stats = {"to_move": 0, "stay_no_season": 0, "stay_unmatched": 0,
             "clash": 0, "registry_updated": 0}
    reg = json.load(open(REGISTRY_PATH, encoding="utf-8"))
    reg_changed = False
    for it in plan:
        if not it["bvid"]:
            stats["stay_unmatched"] += 1
            print(f"  ? 未匹配视频（留【日更】）: {it['file']}（best={it['score']:.2f}）")
            continue
        if not it["season"]:
            stats["stay_no_season"] += 1
            continue
        stats["to_move"] += 1
        src = os.path.join(daily_dir, it["file"])
        dst_dir = os.path.join(author_dir, it["season"])
        dst = os.path.join(dst_dir, it["file"])
        url = f"https://www.bilibili.com/video/{it['bvid']}"
        print(f"  → {it['season']}/{it['file']}  (match={it['score']:.2f}, {url})")
        if os.path.exists(dst):
            stats["clash"] += 1
            print(f"  ⚠️ 目标已存在，跳过: {dst}")
            continue
        if not dry_run:
            if os.path.exists(src):
                os.makedirs(dst_dir, exist_ok=True)
                shutil.move(src, dst)
            elif os.path.exists(dst):
                pass  # 文件已迁移过（重跑）：只做登记表修复
            else:
                print(f"  ⚠️ 源与目标都不存在，仅跳过: {src}")
                continue
            entry = reg.get(_reg_key(url)) if isinstance(reg, dict) else None
            if isinstance(entry, dict):
                entry["folder"] = f"【监控】/{platform}/{author}/{it['season']}"
                fn = entry.get("filename") or ""
                if "日更" in fn:
                    entry["filename"] = fn.replace("/日更/", f"/{it['season']}/")
                reg_changed = True
                stats["registry_updated"] += 1
    if not dry_run and reg_changed:
        with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
            json.dump(reg, f, ensure_ascii=False, indent=1)
    return stats


def main():
    ap = argparse.ArgumentParser(
        description="把【日更】里属于官方合集的笔记分拣迁移到合集目录（默认 dry-run）")
    ap.add_argument("--uid", type=int, required=True)
    ap.add_argument("--author", required=True)
    ap.add_argument("--platform", default="B站")
    ap.add_argument("--apply", action="store_true", help="执行移动与登记表回写")
    ap.add_argument("--refresh-seasons", action="store_true")
    args = ap.parse_args()

    # A. vault 根
    vault = step_a_load_vault()
    # B+C. 合集映射 + 视频索引
    from scripts.up_seasons import fetch_seasons_map
    season_map = fetch_seasons_map(args.uid, force=args.refresh_seasons)
    video_index = step_c_video_index(args.uid)
    seasons_present = sorted(set(season_map.values()))
    print(f"[B] 合集映射 {len(season_map)} 条 BV / {len(seasons_present)} 个合集: "
          f"{'、'.join(seasons_present)}")
    # D. 匹配
    daily_dir = os.path.join(vault, "【监控】", args.platform, args.author, "日更")
    author_dir = os.path.join(vault, "【监控】", args.platform, args.author)
    if not os.path.isdir(daily_dir):
        raise SystemExit(f"❌ 日更目录不存在: {daily_dir}")
    n_files = len([f for f in os.listdir(daily_dir) if f.endswith(".md")])
    print(f"[D] 【日更】共 {n_files} 个文件，开始标题匹配（阈值 {MATCH_RATIO}）")
    plan = step_d_match_files(daily_dir, video_index, season_map)
    # E. 迁移
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[E] {mode} 迁移计划：")
    stats = step_e_apply(plan, daily_dir, author_dir, args.platform,
                         args.author, dry_run=not args.apply)
    print("\n===== 统计 =====")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"  日更剩余: {len([f for f in os.listdir(daily_dir) if f.endswith('.md')])}")
    if args.apply:
        from shared.routing import resolve_folder
        sample = next((it for it in plan if it["season"]), None)
        if sample:
            print("[校验] 路由器口径: " + resolve_folder(
                {"author": args.author, "series": sample["season"],
                 "url": f"https://www.bilibili.com/video/{sample['bvid']}"}))


if __name__ == "__main__":
    main()
