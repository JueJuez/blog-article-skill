# -*- coding: utf-8 -*-
"""内容层 fidelity 配对：飞书总结(视频/生财) ↔ 本地真值(视频raw字幕 / scys缓存原文)。
不碰 Chrome、不调网络。公众号无本地缓存 → 标记 skipped_cdp。
产出 notes/_audit/content_pairs.json。
"""
import os, re, json, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.rename_list import collect_articles, fetch_body, MONITOR_ROOT
from shared.feishu_overview import extract_source_url

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT = os.path.join(ROOT, "notes", "_audit")
os.makedirs(AUDIT, exist_ok=True)

def norm_ep(s):
    m = re.search(r"第\s*([\d]+)\s*集", s)
    return m.group(1) if m else ""

def topic_of(title):
    # 去掉 第XX集_ 前缀，取主题
    t = re.sub(r"^.*?第\s*\d+\s*集\s*[_：:—-]?\s*", "", title)
    t = re.sub(r"[【】\[\]_\-]", " ", t)
    return t.strip()

def bigrams(s):
    s = re.sub(r"\s+", "", s)
    return set(s[i:i+2] for i in range(len(s)-1))

# ── 视频：飞书 B站节点 ↔ notes/<series>/*_raw.md ──
print("收集飞书 B站节点...", flush=True)
arts = collect_articles(MONITOR_ROOT)
bili_nodes = [(p, n) for p, n in arts if p[0] == "B站"]

# 建立 raw 索引：episode号 -> [(series, rawpath, transcript_text, rawtitle)]
raw_idx = {}
raw_dirs = []
for d in os.listdir(os.path.join(ROOT, "notes")):
    dd = os.path.join(ROOT, "notes", d)
    if os.path.isdir(dd):
        for fn in os.listdir(dd):
            if fn.endswith("_raw.md"):
                raw_dirs.append(os.path.join(dd, fn))
print(f"  找到 raw 文件 {len(raw_dirs)} 个", flush=True)

def raw_transcript(path):
    txt = open(path, encoding="utf-8", errors="ignore").read()
    # 去掉 frontmatter(> 开头的行) 与 "原始字幕" 提示行
    lines = [l for l in txt.splitlines() if not l.startswith(">")]
    body = "\n".join(lines)
    body = re.sub(r"原始字幕（AI 不可用.*?）", "", body, flags=re.S)
    return body.strip()

video_pairs = []
for path, node in bili_nodes:
    title = node.get("title", "")
    ep = norm_ep(title)
    topic = topic_of(title)
    # 在 raw 里找 episode 号相同且主题最像的
    best, bestscore = None, 0
    for rp in raw_dirs:
        rn = os.path.basename(rp)
        rep = norm_ep(rn)
        if rep and ep and rep != ep:
            continue
        rtopic = topic_of(rn)
        gb = bigrams(topic) & bigrams(rtopic)
        score = len(gb)
        if score > bestscore:
            bestscore, best = score, rp
    if best and bestscore >= 10:
        summary = fetch_body(node.get("obj_token", ""))
        video_pairs.append({
            "platform": "B站", "account": path[1],
            "title": title,
            "feishu_url": f"https://{os.getenv('FEISHU_TENANT','')}/wiki/{node.get('node_token','')}",
            "src": extract_source_url(summary),
            "summary": summary, "ground_truth": raw_transcript(best),
            "gt_type": "video_raw_transcript",
            "matched_raw": os.path.basename(best),
            "match_score": bestscore,
            "summary_len": len(summary), "gt_len": len(raw_transcript(best)),
        })
        print(f"  [B站] {title[:36]} <-> {os.path.basename(best)[:30]} (score={bestscore})", flush=True)

# ── scys：_ext_ 总结 ↔ 同ID原文 ──
scys_dir = os.path.join(ROOT, "notes", "_scraped", "scys")
scys_pairs = []
ext_files = [f for f in os.listdir(scys_dir) if "_ext_" in f and f.endswith(".md")]
print(f"\nscys _ext 配对 {len(ext_files)} 个，抽前若干...", flush=True)
for ef in ext_files:
    try:
        base = ef.split("_ext_")[0]
        orig = os.path.join(scys_dir, base + ".md")
        sumf = os.path.join(scys_dir, ef)
        if not os.path.exists(orig):
            continue
        orig_txt = open(orig, encoding="utf-8", errors="ignore").read()
        sum_txt = open(sumf, encoding="utf-8", errors="ignore").read()
        # 去掉 _ext 第一行乱码标题与 "来源：my.feishu" 自引
        sum_lines = [l for l in sum_txt.splitlines() if "my.feishu.cn/wiki" not in l and not l.startswith("# ‌") and not l.startswith("# ⁡")]
        sum_body = "\n".join(sum_lines).strip()
        # 原文去掉 frontmatter
        orig_lines = [l for l in orig_txt.splitlines() if not l.startswith(">")]
        orig_body = "\n".join(orig_lines).strip()
        if len(sum_body) < 200 or len(orig_body) < 500:
            continue
    except Exception as e:
        print(f"  [scys skip] {ef}: {e}", flush=True)
        continue
    scys_pairs.append({
        "platform": "生财", "account": "scys",
        "title": base,
        "feishu_url": "",
        "src": "",
        "summary": sum_body, "ground_truth": orig_body,
        "gt_type": "scys_cached_original",
        "matched_raw": ef,
        "summary_len": len(sum_body), "gt_len": len(orig_body),
    })

# 公众号：无本地缓存
wechat_nodes = [(p, n) for p, n in arts if p[0] == "公众号"]
wechat_info = [{"title": n.get("title",""), "account": p[1],
               "src": extract_source_url(fetch_body(n.get("obj_token","")))}
              for p, n in wechat_nodes[:6]]

out = {
    "video_pairs": video_pairs,
    "scys_pairs": scys_pairs,
    "wechat_no_cache": wechat_info,
}
json.dump(out, open(os.path.join(AUDIT, "content_pairs.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print(f"\n✅ 配对完成: 视频 {len(video_pairs)} / 生财 {len(scys_pairs)} / 公众号(无缓存){len(wechat_info)}")
print(f"   → {os.path.join(AUDIT, 'content_pairs.json')}")
