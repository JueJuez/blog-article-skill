#!/usr/bin/env python3
"""scripts/vault_lifecycle.py — vault 生命周期工具（2026-09-06，本地保留机制的对账半边）。

架构决策（用户 2026-09-06 拍板由 AI 定）：
- **记录 = 真源**：dedup 索引（.cache/dedup.json）+ series_state + manifest 记录
  「是否已总结」，**永不清理**——本地/vault 文件删了它也知道哪些总结过。
- **vault 文件 = 上传载荷**：Obsidian vault 本身就是本地 markdown（即"本地保留"），
  audit_sync.py 负责 vault→飞书镜像（本地→云单向）。
- **本工具补四个缺口**（2026-09-07 任务5：audit_sync 薄壳化，对账补传逻辑收编至此）：
  ① reconcile：记录命中但 vault 文件丢失 → 清记录，下次补齐自动重抓重总结
     （用户语义："有就直接上传，没有就重新总结再上传"）。
     P0-1 收紧（DECISION-20260907）：--apply 只清「两端 link 均空」的丢失记录
     （lost_never）；带任一 link 的保留报告（lost_linked），防误清飞书时代记录。
  ② old-report：vault 内超过 N 天的笔记文件清单（**只报告不删除**，归档/清理由用户人工裁决）。
  ③ gc：淘汰「已总结完」的中间产物（PLAN-20260906 P0-1 B 方案——只清 raw/transcripts，
     **成品笔记不动**，淘汰动作全部进 journal；scys 归档等 `_` 开头子目录永不扫描）。
  ④ push：对账 + 幂等补传 + 死链清理 + 登记表 link 回填（原 audit_sync.py run_audit
     语义，逻辑从 audit_sync.py 收编；audit_sync.py 退化为薄壳 CLI 转发）。
     任一入口（push 自身除外）距上次 sync_run 超 7 天 → 自动被动补跑
     maybe_passive_sync（gc 不在被动补跑清单）。

gc 门槛矩阵（DECISION-20260907 + PLAN §7 P1-6/P2-2/P2-5/P2-6）：
- 系列分片 notes/<子目录>/<集数>_raw.md：同名 .body.md 存在 → 可清；缺失 → 留。
- 文章 raw notes/_raw_*.md：标题命中登记表（normalize 前缀匹配，长度≥6 防误清）
  且命中记录 URL 不含不可再生域（默认 mp.weixin.qq.com，.env GC_NONREGEN_HOSTS
  覆盖）→ 可清；公众号等不可再生来源永久留。
- transcripts/BV*.md：BV 号出现在登记表任一 source_url → 可清；
  非 BV 命名（YouTube key / md5，不可确定性反推）→ 保守留。

注意：dedup 索引只存 URL/内容 hash + title + filename，没有 folder。reconcile 按
filename 在 vault 内递归找同名文件；找不到 = 文件丢失。只处理 URL 为指定域名的条目
（默认 bilibili.com/video，避免误清公众号/scys 等非 vault 管理的记录——那些记录的
filename 本来就不在 vault 里）。

用法：
  python scripts/vault_lifecycle.py reconcile --dry            # 预览丢失文件的记录
  python scripts/vault_lifecycle.py reconcile --apply --scope-substr <关键词>   # 清除丢失记录（仅两端 link 均空的）
  python scripts/vault_lifecycle.py old-report --days 90       # 90 天前文件清单（只读）
  python scripts/vault_lifecycle.py gc --dry                   # 预览可淘汰的中间产物
  python scripts/vault_lifecycle.py gc --apply                 # 执行淘汰（动作进 journal）
  python scripts/vault_lifecycle.py push --dry                 # 对账报告（不改数据）
  python scripts/vault_lifecycle.py push --apply               # 对账 + 补传 + 死链清理 + link 回填
"""
import argparse
import json
import os
import re
import sys
import time
from typing import Iterable, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
# _run_push 函数体内 `import audit_sync` 需要从 scripts/ 目录加载薄壳模块；
# 测试经 importlib 独立加载本文件时该目录未必在 sys.path，故在此显式注入。
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))
VAULT = os.environ.get("OBSIDIAN_VAULT_PATH", "")

