"""临时验证脚本：只读公众号文章列表元数据，确认 weread 代理能回溯到多老。
不抓正文、不落盘、不改任何状态。用于决定 backfill 分批策略。
"""
import sys, os, json, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from monitors.wechat import WereadClient

auth = json.load(open(os.path.join(ROOT, "monitors", ".wechat_auth.json"), encoding="utf-8"))
client = WereadClient(token=auth.get("token", ""), vid=auth.get("vid", ""))

TARGETS = {
    "哥飞": "https://mp.weixin.qq.com/s/pvHsJMEvjWY9VGEhlhcVwA",
    "生财有术": "https://mp.weixin.qq.com/s/SGqYe5wRWV7DUucCzEkBvQ",
}


def fmt(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "?"


for name, share in TARGETS.items():
    print(f"\n===== {name} =====")
    try:
        info = client.resolve_mp(share, force=True)
    except Exception as e:
        print("resolve 失败:", type(e).__name__, str(e)[:200])
        continue
    mp_id = info.get("id", "")
    print("mp_id:", mp_id, "| 名称:", info.get("name"))

    earliest = latest = None
    total = 0
    reached_2026_01 = None
    for page in range(1, 41):  # 最多翻 40 页（~800 篇）
        try:
            items = client.list_articles(mp_id, page)
        except Exception as e:
            print(f"page {page} 错误:", type(e).__name__, str(e)[:150])
            break
        if not items:
            print(f"page {page}: 空 -> 停止")
            break
        total += len(items)
        for it in items:
            pt = it.get("publishTime", 0)
            if pt:
                if earliest is None or pt < earliest:
                    earliest = pt
                if latest is None or pt > latest:
                    latest = pt
                if reached_2026_01 is None and pt < 1769884800:  # 2026-02-01 之前
                    reached_2026_01 = fmt(pt)
        fl = fmt(items[0].get("publishTime", 0))
        ll = fmt(items[-1].get("publishTime", 0))
        print(f"  page {page}: {len(items)} 篇 | {fl} .. {ll}")
        if len(items) < 20:
            print("  末页(count<20) -> 停止")
            break

    if earliest:
        print(f">>> {name}: 共翻 {total} 篇 | 最新 {fmt(latest)} | 最早 {fmt(earliest)} | 触及2026-02前: {reached_2026_01 or '未'}")
    else:
        print(f">>> {name}: 未拿到任何文章时间")
