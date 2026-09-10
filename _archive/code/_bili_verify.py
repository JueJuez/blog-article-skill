"""优化后一次性验证：B站视频 + 动态 是否都通。"""
import os
import sys
import json
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from monitors.run import _load_env  # noqa: F401
from monitors.bilibili import BilibiliSource  # noqa: E402

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "subscriptions.json"),
          "r", encoding="utf-8") as f:
    subs = json.load(f)

state = {"sources": {}}
ok = fail = 0
vid_total = dyn_total = charging = 0
for b in subs.get("bilibili", []):
    name, uid = b.get("name", ""), b["uid"]
    print(f"\n--- [{name}] uid={uid} ---")
    t0 = time.time()
    try:
        items = BilibiliSource(uid).discover(state, first_run_limit=5, mode="first")
        vids = [i for i in items if i["route"] == "video"]
        dyns = [i for i in items if i["route"] == "dynamic"]
        vid_total += len(vids); dyn_total += len(dyns)
        charging += sum(1 for i in vids if i.get("is_charging"))
        print(f"  视频 {len(vids)} | 动态 {len(dyns)} | 耗时 {time.time()-t0:.1f}s")
        for i in vids[:3]:
            tag = " [充电专属]" if i.get("is_charging") else ""
            print(f"   ▶ {i['title'][:40]}{tag}")
        for i in dyns[:3]:
            print(f"   ✦ {i['title'][:40]}  ({len(i.get('content',''))}字)")
        ok += 1
    except Exception as e:
        fail += 1
        print(f"  ❌ {type(e).__name__}: {e}")

print("\n" + "=" * 40)
print(f"成功 {ok}/失败 {fail} | 视频 {vid_total}（充电 {charging}）| 动态 {dyn_total}")
