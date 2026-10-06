"""UP 主官方合集（ugc_season）→ BV 映射的确定性抓取与缓存。

用途（2026-10-06）：
- scripts/fetch_up_range.py（UP 全量补齐线）入队时按 BV 反查合集名，显式传
  series 给 shared.routing.resolve_folder → 【监控】/<平台>/<账号>/<合集名>；
- scripts/split_notes_by_season.py 把已落盘在【日更】的笔记按合集分拣迁移。

接口（B站空间页 API，需 BILI_COOKIE 时走 os.environ，缺 cookie 也尝试）：
- GET seasons_series_list?mid=&page_num=&page_size=20  → 合集清单（含 meta.total）
- GET seasons_archives_list?mid=&season_id=&page_num=&page_size=100 → 合集内视频

缓存：notes/_scraped/bili_<uid>_seasons.json（bvid→合集名 + seasons 概览），
默认命中缓存直接返回，--refresh / force=True 重拉。

机械优先：纯 HTTP + JSON 解析，无任何 AI 参与；解析函数（parse_*）与网络
分离，可单测。
"""
import argparse
import json
import os
import random
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_SEASON_PREFIX = "合集·"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def strip_season_prefix(name: str) -> str:
    """「合集·说话之道」→「说话之道」（仅去一次前缀，其余原样保留）。"""
    n = (name or "").strip()
    return n[len(_SEASON_PREFIX):] if n.startswith(_SEASON_PREFIX) else n


def parse_seasons_payload(data: dict) -> list:
    """seasons_series_list.data → [(season_id, 原始名, total), ...]（纯函数）。"""
    out = []
    il = (data or {}).get("items_lists") or {}
    for key in ("seasons_list", "series_list"):
        for s in il.get(key) or []:
            m = s.get("meta") or {}
            sid = m.get("season_id") or m.get("series_id")
            if sid:
                out.append((int(sid), (m.get("name") or "").strip(),
                            int(m.get("total") or 0)))
    return out


def parse_archives_payload(data: dict) -> list:
    """seasons_archives_list.data → [bvid, ...]（纯函数）。"""
    return [a.get("bvid") for a in (data or {}).get("archives") or []
            if a.get("bvid")]


def _get_json(url: str, cookie: str = "") -> dict:
    headers = {"User-Agent": _UA, "Referer": "https://space.bilibili.com/"}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def _seasons_list_page(mid: int, page_num: int, cookie: str) -> dict:
    url = (f"https://api.bilibili.com/x/polymer/web-space/seasons_series_list"
           f"?mid={mid}&page_num={page_num}&page_size=20")
    d = _get_json(url, cookie)
    if d.get("code") != 0:
        raise RuntimeError(f"seasons_series_list code={d.get('code')} {d.get('message')}")
    return d.get("data") or {}


def _season_archives_page(mid: int, season_id: int, page_num: int,
                          cookie: str) -> dict:
    url = (f"https://api.bilibili.com/x/polymer/web-space/seasons_archives_list"
           f"?mid={mid}&season_id={season_id}&page_num={page_num}&page_size=100")
    d = _get_json(url, cookie)
    if d.get("code") != 0:
        raise RuntimeError(
            f"seasons_archives_list(season_id={season_id}) code={d.get('code')} "
            f"{d.get('message')}")
    return d.get("data") or {}


def _seasons_cache_path(uid: int) -> str:
    return os.path.join(ROOT, "notes", "_scraped", f"bili_{uid}_seasons.json")


def load_cached_map(uid: int) -> dict:
    p = _seasons_cache_path(uid)
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p, encoding="utf-8")).get("bvid_map") or {}
    except Exception:
        return {}


def fetch_seasons_map(uid: int, force: bool = False,
                      sleep_range=(1.0, 2.5)) -> dict:
    """返回 {bvid: 合集名（已去「合集·」前缀）}。

    - 一个视频命中多个合集时取先遇到的（API 返回序），不做智能取舍；
    - 缓存命中（force=False 且文件存在）直接返回，不打任何请求；
    - 网络失败向上抛 RuntimeError，由调用方决定降级（补齐线降级为无合集路由）。
    """
    if not force:
        cached = load_cached_map(uid)
        if cached:
            return cached
    cookie = os.environ.get("BILI_COOKIE", "")
    # 1) 合集清单（翻页）
    seasons = []
    page = 1
    while True:
        data = _seasons_list_page(uid, page, cookie)
        got = parse_seasons_payload(data)
        seasons.extend(got)
        total = (((data.get("items_lists") or {}).get("page")) or {}).get("total") or 0
        if not got or len(seasons) >= total:
            break
        page += 1
        time.sleep(random.uniform(*sleep_range))
    # 2) 逐合集拉全量 BV（archives 按 page_size=100 翻页）
    bvid_map: dict = {}
    for sid, raw_name, _total in seasons:
        name = strip_season_prefix(raw_name) or raw_name
        pn = 1
        while True:
            data = _season_archives_page(uid, sid, pn, cookie)
            for bv in parse_archives_payload(data):
                bvid_map.setdefault(bv, name)
            pt = (data.get("page") or {})
            total = int(pt.get("total") or 0)
            if pn * int(pt.get("size") or 100) >= total:
                break
            pn += 1
            time.sleep(random.uniform(*sleep_range))
        time.sleep(random.uniform(*sleep_range))
    cache = {
        "uid": uid,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seasons": [{"season_id": sid, "name": strip_season_prefix(n) or n,
                     "raw_name": n, "total": t} for sid, n, t in seasons],
        "bvid_map": bvid_map,
    }
    os.makedirs(os.path.dirname(_seasons_cache_path(uid)), exist_ok=True)
    with open(_seasons_cache_path(uid), "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    return bvid_map


def main():
    ap = argparse.ArgumentParser(
        description="拉取 UP 主官方合集（ugc_season）→ BV 映射（带缓存）")
    ap.add_argument("--uid", type=int, required=True)
    ap.add_argument("--refresh", action="store_true", help="忽略缓存重拉")
    args = ap.parse_args()
    m = fetch_seasons_map(args.uid, force=args.refresh)
    p = _seasons_cache_path(args.uid)
    cache = json.load(open(p, encoding="utf-8"))
    print(f"合集 {len(cache['seasons'])} 个，映射 BV {len(m)} 条 → {p}")
    for s in cache["seasons"]:
        n = sum(1 for v in m.values() if v == s["name"])
        print(f"  - {s['name']}（season_id={s['season_id']}, total={s['total']},"
              f" 实际映射 {n} 条）")


if __name__ == "__main__":
    main()
