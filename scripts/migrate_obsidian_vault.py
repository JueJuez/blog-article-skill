#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/migrate_obsidian_vault.py — 把 Obsidian 库里的旧结构存量笔记，按 resolve_folder 重路由到飞书镜像结构。

背景（用户 2026-09-04 决策）：Obsidian 改为默认落盘后，新内容会自然走 resolve_folder 的
【监控】/【我的总结】/【待归类】 结构。但库里有一批旧方案遗留文件夹：
  01_独立开发 … 07_内容创作（数字主题夹）
  投资交易 / 独立开发出海（主题/作者两级）
  副业增长/生财有术（scys）
  千刀千法 / 哲学思辨，知行合一（系列课，旧时直接放根）
  【00_待归类】（旧收件箱）
本脚本把它们的 .md 重算路径并移动，使整个库统一成新结构。

每篇处理：
- 解析 frontmatter/正文：source_url / 正文 **作者**： / 标题 / #标签
- author 派生：正文作者 > frontmatter author > <主题>/<作者>/ 二级里的作者夹 > 无
- 系列识别：命中 subscriptions 的 series_patterns → 该系列（归属账号则走【监控】）；
            否则若夹名出现在 ≥60% 子文件标题里 → 视为独立系列
- 旧主题夹名 → 作为 #标签 注入文件（满足「主题改用标签，不建主题夹」）
- resolve_folder 算出目标相对路径 → 移动

安全：
- 默认 --dry-run 只打印计划，不改动任何文件。
- 真实运行写 scripts/migration_obsidian_log.json（{src: dst}），可据此手动回退。
- 只动 legacy 顶层夹下的 .md，绝不碰已在新结构（【监控】/【我的总结】/【待归类】）的文件。
- 目标重名自动加 -2 -3 后缀。

用法：
  python scripts/migrate_obsidian_vault.py --dry-run
  python scripts/migrate_obsidian_vault.py                 # 真实移动
  python scripts/migrate_obsidian_vault.py --topic-tag-only  # 仅注入主题标签、不移动
