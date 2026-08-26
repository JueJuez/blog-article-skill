"""scripts/audit_fidelity.py — 总结质量抽样审计（机械结构扫描 + fidelity 对照）。

用法：
  python scripts/audit_fidelity.py --stage enum          # 枚举各平台/账号节点数
  python scripts/audit_fidelity.py --stage mechanical    # 全量机械结构扫描（双链接/双作者/坏标题/域名错配）
  python scripts/audit_fidelity.py --stage fidelity      # 取真值做 5 维度 fidelity 对照（按 --platform 抽样）
  python scripts/audit_fidelity.py --stage all           # enum + mechanical（fast，先跑这个）

飞书只读，不修改任何数据。产物：notes/_audit/fidelity_audit.md
"""
import os
import re
import sys
import json
import time
import random
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv
load_dotenv()

from scripts.rename_list import collect_articles, fetch_body, MONITOR_ROOT, TENANT_DOMAIN
from shared.feishu_overview import extract_source_url, is_bad_title
from shared.fetch_title import _is_wechat, _is_bilibili, _is_scys, _extract_bvid

AUDIT_DIR = os.path.join(ROOT, "notes", "_audit")
os.makedirs(AUDIT_DIR, exist_ok=True)

# 图文根（【我的总结】）：手贴/文章类笔记（来源多为普通 web URL）
MYNOTES_ROOT = os.getenv("FEISHU_WIKI_MYNOTES", "IDtYwwtRoie65Ik2tHscRVN3nNh")

# ── 平台判定（path[0]=平台层，path[1]=账号层，path[2]=日更/系列）──
def platform_of(path):
    p = path[0] if len(path) > 0 else "?"
    if p == "生财有术":
        return "生财"
    return p

def account_of(path):
    return path[1] if len(path) > 1 else "?"

# 来源链接行解析：捕获 [text](url)；作者行：捕获 **作者**：VALUE（到 | 或行尾）
_SRC_RE = re.compile(r"\*\*来源链接\*\*\s*[:：]\s*\[([^\]]*)\]\(\s*([^)]*?)\s*\)")
_AUTHOR_RE = re.compile(r"\*\*作者\*\*\s*[:：]\s*([^|\n]+?)(?:\s*\|\s*\*\*来源链接\*\*|$)")
# 正文里所有 markdown 链接
_LINK_RE = re.compile(r"\[([^\]]*)\]\(\s*(https?://\S+?)\s*\)")
_GENERIC_LINK_TEXTS = {"原文链接", "原文", "链接", "link", "source", "here", "查看原文"}


def mechanical_checks(title, body):
    """返回 (issues: list[str], facts: dict)。区分严重度：
    - 双来源链接且 URL 不同 → 严重（错链残留，根因1最危险态）
    - 双来源链接但 URL 相同 → 轻（无害重复）
    - 双作者行且作者不同 → 严重（作者冲突）
    - 双作者行但相同 → 轻（重复）
    """
    issues = []
    facts = {}
    src_matches = _SRC_RE.findall(body)          # [(text, url), ...]
    author_matches = [a.strip() for a in _AUTHOR_RE.findall(body) if a.strip()]
    src_urls = [u for (_, u) in src_matches]
    facts["src_urls"] = src_urls
    facts["authors"] = author_matches

    # ── 双来源链接 ──
    if len(src_matches) > 1:
        distinct = set(src_urls)
        if len(distinct) > 1:
            issues.append(f"双来源链接·URL不同(严重): {sorted(distinct)[:2]}")
        else:
            issues.append("双来源链接·同URL重复(轻)")
    # ── 双作者行 ──
    if len(author_matches) > 1:
        if len(set(author_matches)) > 1:
            issues.append(f"双作者行·作者不同(严重): {author_matches[:2]}")
        else:
            issues.append("双作者行·重复(轻)")

    # 主来源识别
    primary = extract_source_url(body)
    facts["primary_src"] = primary
    links = _LINK_RE.findall(body)
    facts["link_count"] = len(links)
    if not primary and links:
        issues.append("来源链接无法识别(extract_source_url 空但正文有链接)")
    if not body.strip():
        issues.append("正文为空")
    if is_bad_title(title):
        issues.append(f"坏标题({title!r})")
    return issues, facts