# ── gc 常量（P2-5：豁免规则显式落配置，不散落硬编码）────────────────────────
_JOURNAL_FILE = os.path.join(ROOT, ".cache", "sync_journal.jsonl")
_EXEMPT_SUBDIR_PREFIX = "_"   # notes 一级子目录 `_` 开头永不扫描（_scraped=scys 归档豁免）
_NONREGEN_HOSTS_DEFAULT = "mp.weixin.qq.com"  # 不可再生域默认值，.env GC_NONREGEN_HOSTS 覆盖
_BV_RE = re.compile(r"^BV[0-9A-Za-z]{8,12}$")
_MIN_TITLE_NORM_LEN = 6       # 标题 normalize 后低于此长度不参与登记表匹配（防误清）
_RAW_TS_SUFFIX_RE = re.compile(r"-\d{8}-\d{6}$")


def _vault_files_index() -> dict:
    """vault 内 filename -> 完整路径 索引（一次扫描）。"""
    idx = {}
    for dirpath, _dirs, files in os.walk(VAULT):
        for fn in files:
            if fn.endswith(".md") and fn not in idx:
                idx[fn] = os.path.join(dirpath, fn)
    return idx


# ── journal 基础设施（P2-6：淘汰/运行动作全部进 jsonl，判定读 journal 非 mtime）──