"""
import os
import re
import sys
import json
import argparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, ".env"))

from shared.routing import resolve_folder

VAULT = os.getenv("OBSIDIAN_VAULT_PATH", "")
# 新结构根：这些下面的文件一律不动（已是目标结构）
SKIP_ROOTS = {"【监控】", "【我的总结】", "【待归类】"}
OLD_INBOX = "【00_待归类】"


def _load_subs():
    path = os.path.join(BASE_DIR, "monitors", "subscriptions.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _known_series():
    """series 名 → 归属账号名（来自 subscriptions 的 series_patterns）。"""
    subs = _load_subs()
    out = {}
    for sec in ("wechat", "bilibili"):
        for w in (subs.get(sec) or []):
            n = (w.get("name") or "").strip()
            for p in (w.get("series_patterns") or []):
                s = (p.get("series") or "").strip()
                if n and s:
                    out[s] = n
    return out


KNOWN_SERIES = _known_series()


# ---------- 解析 ----------
def _split_fm(text):
    """返回 (meta_dict浅解析, body)。只抽取我们要的字段，避免依赖 yaml。"""
    if text.startswith("---"):
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
        if m:
            fm_text, body = m.group(1), m.group(2)
            meta = {}
            for line in fm_text.splitlines():
                mm = re.match(r"^\s*(\w+)\s*:\s*(.+?)\s*$", line)
                if mm:
                    meta[mm.group(1)] = mm.group(2).strip().strip('"').strip("'")
            return meta, body
    return {}, text


_PLACEHOLDER_AUTHORS = {"未知", "匿名", "作者未知", "佚名", "无", "none", "n/a", "-", "", " "}


def _clean_author(raw):
    """清洗作者：占位符 / 整串被括号包住的伪作者 → 视为无作者。"""
    if not raw:
        return ""
    a = raw.strip()
    if a.lower() in _PLACEHOLDER_AUTHORS:
        return ""
    # 形如 【作者未知】 / [YaoYuan] 整体被 【】 或 [] 包住 → 占位符
    if re.fullmatch(r"[【\[][^】\]]*[】\]]", a):
        return ""
    return a


def _extract_body_author(body):
    m = re.search(r"\*\*作者\*\*[：:]\s*([^\n|]+)", body)
    if m:
        return re.split(r"[（(]", m.group(1).strip())[0].strip()
    return ""


def _first_h1(body):
    for line in body.splitlines():
        if line.startswith("# ") and not line.startswith("##"):
            return line[2:].strip()
    return ""


def _extract_tags(body):
    tags = []
    for line in body.splitlines():
        s = line.strip()
        if re.match(r"^#[^#\s]+(?:\s+#[^#\s]+)*$", s):
            tags.extend(s.split())
    return tags


def _legacy_topic(top_folder):
    t = re.sub(r"^【|】$", "", top_folder).strip()
    t = re.sub(r"^\d+_", "", t)  # 去 01_ 前缀
    return t


def _is_series_folder(top_folder, top_abs):
    """夹名出现在 ≥60% 子 .md 的「文件名或标题」里 → 视为系列（系列名常在文件名）。"""
    names, hits = 0, 0
    for fn in os.listdir(top_abs):
        if fn.lower().endswith(".md"):
            names += 1
            base = os.path.splitext(fn)[0]
            t = ""
            try:
                with open(os.path.join(top_abs, fn), encoding="utf-8") as f:
                    t = _first_h1(f.read())
            except Exception:
                pass
            if top_folder in base or (t and top_folder in t):
                hits += 1
    return names > 0 and hits / names >= 0.6


# ---------- 标签注入 ----------
def _ensure_topic_tag(text, topic_tag):
    if topic_tag in text:
        return text
    tag_line_pat = re.compile(r"^#[^#\s]+(?:\s+#[^#\s]+)*$", re.M)
    m = tag_line_pat.search(text)
    if m:
        new_line = m.group(0) + " " + topic_tag
        return text[:m.start()] + new_line + text[m.end():]
    # 无标签行：追加到文件末尾（Obsidian 全文件扫描 #标签，位置无关）
    if not text.endswith("\n"):
        text += "\n"
    return text + topic_tag + "\n"


# ---------- 主流程 ----------
def _iter_legacy_md():
    """yield (abs_path, rel_parts)。rel_parts 不含 vault 根。"""
    for name in os.listdir(VAULT):
        top_abs = os.path.join(VAULT, name)
        if not os.path.isdir(top_abs):
            continue
        if name in SKIP_ROOTS:
            continue  # 已是新结构
        for root, dirs, files in os.walk(top_abs):
            dirs[:] = [d for d in dirs if d not in SKIP_ROOTS]
            for fn in files:
                if not fn.lower().endswith(".md"):
                    continue
                abs_path = os.path.join(root, fn)
                rel = os.path.relpath(abs_path, VAULT)
                rel_parts = rel.split(os.sep)
                yield abs_path, rel_parts


_FOLDER_SERIES_CACHE = {}


def _folder_series(top, top_abs):
    if top in _FOLDER_SERIES_CACHE:
        return _FOLDER_SERIES_CACHE[top]
    res = ""
    if top in KNOWN_SERIES:
        res = top
    elif _is_series_folder(top, top_abs):
        res = top
    _FOLDER_SERIES_CACHE[top] = res
    return res


def plan_one(abs_path, rel_parts):
    with open(abs_path, encoding="utf-8") as f:
        text = f.read()
    meta, body = _split_fm(text)
    url = meta.get("source_url", "")
    fm_author = _clean_author(meta.get("author", ""))
    body_author = _clean_author(_extract_body_author(body))
    title = meta.get("title", "") or _first_h1(body) or os.path.splitext(rel_parts[-1])[0]
    tags = _extract_tags(body)

    top = rel_parts[0]
    top_abs = os.path.join(VAULT, top)
    topic = _legacy_topic(top)
    author = fm_author or body_author
    if not author and len(rel_parts) >= 3:
        author = rel_parts[1]  # <主题>/<作者>/file

    # 系列按「整夹」判定：夹名命中 series_patterns 或出现在多数子文件名/标题 → 独立系列。
    # 系列夹整夹归【我的总结】/系列课/<系列>/，不按单篇作者拆散（含总览文件）。
    folder_series = _folder_series(top, top_abs)
    series = folder_series

    item = {"url": url or "", "author": author or "", "title": title, "tags": tags or []}
    if "生财有术" in rel_parts or topic == "生财有术":
        item["scys_domain"] = topic if topic != "生财有术" else "副业增长"
    if series:
        item["series"] = series
        item["author"] = ""  # 独立系列不按作者拆

    target = resolve_folder(item)
    topic_tag = "#" + topic
    return target, topic_tag, item, text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不改动（默认行为等价，但显式更清晰）")
    ap.add_argument("--topic-tag-only", action="store_true", help="仅注入主题标签、不移动文件")
    args = ap.parse_args()

    if not VAULT or not os.path.isdir(VAULT):
        print(f"✗ OBSIDIAN_VAULT_PATH 未配置或不存在：{VAULT!r}")
        return

    plan = []  # (src, dst, topic_tag, text)
    for abs_path, rel_parts in _iter_legacy_md():
        target, topic_tag, item, text = plan_one(abs_path, rel_parts)
        dst = os.path.join(VAULT, *target.split("/"), os.path.basename(abs_path))
        plan.append((abs_path, dst, topic_tag, text, target, item))

    # 去重后缀
    def _uniq(dst):
        if not os.path.exists(dst):
            return dst
        base, ext = os.path.splitext(dst)
        i = 2
        while os.path.exists(f"{base}-{i}{ext}"):
            i += 1
        return f"{base}-{i}{ext}"

    from collections import Counter
    root_counter = Counter()
    inbox_count = 0
    for abs_path, dst, topic_tag, text, target, item in plan:
        dst = _uniq(dst)
        root = target.split("/")[0]
        root_counter[root] += 1
        if root == "【待归类】":
            inbox_count += 1
        print(f"  {os.path.relpath(abs_path, VAULT)}")
        print(f"    → {os.path.relpath(dst, VAULT)}   (author={item.get('author') or '-'}, series={item.get('series') or '-'}, tag={topic_tag})")

    print(f"\n共 {len(plan)} 篇待处理。目标分布：")
    for k, v in root_counter.items():
        print(f"  {k}: {v}")
    print(f"  其中落入【待归类】收件箱：{inbox_count} 篇（多为无作者的旧主题夹内容，已注入主题标签，可后按标签浏览/手动归类）")

    if args.dry_run or not plan:
        print("\n[dry-run] 未改动任何文件。去掉 --dry-run 执行真实移动。")
        return

    # 真实执行
    log = {}
    for abs_path, dst, topic_tag, text, target, item in plan:
        dst = _uniq(dst)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not args.topic_tag_only:
            # 先注入标签再移动（在原文件上改）
            new_text = _ensure_topic_tag(text, topic_tag)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(new_text)
            os.replace(abs_path, dst)
            log[abs_path] = dst
        else:
            new_text = _ensure_topic_tag(text, topic_tag)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(new_text)
            log[abs_path] = "(tag-only)"

    log_path = os.path.join(BASE_DIR, "scripts", "migration_obsidian_log.json")
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 完成。移动/改写 {len(log)} 篇，日志：{log_path}")


if __name__ == "__main__":
    main()
