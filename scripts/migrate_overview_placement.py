"""一次性迁移：把日更总览从「埋在日更里」搬到「账号层下」。

机制（shared/feishu_overview._overview_folder）：
- rebuild(folder) 现在把总览文档建在账号节点下，条目仍枚举自日更容器。
- 同时删除日更容器里残留的旧总览孤儿文档。
- skip_suggest=True：不重抓原文 og 标题（本次只搬位置），省数百次网络请求。

用法：python scripts/migrate_overview_placement.py
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from shared import feishu_overview as fo

STATE = fo.STATE_PATH
db = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
daily_folders = [k for k in db if k.endswith("/日更")]
print(f"发现 {len(daily_folders)} 个 日更 folder 需迁移：")
for fl in daily_folders:
    print("  -", fl)

total = 0
for fl in daily_folders:
    try:
        n = fo.rebuild(fl, skip_suggest=True)
        print(f"  ✅ {fl} → 账号层总览，{n} 条")
        total += 1
    except Exception as e:
        print(f"  ⚠️ {fl} 失败: {e}")
print(f"\n完成 {total}/{len(daily_folders)} 个 日更 总览迁移到账号层。")
