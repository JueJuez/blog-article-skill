"""scripts/rename_list.py — 生成「飞书改名清单」。

问题（用户 2026-08-25）：飞书大量节点标题错乱——可能是寒暄首句（"大家好，我是XX"）、
未命名占位、或干脆不是原文标题。节点名 / 正文 H1 都不可信（H1 是模型自创）。
唯一可靠来源是原文链接 source_url 的 og:title。

本脚本：
  1. BFS 收集【监控】树下所有文章叶子节点（has_child=false，跳过总览文档）。
  2. 对每个节点：取当前标题 → 抓正文取 source_url → 抓 og:title。
  3. 判定是否需改名：
       a. 启发式坏标题（寒暄/未命名/段标题）；或
       b. 当前标题（剥日期后缀归一化）≠ og:title（标题不符原文）。
  4. 输出 notes/_audit/rename_list.md：按 平台/账号 分组，列出
     当前标题 | 建议标题(og:title) | 飞书节点 | 原文 | 原因。

用法：
  python scripts/rename_list.py --platform 公众号 --limit 5        # 试跑
  python scripts/rename_list.py --platform 公众号                 # 全量公众号
  python scripts/rename_list.py                                   # 全平台
  python scripts/rename_list.py --dry-run                         # 只统计不写文件

只读飞书 + 只读抓原文，不修改任何飞书数据。改名由用户在飞书里手动粘贴。
"""
import os
import re
import sys
import json
import time
import random
import argparse
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

from shared import feishu_overview as fo
from shared.fetch_title import (
    fetch_og_title, titles_differ, _is_scys, _is_wechat, _is_bilibili, _extract_bvid,
)
from shared.cdp_session import SharedCdpSession
from monitors.bilibili import _fetch_vlist   # 复用监控脚本的 WBI 签名 + 登录态列表拉取（低频安全）
import json as _json

SPACE_ID = os.getenv("FEISHU_WIKI_SPACE", "7636965310725115074")
TENANT_DOMAIN = "r1t40urlzrp.feishu.cn"
MONITOR_ROOT = "VnVTwf8vJi7VFgkyKG4cwCTpnfb"

# 启发式坏标题（与 feishu_overview.is_bad_title 同义，本地副本避免重复抓取判断）
_BAD_GREET_RE = re.compile(
    r"^(大家好|你好|亲爱的|各位朋友|我是|我叫)|大家好[，,、]?\s*我是",
    re.IGNORECASE,
)
_BAD_GENERIC = {
    "总结", "总结：", "要点提炼", "要点", "摘要", "概览", "复盘", "正文",
    "目录", "概述", "导读", "前言", "简介", "全文",
}

# 微信未登录/未渲染时拿到的默认页标题，不是文章真标题 → 视为「取不到，标人工核」
_WECHAT_GENERIC_RE = re.compile(r"^(微信公众平台|微信公众平台首页|公众号|网页链接)\b")


def _is_generic_wechat(title):
    return bool(_WECHAT_GENERIC_RE.match((title or "").strip()))


def _find_lark_cli():
    import shutil
    cand = shutil.which("lark-cli")
    if cand:
        return cand
    base = r"C:\Users\O1830\.workbuddy\binaries\node\cli-connector-packages"
    p = os.path.join(base, "lark-cli")
    return p if os.path.exists(p) else "lark-cli"


LARK_CLI = _find_lark_cli()