def stage_enum():
    arts = collect_articles(MONITOR_ROOT)
    groups = {}
    for path, node in arts:
        plat = platform_of(path)
        acct = account_of(path)
        groups.setdefault(plat, {}).setdefault(acct, 0)
        groups[plat][acct] += 1
    print(f"总文章节点: {len(arts)}")
    for plat in sorted(groups):
        total = sum(groups[plat].values())
        print(f"\n## {plat}  ({total})")
        for acct in sorted(groups[plat], key=lambda a: -groups[plat][a]):
            print(f"   {acct}: {groups[plat][acct]}")
    return groups


def stage_mechanical(limit_per_platform=0):
    arts = collect_articles(MONITOR_ROOT)
    # 按平台分组
    by_plat = {}
    for path, node in arts:
        by_plat.setdefault(platform_of(path), []).append((path, node))

    all_issues = []
    stats = {"total": len(arts), "by_issue": {}, "by_platform": {}}
    for plat, items in by_plat.items():
        if limit_per_platform:
            items = items[:limit_per_platform]
        pstat = {"n": len(items), "issues": 0, "issue_types": {}}
        for path, node in items:
            title = node.get("title", "")
            obj = node.get("obj_token", "")
            body = fetch_body(obj)
            issues, facts = mechanical_checks(title, body)
            # 域名错配检测
            src = facts.get("primary_src", "")
            if src:
                if plat == "公众号" and not _is_wechat(src):
                    issues.append(f"域名错配(公众号节点但来源={src[:40]})")
                elif plat == "B站" and not _is_bilibili(src):
                    issues.append(f"域名错配(B站节点但来源={src[:40]})")
                elif plat == "生财" and not _is_scys(src):
                    issues.append(f"域名错配(生财节点但来源={src[:40]})")
            if issues:
                pstat["issues"] += 1
                for it in issues:
                    key = it.split("(")[0]
                    pstat["issue_types"][key] = pstat["issue_types"].get(key, 0) + 1
                    stats["by_issue"][key] = stats["by_issue"].get(key, 0) + 1
                all_issues.append({
                    "platform": plat, "account": account_of(path),
                    "title": title,
                    "feishu_url": f"https://{TENANT_DOMAIN}/wiki/{node.get('node_token','')}",
                    "src": src, "src_urls": facts.get("src_urls", []),
                    "authors": facts.get("authors", []),
                    "issues": issues,
                })
        stats["by_platform"][plat] = pstat

    # 写出 JSON
    out_json = os.path.join(AUDIT_DIR, "mechanical_scan.json")
    json.dump({"stats": stats, "issues": all_issues},
              open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    # 打印摘要
    crit = sum(1 for it in all_issues if any("严重" in x for x in it["issues"]))
    print(f"\n=== 机械结构扫描 ===")
    print(f"扫描节点: {stats['total']}")
    print(f"有问题节点: {len(all_issues)}  (其中严重: {crit})")
    print("\n按问题类型:")
    for k, v in sorted(stats["by_issue"].items(), key=lambda x: -x[1]):
        print(f"   {k}: {v}")
    print("\n按平台:")
    for p, s in stats["by_platform"].items():
        print(f"   {p}: {s['issues']}/{s['n']} 有问题  {s['issue_types']}")
    print(f"\n明细: {out_json}")
    return all_issues, stats


def _gt_bilibili(url):
    try:
        from videos.fetch import fetch_transcript
        r = fetch_transcript(url)
        if not r:
            return None, "fetch_transcript_none"
        title, segments, author = r
        text = "\n".join((s.get("text") or "") for s in segments if isinstance(s, dict))
        return text, "transcript"
    except Exception as e:
        return None, f"err:{e}"


def _gt_web(url):
    try:
        from articles.fetch import fetch_web_content
        txt = fetch_web_content(url)
        if isinstance(txt, tuple):
            txt = txt[1] if len(txt) > 1 else ""
        return (txt or ""), "web"
    except Exception as e:
        return None, f"err:{e}"


def _gt_cdp(url):
    """公众号/生财：CDP 取渲染后正文（需登录态）。会 kill Chrome 回退，调用方须已确认。"""
    try:
        from shared.cdp_session import SharedCdpSession
        with SharedCdpSession() as s:
            html = s.get_html(url, wait=9000)
        # 粗略去标签
        import re as _re
        text = _re.sub(r"<script[\s\S]*?</script>", "", html or "")
        text = _re.sub(r"<style[\s\S]*?</style>", "", text)
        text = _re.sub(r"<[^>]+>", "\n", text)
        text = _re.sub(r"\n\s*\n+", "\n", text).strip()
        return text, "cdp"
    except Exception as e:
        return None, f"err:{e}"


def _classify_src(src):
    """按来源 URL 判真值取法：bili→transcript(纯网络) / web→fetch_web_content(纯GET) /
    wechat|scys→CDP(需kill Chrome) / none→无法取。"""
    if not src:
        return "none"
    if _is_bilibili(src):
        return "bili"
    if _is_wechat(src):
        return "wechat"
    if _is_scys(src):
        return "scys"
    return "web"


def stage_fidelity(platform_filter="", limit_per_class=5, allow_cdp=False):
    """取真值做 fidelity 对照。扫描【监控】+【我的总结】两棵树，按来源类型聚类：
      - bili  → fetch_transcript（纯网络，不碰 Chrome）
      - web   → fetch_web_content（纯 GET，不碰 Chrome）
      - wechat/scys → 默认跳过（需 CDP kill Chrome，除非 --allow-cdp）
    产出 notes/_audit/fidelity_pairs.json：每篇 {class,platform,account,title,feishu_url,
    src, summary, ground_truth, gt_type, summary_len, gt_len}。供逐篇判 5 维度。
    """
    roots = {"监控": MONITOR_ROOT, "我的总结": MYNOTES_ROOT}
    by_class = {}
    for rname, rtok in roots.items():
        arts = collect_articles(rtok)
        for path, node in arts:
            body = fetch_body(node.get("obj_token", ""))
            src = extract_source_url(body)
            if not body or not src:
                continue
            cls = _classify_src(src)
            if platform_filter and platform_filter != cls:
                continue
            by_class.setdefault(cls, []).append((rname, path, node, body, src))

    pairs = []
    for cls, items in by_class.items():
        # 长文优先（数据密集更易失真）
        items.sort(key=lambda x: -len(x[3]))
        cands = items[:limit_per_class]
        for rname, path, node, body, src in cands:
            gt, gt_type = None, "none"
            if cls == "bili":
                gt, gt_type = _gt_bilibili(src)
            elif cls == "web":
                gt, gt_type = _gt_web(src)
            elif cls in ("wechat", "scys"):
                gt, gt_type = (_gt_cdp(src) if allow_cdp else (None, "skipped_cdp"))
            plat = platform_of(path) if rname == "监控" else cls
            pairs.append({
                "class": cls, "root": rname, "platform": plat,
                "account": account_of(path),
                "title": node.get("title", ""),
                "feishu_url": f"https://{TENANT_DOMAIN}/wiki/{node.get('node_token','')}",
                "src": src,
                "summary": body, "ground_truth": gt or "", "gt_type": gt_type,
                "summary_len": len(body), "gt_len": len(gt or ""),
            })
            print(f"  [{cls}/{plat}/{account_of(path)}] {node.get('title','')[:36]} | gt={gt_type} gtlen={len(gt or '')}", flush=True)

    out = os.path.join(AUDIT_DIR, "fidelity_pairs.json")
    json.dump(pairs, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n✅ fidelity pairs: {len(pairs)} 篇 → {out}")
    print(f"   取真值类型: bili={sum(1 for p in pairs if p['class']=='bili')} "
          f"web={sum(1 for p in pairs if p['class']=='web')} "
          f"wechat/scys={sum(1 for p in pairs if p['class'] in ('wechat','scys'))}")
    print(f"   (公众号/生财={ 'CDP' if allow_cdp else '已跳过(需授权CDP)' })")
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="enum", choices=["enum", "mechanical", "fidelity", "all"])
    ap.add_argument("--platform", default="", help="fidelity 阶段限定平台(或账号)")
    ap.add_argument("--limit", type=int, default=0, help="机械扫描:0=全扫; fidelity:0→每平台5篇")
    ap.add_argument("--allow-cdp", action="store_true", help="允许 CDP(kill Chrome)取公众号/生财真值")
    args = ap.parse_args()
    if args.stage in ("enum", "all"):
        stage_enum()
    if args.stage in ("mechanical", "all"):
        stage_mechanical(limit_per_platform=args.limit)
    if args.stage == "fidelity":
        stage_fidelity(platform_filter=args.platform,
                       limit_per_class=(args.limit or 5),
                       allow_cdp=args.allow_cdp)


if __name__ == "__main__":
    main()