def journal_append(action: str, ts: Optional[int] = None, **fields) -> None:
    """追加一条操作记录（一行一 JSON）；ts 允许显式覆盖（测试/回填用）。"""
    rec = {"ts": int(ts) if ts is not None else int(time.time()),
           "action": action, **fields}
    parent = os.path.dirname(_JOURNAL_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(_JOURNAL_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def journal_last_run(action: str = "gc_run") -> Optional[int]:
    """最近一次指定 action 的 ts；journal 缺失/无有效记录 → None（损坏行跳过）。"""
    if not os.path.exists(_JOURNAL_FILE):
        return None
    last = None
    try:
        with open(_JOURNAL_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                if rec.get("action") == action:
                    last = rec.get("ts")
    except OSError:
        return None
    return last


def should_run_gc(days: int = 7, now: Optional[float] = None) -> bool:
    """距上次 gc_run 超过 days 天（或从未跑过）→ True（被动补跑判定依据）。"""
    last = journal_last_run("gc_run")
    if last is None:
        return True
    cur = now if now is not None else time.time()
    return cur - last > days * 86400


def should_run_sync(days: int = 7, now: Optional[float] = None) -> bool:
    """距上次 sync_run 超过 days 天（或从未跑过）→ True（被动补跑判定依据）。"""
    last = journal_last_run("sync_run")
    if last is None:
        return True
    cur = now if now is not None else time.time()
    return cur - last > days * 86400


# ── gc 标题提取 / 豁免域清单 ────────────────────────────────────────────────


def _nonregen_hosts() -> tuple:
    """不可再生域清单（P2-5）：.env GC_NONREGEN_HOSTS 逗号分隔，缺省 mp.weixin.qq.com。"""
    raw = os.environ.get("GC_NONREGEN_HOSTS", _NONREGEN_HOSTS_DEFAULT)
    return tuple(h.strip() for h in raw.split(",") if h.strip())


def _raw_title_from_file(path: str) -> str:
    """从 raw 文件 header 提取「> 标题：」行（save_raw_content_to_file 产出格式）。"""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s.startswith("> 标题："):
                    return s[len("> 标题："):].strip()
                if s and not s.startswith(">"):
                    break  # header 区结束
    except OSError:
        return ""
    return ""


def _raw_title_from_filename(fn: str) -> str:
    """文件名 fallback：_raw_<标题>-<YYYYMMDD-HHMMSS>.md → <标题>（可能被截断 30 字）。"""
    stem = fn[len("_raw_"):] if fn.startswith("_raw_") else fn
    if stem.endswith(".md"):
        stem = stem[:-len(".md")]
    return _RAW_TS_SUFFIX_RE.sub("", stem)


# ── gc 目标收集（只收集不删除）──────────────────────────────────────────────


def collect_gc_targets(notes_dir: str, transcripts_dir: str, index,
                       nonregen_hosts: Optional[Iterable[str]] = None) -> list:
    """按门槛矩阵收集可淘汰的中间产物（raw/transcripts），不动任何成品笔记。

    index 为 dedup 登记表（key -> {source_url, title, ...}）。
    """
    from articles import dedup
    if nonregen_hosts is None:
        nonregen_hosts = _nonregen_hosts()
    records = list(index.values()) if isinstance(index, dict) else list(index)
    targets = []

    # ① 系列分片（P1-6：与 apply_pending_series.py 的 .body.md 存在性判定一致）
    if os.path.isdir(notes_dir):
        for name in sorted(os.listdir(notes_dir)):
            sub = os.path.join(notes_dir, name)
            if not os.path.isdir(sub) or name.startswith(_EXEMPT_SUBDIR_PREFIX):
                continue
            for fn in os.listdir(sub):
                if not fn.endswith("_raw.md"):
                    continue
                raw = os.path.join(sub, fn)
                body = raw[:-len("_raw.md")] + ".body.md"
                if os.path.exists(body):
                    targets.append({"path": raw, "kind": "series_raw",
                                    "reason": "body 已生成，字幕分片可清"})

    # ② 文章 raw：标题命中登记表且来源非不可再生域 → 可清（公众号等永久留）
    if os.path.isdir(notes_dir):
        norm_pairs = []
        for rec in records:
            n_title = dedup.normalize_title(rec.get("title", ""))
            if n_title:
                norm_pairs.append((n_title, rec.get("source_url", "")))
        for fn in sorted(os.listdir(notes_dir)):
            if not (fn.startswith("_raw_") and fn.endswith(".md")):
                continue
            p = os.path.join(notes_dir, fn)
            if not os.path.isfile(p):
                continue
            title = _raw_title_from_file(p) or _raw_title_from_filename(fn)
            n_raw = dedup.normalize_title(title)
            hit_url = ""
            for n_rec, url in norm_pairs:
                if min(len(n_raw), len(n_rec)) >= _MIN_TITLE_NORM_LEN and (
                        n_rec.startswith(n_raw) or n_raw.startswith(n_rec)):
                    hit_url = url
                    break
            if not hit_url:
                continue
            if any(host in hit_url for host in nonregen_hosts):
                continue
            targets.append({"path": p, "kind": "article_raw",
                            "reason": "登记表已总结，raw 可清"})

    # ③ transcripts：BV 模式文件名且 BV 出现在登记表任一 source_url → 可清
    if os.path.isdir(transcripts_dir):
        all_urls = [r.get("source_url", "") for r in records]
        for fn in sorted(os.listdir(transcripts_dir)):
            if not fn.endswith(".md"):
                continue
            stem = fn[:-len(".md")]
            if not _BV_RE.fullmatch(stem):
                continue  # YouTube key / md5 名不可确定性反推 → 保守留
            if any(stem in u for u in all_urls):
                targets.append({"path": os.path.join(transcripts_dir, fn),
                                "kind": "transcript",
                                "reason": "视频已总结，字幕缓存可清"})
    return targets


def cmd_reconcile(apply: bool, scope_substr: str = "") -> None:
    from articles import dedup
    if not VAULT or not os.path.isdir(VAULT):
        print(f"❌ OBSIDIAN_VAULT_PATH 未配置或不存在：{VAULT}")
        sys.exit(1)
    if apply and not scope_substr:
        print("❌ --apply 必须搭配 --scope-substr <关键词>（UP名/公众号名等）。\n"
              "   原因：dedup 索引里混着飞书时代记录（其 filename 本来就不在 vault），\n"
              "   无范围的全量清除会误清它们导致整批重总结。先 dry 看清单，再按范围清。")
        sys.exit(1)
    index = dedup._load_index()
    vfiles = _vault_files_index()
    lost_never = []   # 两端 link 均空：从未上传过 → 可安全清（下次补齐重抓重总结）
    lost_linked = []  # 带任一 link：有同步痕迹 → 保留（防误清飞书时代记录）
    for h, rec in index.items():
        title, filename = rec.get("title", ""), rec.get("filename", "")
        if not filename or not filename.endswith(".md"):
            continue  # 非 markdown 记录不动，避免误清
        if scope_substr and scope_substr not in title and scope_substr not in filename:
            continue
        # 索引里 filename 可能带 vault 相对路径（folder/xxx.md），按 basename 对账
        base = os.path.basename(filename)
        if base in vfiles:
            continue
        if rec.get("feishu_link") or rec.get("obsidian_link"):
            lost_linked.append((h, title, base))
        else:
            lost_never.append((h, title, base))
    lost = lost_never + lost_linked
    print(f"[reconcile] 索引 {len(index)} 条；vault 文件 {len(vfiles)} 个；"
          f"范围「{scope_substr or '全部(仅报告)'}」内记录在但 vault 文件丢失 {len(lost)} 条"
          f"（从未上传 {len(lost_never)} / 带同步痕迹 {len(lost_linked)}）：")
    for h, title, base in lost_never[:30]:
        print(f"    - {title[:40]}  ({base})")
    if len(lost) > 30:
        print(f"    …共 {len(lost)} 条")
    if not apply:
        print("\n[dry] 未做改动。确认范围后加 --scope-substr <关键词> --apply 清除"
              "（下次补齐将自动重抓重总结；带 link 的记录保留不动）。")
        return
    for h, _t, _f in lost_never:
        index.pop(h, None)
    dedup._save_index(index)
    print(f"✅ 已清除 {len(lost_never)} 条丢失记录（两端 link 均空），"
          f"保留 {len(lost_linked)} 条带同步痕迹记录，dedup 索引剩 {len(index)} 条。")


def cmd_old_report(days: int) -> None:
    if not VAULT or not os.path.isdir(VAULT):
        print(f"❌ OBSIDIAN_VAULT_PATH 未配置或不存在：{VAULT}")
        sys.exit(1)
    cutoff = time.time() - days * 86400
    old = []
    for dirpath, _dirs, files in os.walk(VAULT):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            p = os.path.join(dirpath, fn)
            mtime = os.path.getmtime(p)
            if mtime < cutoff:
                old.append((mtime, p))
    old.sort()
    print(f"[old-report] vault 内超过 {days} 天未改动的笔记 {len(old)} 个"
          f"（只报告，不删除；清理请人工裁决后自行操作）：")
    for mtime, p in old[:50]:
        print(f"    - {time.strftime('%Y-%m-%d', time.localtime(mtime))}  {os.path.relpath(p, VAULT)}")
    if len(old) > 50:
        print(f"    …共 {len(old)} 个")


def cmd_gc(apply: bool, notes_dir: str = "", transcripts_dir: str = "") -> None:
    """淘汰已总结完的中间产物（B 方案：raw/transcripts 可清，成品笔记永不动）。"""
    from articles import dedup
    notes_dir = notes_dir or os.path.join(ROOT, "notes")
    transcripts_dir = transcripts_dir or os.path.join(ROOT, "transcripts")
    targets = collect_gc_targets(notes_dir, transcripts_dir, dedup._load_index())
    print(f"[gc] 中间产物扫描（notes={notes_dir}）：可淘汰 {len(targets)} 项")
    for t in targets:
        print(f"    - [{t['kind']}] {t['path']}  ({t['reason']})")
    if not targets:
        print("[gc] 无可淘汰中间产物。")
    if not apply:
        print("\n[dry] 未做改动。确认后加 --apply 执行淘汰（动作进 sync_journal.jsonl）。")
        return
    evicted = 0
    for t in targets:
        try:
            os.remove(t["path"])
        except FileNotFoundError:
            continue  # collect 与 remove 之间被外部删除 → 跳过不崩
        evicted += 1
        journal_append("gc_evict", kind=t["kind"], path=t["path"], reason=t["reason"])
    journal_append("gc_run", evicted=evicted)
    print(f"✅ 已淘汰 {evicted} 项中间产物（成品不动，记录见 {_JOURNAL_FILE}）。")


# ── push：对账 + 补传 + 死链清理 + link 回填（任务5，自 audit_sync.py 收编）──


def _set_ledger_links_by_note(dedup_mod, base_name: str,
                              feishu_link: str, obsidian_link: str) -> int:
    """按笔记 basename 在登记表查唯一命中记录并回填双端 link；返回回填条数。

    命中 0 条（push 新笔记但无登记记录）或多条（不同目录同名歧义）→ 0，
    保守不写——link 只跟唯一确定的记录走。
    """
    index = dedup_mod._load_index()
    hits = [k for k, rec in index.items()
            if os.path.basename(rec.get("filename", "")) == base_name]
    if len(hits) != 1:
        return 0
    dedup_mod.set_links_by_key(hits[0], feishu_link=feishu_link,
                               obsidian_link=obsidian_link)
    return 1


def _run_push(apply: bool, vault: str = "", only: str = "") -> Optional[dict]:
    """对账 vault→飞书，返回七键结果 dict；门禁未过（vault 缺失/飞书不可用）返回 None。

    apply=False 仅报告；apply=True：死链清理（登记表 feishu_link 指向飞书树外
    token → 只清 feishu_link）→ 幂等补传缺飞书笔记（写回 feishu_node_token）→
    复验回填登记表双端 link → journal 记 sync_run（仅 apply）。
    """
    import audit_sync
    from articles import dedup
    from articles.feishu import FeishuOutput
    root = vault or VAULT
    if not root or not os.path.isdir(root):
        print(f"❌ vault 目录未配置或不存在：{root}")
        return None
    feishu = FeishuOutput()
    if not feishu.is_available():
        print("❌ 飞书不可用（需 FEISHU_WIKI_SPACE + lark-cli），无法对账补传。")
        return None
    parent = feishu.wiki_parent_node
    if not parent:
        print("❌ 未配置 FEISHU_WIKI_PARENT_NODE，无法对账补传。")
        return None
    obs = audit_sync.collect_obs(root)
    only_path = ""
    fei: dict = {}
    dead_links_cleared = 0
    if only:
        # 单篇模式：obs 过滤到指定文件，跳过整树扫描与死链清理
        only_path = os.path.abspath(only)
        obs = {k: v for k, v in obs.items() if os.path.abspath(v) == only_path}
        if not obs:
            print(f"⚠️ --only 指定的文件不在 vault 成品树内或非 .md：{only}")
            return {"missing": 0, "pushed": 0, "skipped": 0, "failed": 0,
                    "dead_links_cleared": 0, "ledger_linked": 0, "orphans": 0}
    else:
        fei = audit_sync.collect_feishu(feishu, parent)
        if apply:
            fei_tokens = set(fei.values())
            for h, rec in dedup._load_index().items():
                link = rec.get("feishu_link", "")
                tok = audit_sync.extract_feishu_token_from_url(link) or link
                if tok and tok not in fei_tokens:
                    dedup.set_links_by_key(h, feishu_link="")
                    dead_links_cleared += 1
    if only:
        missing = audit_sync.classify_only(feishu, only_path, root)
    else:
        missing = audit_sync.classify_missing(obs, set(fei.keys()),
                                              set(fei.values()))
    skipped = len(obs) - len(missing)
    pushed = failed = 0
    if apply and missing:
        created, _idle_skipped, push_failed, _tw = audit_sync.push_missing(
            feishu, missing)
        pushed, failed = created, push_failed
    ledger_linked = 0
    if apply:
        if only:
            # 单篇回写：push 成功且本地 frontmatter 确实拿到 token 才回填，
            # 防 push 失败时把登记表里已有的 link 清空。
            if pushed:
                _k, path = missing[0]
                fm_tok = ((audit_sync.parse_frontmatter(path)
                           .get("feishu_node_token")) or "").strip()
                if fm_tok:
                    rel = os.path.relpath(path, root).replace(os.sep, "/")
                    ledger_linked += _set_ledger_links_by_note(
                        dedup, os.path.basename(path), fm_tok, rel)
        else:
            # 全量复验回填：push 后重扫飞书树，逐篇惰性回填双端 link
            fei2 = audit_sync.collect_feishu(feishu, parent)
            for key, path in obs.items():
                tok = fei2.get(key, "")
                if not tok:
                    continue
                audit_sync.write_feishu_node_token(path, tok)
                rel = os.path.relpath(path, root).replace(os.sep, "/")
                ledger_linked += _set_ledger_links_by_note(
                    dedup, os.path.basename(path), tok, rel)
    if apply:
        journal_append("sync_run", pushed=pushed, skipped=skipped,
                       failed=failed, dead_links_cleared=dead_links_cleared,
                       ledger_linked=ledger_linked)
    orphans = 0 if only else len(sorted(set(fei.keys()) - set(obs)))
    print(f"[push] 缺飞书 {len(missing)} / 补传 {pushed} / 已同步 {skipped} / "
          f"失败 {failed} / 清死链 {dead_links_cleared} / "
          f"回填登记 {ledger_linked} / 孤儿 {orphans}"
          + ("" if apply else "（dry，未写任何数据）"))
    return {"missing": len(missing), "pushed": pushed, "skipped": skipped,
            "failed": failed, "dead_links_cleared": dead_links_cleared,
            "ledger_linked": ledger_linked, "orphans": orphans}


def cmd_push(apply: bool, vault_dir: str = "", only: str = "") -> None:
    res = _run_push(apply=apply, vault=vault_dir, only=only)
    if res is None:
        sys.exit(1)


def maybe_passive_sync(days: int = 7) -> None:
    """被动同步：距上次 sync_run 超过 days 天 → 自动补跑对账+补传（吞异常不外泄）。"""
    if not should_run_sync(days):
        return
    print(f"[被动同步] 距上次 sync_run 超过 {days} 天，自动补跑对账+补传…")
    try:
        res = _run_push(apply=True)
    except Exception as e:  # noqa: BLE001 — 被动路径吞一切异常，不影响当前命令
        print(f"[被动同步] 补跑失败（已忽略，不影响当前命令）：{e}")
        return
    if res is not None:
        print(f"[被动同步] 完成：补传 {res['pushed']}，"
              f"清死链 {res['dead_links_cleared']}。")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("reconcile", help="记录 vs vault 文件对账")
    p1.add_argument("--apply", action="store_true")
    p1.add_argument("--scope-substr", dest="scope_substr", default="",
                    help="只对账标题/文件名含此关键词的记录（--apply 必带）")
    p2 = sub.add_parser("old-report", help="超期文件清单（只读）")
    p2.add_argument("--days", type=int, default=90)
    p3 = sub.add_parser("gc", help="淘汰已总结中间产物（raw/transcripts，成品不动）")
    p3.add_argument("--apply", action="store_true", help="执行淘汰（默认 dry 预览）")
    p3.add_argument("--notes-dir", default="", help="中间产物 notes 目录（默认项目根 notes/）")
    p3.add_argument("--transcripts-dir", default="", help="字幕缓存目录（默认项目根 transcripts/）")
    p4 = sub.add_parser("push", help="对账 vault→飞书 + 幂等补传 + 死链清理 + link 回填")
    p4.add_argument("--apply", action="store_true", help="执行补传（默认 dry 报告）")
    p4.add_argument("--target", default="feishu", choices=["feishu"],
                    help="补传目标（当前仅 feishu）")
    p4.add_argument("--only", default="", help="只处理单篇笔记(路径)，测试/定点补传")
    p4.add_argument("--vault", dest="vault_dir", default="",
                    help="Obsidian vault 根（覆盖 OBSIDIAN_VAULT_PATH）")
    args = ap.parse_args()
    if args.cmd != "push":
        maybe_passive_sync(7)
    if args.cmd != "gc" and should_run_gc(7):
        print("[提示] 距上次 gc 超过 7 天，中间产物可能堆积；"
              "可跑 `python scripts/vault_lifecycle.py gc --dry` 预览。")
    if args.cmd == "reconcile":
        cmd_reconcile(args.apply, args.scope_substr)
    elif args.cmd == "gc":
        cmd_gc(args.apply, args.notes_dir, args.transcripts_dir)
    elif args.cmd == "push":
        cmd_push(args.apply, args.vault_dir, args.only)
    else:
        cmd_old_report(args.days)


if __name__ == "__main__":
    main()
