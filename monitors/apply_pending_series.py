"""monitors/apply_pending_series.py — 系列课降级待总结队列 drainer（续跑安全版）。

背景：FORCE_AGENT_MODE=1（无外部 AI）下，B站系列课降级只产出
notes/<系列名>/*_raw.md（字幕），真正总结必须由执行模型（Agent）完成。
monitors/run.py 在发现系列且降级时会把系列登记到 pending_series.json。

本脚本负责收尾闭环：
1. 读 pending_series.json（monitors/run.py 登记的系列课）。
2. 对每个系列：从磁盘 .body.md 派生「待落盘候选」，串行（分批）调
   videos.main._save_series_note 落飞书，更新 manifest 状态机
   （summarized → landed → verified），重生成系列总览。
3. ⚠️ 关键变更（优化 B）：落盘成功后【不再立即删除本地 body/raw】，
   只有 --cleanup 且 manifest=verified（飞书回读确认存在）时才删本地源，
   避免中断后失去「已落盘」信号、被迫手动对账。
4. 命名自愈（优化 E）：扫描并纠正 *_body.md 误命名（drain 只认 .body.md）。
5. 分批 + 进度日志（优化 G）：每批默认 5 集，写 .series_progress.log，
   中断只丢当前批、进度可见、单集失败不中断整轮。

用法：
    python monitors/apply_pending_series.py                 # 落盘已有 body
    python monitors/apply_pending_series.py --regenerate    # 落盘前删飞书旧节点
    python monitors/apply_pending_series.py --batch 5       # 每批集数
    python monitors/apply_pending_series.py --cleanup       # 落盘后清理已 verified 的本地源
"""
import os
import re
import sys
import json
import argparse
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

import articles.main as articles_main
from videos.main import _save_series_note, _generate_series_overview, _NOTE_TYPE_TAG, _sanitize_filename
from shared import series_state
from shared import series_manifest as sm
from shared import series_naming as sn
from videos.asr import safe_remove_one

PENDING_SERIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pending_series.json")
NOTES_DIR = articles_main.NOTES_DIR


def _delete_feishu_node(series_title: str, base: str):
    """--regenerate：删除飞书系列容器下与 base 同名的旧节点，避免重复。"""
    try:
        from articles.feishu import FeishuOutput
        f = FeishuOutput()
        if not f.is_available():
            return
        parent = f.ensure_series_node(series_title)
        if not parent:
            return
        for node in f.list_children(parent):
            if node.get("title") == os.path.splitext(base)[0]:
                f.delete_node(node.get("node_token"))
                print(f"   🗑️ 已删飞书旧节点：{base}")
    except Exception as e:
        print(f"   ⚠️ 删飞书旧节点失败（非致命）：{e}")


def _fix_stray_naming(series_dir: str) -> int:
    """（优化 E）纠正 *_body.md 误命名 → .body.md。返回纠正数。"""
    fixed = 0
    for stray in sn.detect_stray_underscore(series_dir):
        canonical = stray[:-len("_body.md")] + ".body.md"
        if not os.path.exists(canonical):
            try:
                os.replace(stray, canonical)
                fixed += 1
                print(f"   🔧 命名纠正：{os.path.basename(stray)} → {os.path.basename(canonical)}")
            except Exception as e:
                print(f"   ⚠️ 命名纠正失败 {stray}: {e}")
        else:
            # 两者都在：保留非空的，删掉另一个
            try:
                if os.path.getsize(stray) > os.path.getsize(canonical):
                    os.replace(stray, canonical)
                else:
                    os.remove(stray)
                fixed += 1
                print(f"   🔧 命名冲突已合并：{os.path.basename(stray)}")
            except Exception as e:
                print(f"   ⚠️ 命名冲突处理失败 {stray}: {e}")
    return fixed