def run_cli(args, timeout=60):
    cmd = [LARK_CLI] + args + ["--as", "user", "--format", "json"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if not r.stdout.strip():
        return {}
    try:
        return json.loads(r.stdout)
    except Exception:
        return {}


def list_children(parent_token):
    d = run_cli([
        "wiki", "+node-list", "--space-id", SPACE_ID,
        "--parent-node-token", parent_token, "--page-all", "--page-size", "50",
    ])
    return (d.get("data", {}) or {}).get("nodes", []) or []


def is_overview(title):
    return title.startswith("📋 总览") or title.startswith("00_")


def collect_articles(root_token):
    """BFS 仅下钻 has_child 容器，收集文章叶子（has_child=false 且非总览）。"""
    out = []
    stack = [(root_token, [])]
    seen = set()
    while stack:
        tok, path = stack.pop()
        if tok in seen:
            continue
        seen.add(tok)
        for node in list_children(tok):
            title = node.get("title", "")
            nt = node.get("node_token", "")
            child_path = path + [title]
            if is_overview(title):
                continue
            if node.get("has_child"):
                stack.append((nt, child_path))
            else:
                out.append((child_path, node))
    return out


def fetch_body(obj_token):
    if not obj_token:
        return ""
    d = run_cli([
        "docs", "+fetch", "--doc", obj_token,
        "--doc-format", "markdown", "--scope", "full",
    ], timeout=30)
    return (((d or {}).get("data", {}) or {}).get("document", {}) or {}).get("content", "") or ""


def is_heuristic_bad(title):
    t = (title or "").strip()
    if t.startswith("未命名"):
        return True
    if t in _BAD_GENERIC:
        return True
    if _BAD_GREET_RE.search(t):
        return True
    return False


def _build_bilibili_map():
    """从 subscriptions.json 读 B站 UP 的 uid，按 UP 逐个拉「全量视频列表」(bvid+title)，
    构建 {bvid: title} 映射。

    低频关键：每个 UP 仅 1~3 次 WBI 签名请求（monitors.bilibili._fetch_vlist 自带
    页间 2s 退避 + 登录态 Cookie + 重试），6 个 UP 总计约 12~18 次请求，
    远比「逐篇打 view 接口(111 次)」安全，杜绝账号/IP 被风控。
    这正是用户 2026-08-26 要求的「用 API 返回的列表跟现有 B站 总结做对比」。
    """
    sub_path = os.path.join(ROOT, "monitors", "subscriptions.json")
    try:
        subs = _json.loads(open(sub_path, encoding="utf-8").read())
    except Exception:
        subs = {}
    mids = [str(b.get("uid")) for b in (subs.get("bilibili") or []) if b.get("uid")]
    merged = {}
    for mid in mids:
        try:
            vl = _fetch_vlist(mid, ps=50, paginate=True)
        except Exception as e:
            print(f"   ⚠️ B站 UP {mid} 列表拉取失败: {e}", flush=True)
            vl = []
        for it in vl:
            bv = it.get("bvid")
            t = (it.get("title") or "").strip()
            if bv and t:
                merged[bv] = t
        print(f"   · UP {mid}: 列表 {len(vl)} 个视频", flush=True)
        time.sleep(3)  # 跨 UP 退避，进一步压低频控风险
    print(f"🌐 B站列表映射构建完成: {len(merged)} 个视频（来自 {len(mids)} 个 UP）", flush=True)
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="", help="只处理路径含该片段的平台（如 公众号 / B站 / 生财有术）")
    ap.add_argument("--account", default="", help="只处理路径含该账号的节点")
    ap.add_argument("--limit", type=int, default=0, help="最多检查 N 个节点（测试用）")
    ap.add_argument("--dry-run", action="store_true", help="只统计需改数，不写文件")
    ap.add_argument("--out", default=os.path.join(ROOT, "notes", "_audit", "rename_list.md"))
    ap.add_argument("--delay", type=float, default=0.3, help="每次 og:title 抓取间隔（秒），防频控")
    args = ap.parse_args()

    print(f"📂 监控根: {MONITOR_ROOT}")
    articles = collect_articles(MONITOR_ROOT)
    print(f"📄 文章节点共 {len(articles)} 个")
    if args.platform:
        articles = [a for a in articles if args.platform in a[0]]
        print(f"   过滤平台={args.platform!r} → {len(articles)} 个")
    if args.account:
        articles = [a for a in articles if args.account in a[0]]
        print(f"   过滤账号={args.account!r} → {len(articles)} 个")
    if args.limit:
        articles = articles[:args.limit]

    # ── Phase 1: 收集所有节点，抓正文取 source_url（一次性 lark 调用，快）──
    entries = []
    for path, node in articles:
        title = node.get("title", "")
        nt = node.get("node_token", "")
        obj = node.get("obj_token", "")
        feishu_url = f"https://{TENANT_DOMAIN}/wiki/{nt}"
        platform = path[1] if len(path) > 1 else "?"
        account = path[2] if len(path) > 2 else "?"
        reason = ""
        if is_heuristic_bad(title):
            reason = "坏标题(寒暄/未命名/段标题)"
        body = fetch_body(obj)
        src = fo.extract_source_url(body)
        # 生财：用户确认当前标题正确，跳过（CDP 登录墙也取不到可靠标题）
        if src and _is_scys(src):
            continue
        entries.append({
            "platform": platform, "account": account, "title": title,
            "feishu_url": feishu_url, "src": src, "reason": reason,
        })

    # ── Phase 2: 所有公众号 URL 共用一个共享 CDP 登录态会话循环抓取 ──
    # （shared.cdp_session.SharedCdpSession：活 Chrome 优先，失败才回退克隆；
    #  公众号 与 scys 共用同一套「只开关一次浏览器」逻辑）
    wechat_srcs = [e["src"] for e in entries if e["src"] and _is_wechat(e["src"])]
    wechat_titles = {}
    if wechat_srcs:
        print(f"🌐 公众号 CDP 会话：循环抓 {len(wechat_srcs)} 个原文标题（单会话复用登录态）", flush=True)
        try:
            with SharedCdpSession() as sess:
                for i, u in enumerate(wechat_srcs, 1):
                    try:
                        wechat_titles[u] = sess.get_title(u)
                    except Exception as e:
                        print(f"    [{i}/{len(wechat_srcs)}] CDP 失败 {u[:50]}: {e}", flush=True)
                        wechat_titles[u] = ""
                    if i % 10 == 0:
                        print(f"    ... {i}/{len(wechat_srcs)}", flush=True)
        except Exception as e:
            print(f"⚠️ CDP 会话整体失败（不影响落盘，公众号将退化为裸 og:title）: {e}", flush=True)

    # ── Phase 2.5: 构建 B站 {bvid: title} 列表映射（按 UP 拉全量视频列表，低频 WBI 签名）──
    bili_map = _build_bilibili_map()

    # ── Phase 3: 组装改名清单 ──
    rows = []
    for e in entries:
        src = e["src"]
        reason = e["reason"]
        suggested = ""
        if src:
            if _is_wechat(src):
                real = wechat_titles.get(src) or fetch_og_title(src)
            elif _is_bilibili(src):
                bvid = _extract_bvid(src)
                real = bili_map.get(bvid, "")
                if not real:
                    print(f"   ⚠️ B站 bvid={bvid} 不在列表映射(可能已删/私密)，标人工核: {src}", flush=True)
            else:
                real = fetch_og_title(src)   # 其余平台→裸 og:title
            # 通用占位拦截（对所有来源生效）：微信默认页标题/"网页链接"等都不是真标题
            # → 视为取不到，退化为「人工核」，绝不把占位值当建议标题写入清单。
            if _is_generic_wechat(real):
                real = ""
            if real:
                if not reason and titles_differ(e["title"], real):
                    reason = "标题不符原文"
                if reason:
                    suggested = real
            elif reason:
                suggested = ""  # 取不到，退化为展示原文链接
        time.sleep(args.delay)   # 关键：B站 API / og 抓取延时，防爆频控（上次 B站=0 根因）
        if reason:
            rows.append((e["platform"], e["account"], e["title"], suggested,
                         e["feishu_url"], src, reason))

    print(f"\n🔎 需改标题节点: {len(rows)} 个（共检查 {len(articles)}）")
    if args.dry_run:
        print("(dry-run 未写文件)")
        for r in rows[:50]:
            print(f"   · [{r[2]}] → 建议: {r[3] or '(og取不到,见原文)'}  ({r[6]})")
        return

    # 写 markdown
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    groups = {}
    for r in rows:
        groups.setdefault((r[0], r[1]), []).append(r)
    lines = ["# 飞书改名清单（按 原文真实标题反查：公众号走 CDP 登录态 / B站走 UP 视频列表 WBI 签名批量取标题）\n"]
    lines.append("> 自动生成 · 只读飞书+原文，不修改任何数据。\n")
    lines.append("> 用法：打开「飞书节点」链接 → 改名 → 把「建议标题」粘贴进去（取不到的，点「原文」人工核对）。\n")
    lines.append("> 生财(scys) 已确认标题正确，不在本清单内。\n")
    lines.append(f"> 共 {len(rows)} 个节点需改名。\n")
    for (plat, acct) in sorted(groups.keys()):
        lines.append(f"\n## {plat} / {acct}  （{len(groups[(plat, acct)])} 个）\n")
        lines.append("| 飞书当前标题 | 建议标题(原文og:title) | 飞书节点 | 原文 | 原因 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for r in groups[(plat, acct)]:
            cur = r[2].replace("|", "／")
            sug = (r[3] or "_(og取不到,点原文人工核)_").replace("|", "／")
            flink = f"[打开]({r[4]})"
            slink = f"[原文]({r[5]})" if r[5] else "—"
            lines.append(f"| {cur} | {sug} | {flink} | {slink} | {r[6]} |")
        lines.append("")
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"\n✅ 改名清单已写入: {args.out}")


if __name__ == "__main__":
    main()
