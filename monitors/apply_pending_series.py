"""monitors/apply_pending_series.py — 系列课降级待总结队列 drainer。

背景：FORCE_AGENT_MODE=1（无外部 AI）下，B站系列课降级只产出
notes/<系列名>/*_raw.md（字幕），真正总结必须由执行模型（Agent）完成。
monitors/run.py 在发现系列且降级时会把系列登记到 pending_series.json。

本脚本负责收尾闭环：
1. 读 pending_series.json（monitors/run.py 登记的系列课）。
2. 对每个系列：把已存在的 <x>.body.md（Agent 已产出的笔记正文）串行调用
   videos.main._save_series_note 落飞书（避免并发重复节点），清理 raw/body，
   重生成系列总览；剩余「有 raw 但无 body」的集打印 NEED_AGENT_SERIES_SUMMARY 供 Agent 接单。
3. raw 全部处理完则移除该系列条目；最后把更新后的 pending_series.json 写回。

用法：
    python monitors/apply_pending_series.py               # 落盘已有 body + 打印剩余待总结
    python monitors/apply_pending_series.py --regenerate  # 落盘前先删飞书旧节点（重生成）
"""
import os
import re
import sys
import json
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

import articles.main as articles_main
from videos.main import _save_series_note, _generate_series_overview, _NOTE_TYPE_TAG, _sanitize_filename
from shared import series_state

PENDING_SERIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pending_series.json")
NOTES_DIR = articles_main.NOTES_DIR


def _derive_base(raw_abs: str):
    """从 raw 文件名推导 (page, part, base_name)。例：第01集_xxx_raw.md → (1, 'xxx', '第01集_xxx')。"""
    name = os.path.basename(raw_abs)
    m = re.match(r"^第(\d+)集_(.*?)(_raw)?\.md$", name)
    if not m:
        return None, None, None
    page = int(m.group(1))
    part = m.group(2)
    base = f"第{page:02d}集_{_sanitize_filename(part)}"
    return page, part, base


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


def drain_series_pending(obsidian: bool = False, regenerate: bool = False):
    """系列课降级待总结队列的落盘闭环（可被 monitors/run.py 自动调用）。

    仅处理「已有 .body.md（Agent 已总结）」的集：串行调 _save_series_note 落飞书，
    成功后写 series_state.mark_done（增量去重的关键），清理 raw/body，重生成总览。
    仍只有 raw 无 body 的集打印 NEED_AGENT_SERIES_SUMMARY，交执行模型接单。

    注意：本函数只负责「落盘」，不负责「总结」。总结（raws→bodies）由执行模型在
    收尾例程里派子 Agent 完成，之后再调本函数落地。两者合并即系列课全自动闭环。
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
        raws = s.get("degraded_raws", [])
        print(f"\n📚 系列「{series_title}」：{len(raws)} 集待处理")

        remaining = []
        saved = 0
        for raw_rel in raws:
            raw_abs = raw_rel if os.path.isabs(raw_rel) else os.path.join(NOTES_DIR, raw_rel)
            if not os.path.exists(raw_abs):
                continue  # 已处理/已清理
            body_abs = raw_abs[:-len("_raw.md")] + ".body.md" if raw_abs.endswith("_raw.md") else raw_abs + ".body"
            if not os.path.exists(body_abs):
                remaining.append(raw_rel)  # 等 Agent 总结
                continue
            page, part, base = _derive_base(raw_abs)
            if not base:
                remaining.append(raw_rel)
                continue
            try:
                body = open(body_abs, encoding="utf-8").read()
            except Exception as e:
                print(f"  ❌ 读 body 失败 {body_abs}: {e}")
                remaining.append(raw_rel)
                continue
            note_type = "structured"
            tags = [series_title, _NOTE_TYPE_TAG.get(note_type, "视频笔记")]
            if regenerate:
                _delete_feishu_node(series_title, base)
            try:
                _save_series_note(body, series_dir, base, author, url, tags, note_type, obsidian=obsidian)
                series_state.mark_done(series_title, base, url=url, author=author)  # 增量去重：标记已落盘
                saved += 1
                for p in (raw_abs, body_abs):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
                print(f"  ✅ 已落盘：{base}")
            except Exception as e:
                print(f"  ❌ 落盘失败 {base}: {e}")
                remaining.append(raw_rel)

        # 重生成系列总览（从飞书容器读回，避免依赖本地未落盘旧稿；upsert 不会重复）
        try:
            _generate_series_overview(series_title, series_dir, url, obsidian=obsidian)
            print(f"  🧭 系列总览已重生成")
        except Exception as e:
            print(f"  ⚠️ 总览重生成失败（非致命）：{e}")

        if remaining:
            s["degraded_raws"] = remaining
            kept.append(s)
            print(f"  🤖 NEED_AGENT_SERIES_SUMMARY: 还有 {len(remaining)} 集只有 raw、无 body，"
                  f"需执行模型按 notes/{_sanitize_filename(series_title)}/*_raw.md 总结成 .body.md 后重跑本脚本")
        else:
            print(f"  ✅ 系列「{series_title}」全部 {saved} 集已落盘，从队列移除")

    json.dump(kept, open(PENDING_SERIES_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n=== 系列课 drain 汇总 ===\n剩余待 Agent 总结的系列：{len(kept)} 个；已闭环：{len(series_list)-len(kept)} 个")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regenerate", action="store_true", help="落盘前先删除飞书同名旧节点（重生成）")
    ap.add_argument("--obsidian", action="store_true", help="同时写入 Obsidian（默认只写飞书）")
    args = ap.parse_args()
    drain_series_pending(obsidian=args.obsidian, regenerate=args.regenerate)


if __name__ == "__main__":
    main()