def _candidate_bodies(series_dir: str, manifest: "sm.SeriesManifest") -> list:
    """从磁盘扫 .body.md，返回待落盘候选（page, body_abs, base）；跳过已 verified。"""
    cands = []
    if not os.path.isdir(series_dir):
        return cands
    for f in sorted(os.listdir(series_dir)):
        if not f.endswith(".body.md"):
            continue
        page, part = sn.parse_body_name(f)
        if page is None:
            continue
        if manifest.state(page) == sm.VERIFIED:
            continue  # 已确认落定，跳过
        body_abs = os.path.join(series_dir, f)
        # 正确去后缀：.body.md 是「点+body+点+md」双后缀，os.path.splitext 只会切掉 .md
        # 留下 .body 污染 base → 飞书节点标题带 .body（已踩坑）。必须整体切掉 .body.md。
        base = f[: -len(".body.md")]  # 第NN集_xxx
        cands.append((page, body_abs, base))
    return cands


def _verify_in_feishu(series_title: str, base: str) -> bool:
    """回读飞书确认节点存在（优化 B 删除门禁的依赖）。"""
    try:
        from articles.feishu import FeishuOutput
        f = FeishuOutput()
        if not f.is_available():
            return False
        parent = f.ensure_series_node(series_title)
        if not parent:
            return False
        return f._verify_node_present(parent, os.path.splitext(base)[0])
    except Exception:
        return False


def _log_progress(log_path: str, page: int, base: str, ok: bool, detail: str):
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} | 第{page:02d}集 | "
                     f"{'OK' if ok else 'FAIL'} | {base} | {detail}\n")
    except Exception:
        pass


