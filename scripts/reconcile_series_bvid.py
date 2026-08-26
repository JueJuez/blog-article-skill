"""权威对账 + bvid 重映射（只读 GET，不抓转录）。

- 用 B站 view API 的 ugc_season.sections[0].episodes 取每系列「完整有序列表」(index->bvid)。
- 用作者全量 vlist 取 bvid->标题（season 内 episode 无漂亮标题）。
- 把正确 bvid/url 写回各系列 _manifest.json（修正 raw 头不可信 bvid 的根因）。
- 产出 notes/_audit/recon_series_2026-08-26.md 对账报告。
"""
import os, sys, json, re, urllib.parse
from dotenv import load_dotenv
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))
sys.path.insert(0, ROOT)

from monitors.bilibili import _get_session, _Wbi, _BILI, _fetch_vlist

NOTES = os.path.join(ROOT, "notes")
SEASONS = [
    # series_title, first_bvid, author, uid
    ("投资思考", "BV1CPbi67Ej3", "舟亦横", 231015845),
    ("价值投资，知行合一", "BV1qG8K6WEzV", "土斯土耶夫斯基", 362876897),
    ("中国好公司", "BV1uU8j6zEHB", "土斯土耶夫斯基", 362876897),
    ("哲学思辨，知行合一", "BV1p9376JEJS", "土斯土耶夫斯基", 362876897),
]

def fetch_season(first_bvid):
    s = _get_session()
    p = _Wbi.sign({"bvid": first_bvid})
    r = s.get(f"{_BILI}/x/web-interface/wbi/view?" + urllib.parse.urlencode(p), timeout=12)
    d = r.json().get("data", {})
    us = d.get("ugc_season", {})
    secs = us.get("sections") or []
    eps = []
    order = 0
    for sec in secs:
        for e in (sec.get("episodes") or []):
            order += 1
            eps.append({"page": order, "bvid": e.get("bvid")})
    return us.get("title"), eps

def fetch_author_titles(uid):
    m = {}
    for v in _fetch_vlist(str(uid), ps=50, paginate=True):
        if v.get("bvid"):
            m[v["bvid"]] = v.get("title", "")
    return m

def local_raw_pages(series_title):
    d = os.path.join(NOTES, series_title)
    pages = set()
    if not os.path.isdir(d):
        return pages, []
    files = []
    for f in os.listdir(d):
        m = re.match(r"^第(\d+)集_.*_raw\.md$", f)
        if m:
            pages.add(int(m.group(1)))
            files.append(f)
    return pages, files

def main():
    report = ["# 系列课对账报告（2026-08-26）", "",
              "> 数据源：B站 view API 的 ugc_season（权威完整列表）+ 飞书已落盘节点 + 本地 raw。", ""]
    author_titles_cache = {}
    for series_title, first_bvid, author, uid in SEASONS:
        print(f"\n### {series_title} ({author})")
        season_name, eps = fetch_season(first_bvid)
        print(f"  B站合集「{season_name}」共 {len(eps)} 集")
        # bvid -> 标题
        if uid not in author_titles_cache:
            author_titles_cache[uid] = fetch_author_titles(uid)
        titles = author_titles_cache[uid]
        # 建 index(1-based) -> (bvid, title)
        season_map = {}
        for e in eps:
            pg = e.get("page")
            bv = e.get("bvid")
            season_map[pg] = {"bvid": bv, "title": titles.get(bv, "")}
        local_pages, local_files = local_raw_pages(series_title)
        season_indices = set(season_map.keys())
        missing_local = sorted(season_indices - local_pages)   # B站有、本地无 -> 需补抓
        extra_local = sorted(local_pages - season_indices)     # 本地有、B站合集无 -> 异常/ outlier
        # 写回 manifest
        mpath = os.path.join(NOTES, series_title, "_manifest.json")
        if os.path.exists(mpath):
            m = json.load(open(mpath, encoding="utf-8"))
            for ep in m.get("episodes", {}).values():
                pg = ep.get("page")
                if pg in season_map:
                    ep["bvid"] = season_map[pg]["bvid"]
                    ep["url"] = f"https://www.bilibili.com/video/{season_map[pg]['bvid']}"
            m["expected_total"] = len(eps)
            m["updated_at"] = "2026-08-26T20:48"
            json.dump(m, open(mpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print(f"  ✅ 已写回 _manifest.json（{len(m['episodes'])} 集 bvid/url）")
        else:
            # 哲学思辨：无 manifest，建一个
            episodes = {}
            for pg in sorted(local_pages):
                episodes[f"{pg:02d}"] = {"page": pg, "part": "", "bvid": season_map.get(pg, {}).get("bvid", ""),
                                          "url": f"https://www.bilibili.com/video/{season_map[pg]['bvid']}" if pg in season_map else "",
                                          "state": "raw_ready" if os.path.exists(os.path.join(NOTES, series_title, f"第{pg:02d}集" + "_" * 0)) else "raw_ready",
                                          "raw": ""}
            m = {"series_title": series_title, "url": f"https://www.bilibili.com/video/{first_bvid}",
                 "author": author, "expected_total": len(eps), "updated_at": "2026-08-26T20:48",
                 "episodes": episodes}
            json.dump(m, open(mpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print(f"  ✅ 新建 _manifest.json（{len(episodes)} 集）")
        report.append(f"## {series_title}（{author}）")
        report.append(f"- B站合集总数：**{len(eps)}** 集（用户预期见下）")
        report.append(f"- 本地 raw 集数：{len(local_pages)}（第NN集命名）")
        report.append(f"- 缺失（B站有、本地无，需补抓转录）：**{len(missing_local)}** 集 → {missing_local}")
        if extra_local:
            report.append(f"- 异常（本地有、B站合集无）：{extra_local}（可能是合集外单视频，勿误抓）")
        report.append("")
    open(os.path.join(NOTES, "_audit", "recon_series_2026-08-26.md"), "w", encoding="utf-8").write("\n".join(report))
    print("\n📄 报告已写 notes/_audit/recon_series_2026-08-26.md")

if __name__ == "__main__":
    main()