def drain_series_pending(obsidian: bool = False, regenerate: bool = False,
                         batch: int = 5, cleanup: bool = False):
    """系列课降级待总结队列的落盘闭环（续跑安全版）。

    续跑信号来自 manifest（磁盘+飞书对账自愈），而非「文件是否被删」。
    落盘成功后只更新 manifest 状态，不删本地源（除非 --cleanup 且 verified）。
    """
    if not os.path.exists(PENDING_SERIES_PATH):
        print("⚠️ 无 pending_series.json，无需处理（系列课均已接管或本次无降级系列）")
        return

    series_list = json.load(open(PENDING_SERIES_PATH, encoding="utf-8"))
    if not series_list:
        print("⚠️ pending_series.json 为空，无需处理")
        return

    kept = []
    for s in series_list:
        series_title = s.get("series_title", "")
        series_dir = s.get("series_dir") or os.path.join(NOTES_DIR, _sanitize_filename(series_title))
        url = s.get("url", "")
        author = s.get("author", "")
        print(f"\n📚 系列「{series_title}」")

        # 命名自愈（优化 E）
        fixed = _fix_stray_naming(series_dir)
        if fixed:
            print(f"   🔧 纠正了 {fixed} 个命名错误")

        # 加载/对账 manifest（优化 A：磁盘+飞书自愈，取代「文件存在即续跑」）
        m = sm.load_or_init(series_title, url=url, author=author,
                            notes_dir=NOTES_DIR, reconcile=True)
        print(f"   {m.summary_line()}")

        progress_log = os.path.join(series_dir, ".series_progress.log")
        cands = _candidate_bodies(series_dir, m)
        print(f"   待落盘候选：{len(cands)} 集")

        landed = 0
        # 分批处理（优化 G）：每批 batch 集，单集异常不中断整轮
        for i in range(0, len(cands), batch):
            chunk = cands[i:i + batch]
            for page, body_abs, base in chunk:
                try:
                    body = open(body_abs, encoding="utf-8").read()
                except Exception as e:
                    print(f"  ❌ 读 body 失败 {body_abs}: {e}")
                    _log_progress(progress_log, page, base, False, f"read fail: {e}")
                    continue
                if not body.strip():
                    print(f"  ⚠️ body 为空，跳过：{base}")
                    _log_progress(progress_log, page, base, False, "empty body")
                    continue
                note_type = "structured"
                tags = [series_title, _NOTE_TYPE_TAG.get(note_type, "视频笔记")]
                if regenerate:
                    _delete_feishu_node(series_title, base)
                try:
                    _save_series_note(body, series_dir, base, author, url, tags, note_type, obsidian=obsidian)
                    m.set_state(page, sm.LANDED)
                    series_state.mark_done(series_title, base, url=url, author=author)  # monitors 增量去重兼容
                    # 飞书回读校验 → verified（优化 B 删除门禁的前置条件）
                    verified = _verify_in_feishu(series_title, base)
                    if verified:
                        m.set_state(page, sm.VERIFIED)
                    landed += 1
                    st = "verified" if verified else "landed(未回读确认)"
                    print(f"  ✅ 已落盘：{base} [{st}]")
                    _log_progress(progress_log, page, base, True, st)
                except Exception as e:
                    print(f"  ❌ 落盘失败 {base}: {e}")
                    _log_progress(progress_log, page, base, False, f"land fail: {e}")

        m.save()

        # 仍只有 raw 无 body 的集（需要 Agent 总结）→ 留在队列
        remaining_raws = []
        if os.path.isdir(series_dir):
            for f in os.listdir(series_dir):
                if f.endswith("_raw.md"):
                    body_candidate = f[:-len("_raw.md")] + ".body.md"
                    if not os.path.exists(os.path.join(series_dir, body_candidate)):
                        remaining_raws.append(os.path.relpath(
                            os.path.join(series_dir, f), NOTES_DIR))

        # 总览重生成（从飞书读回，upsert 不重复）
        try:
            _generate_series_overview(series_title, series_dir, url, obsidian=obsidian)
            print(f"  🧭 系列总览已重生成")
        except Exception as e:
            print(f"  ⚠️ 总览重生成失败（非致命）：{e}")

        # --cleanup：仅删 verified 的本地源（优化 B）
        cleaned = 0
        if cleanup:
            for page in m.verified_pages():
                ep = m.get(page)
                raw_rel = ep.get("raw")
                body_rel = ep.get("body")
                for rel in (raw_rel, body_rel):
                    if not rel:
                        continue
                    p = os.path.join(NOTES_DIR, rel)
                    if os.path.exists(p) and safe_remove_one(p):
                        cleaned += 1
            print(f"  🧹 已清理 {cleaned} 个本地源文件（verified）")

        if remaining_raws:
            s["degraded_raws"] = remaining_raws
            kept.append(s)
            print(f"  🤖 NEED_AGENT_SERIES_SUMMARY: 还有 {len(remaining_raws)} 集只有 raw、无 body，"
                  f"需执行模型按 notes/{_sanitize_filename(series_title)}/*_raw.md 总结成 .body.md 后重跑本脚本")
        else:
            print(f"  ✅ 系列「{series_title}」全部候选已落盘，从队列移除")

    json.dump(kept, open(PENDING_SERIES_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n=== 系列课 drain 汇总 ===\n剩余待 Agent 总结的系列：{len(kept)} 个；"
          f"已闭环：{len(series_list)-len(kept)} 个")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regenerate", action="store_true", help="落盘前先删除飞书同名旧节点（重生成）")
    ap.add_argument("--obsidian", action="store_true", help="同时写入 Obsidian（默认只写飞书）")
    ap.add_argument("--batch", type=int, default=5, help="每批落盘集数（默认 5，优化 G）")
    ap.add_argument("--cleanup", action="store_true", help="落盘后清理已 verified 的本地源（优化 B）")
    ap.add_argument("--check", action="store_true", help="自报飞书写入机制与可用性（不落盘）")
    args = ap.parse_args()
    if args.check:
        from articles.feishu import FeishuOutput
        fo = FeishuOutput()
        print(fo.explain_mechanism())
        print(f"  is_available : {fo.is_available()}")
        print("  （--check 仅自报，未落盘；要落盘去掉 --check 即可）")
        return
    drain_series_pending(obsidian=args.obsidian, regenerate=args.regenerate,
                         batch=args.batch, cleanup=args.cleanup)


if __name__ == "__main__":
    main()
