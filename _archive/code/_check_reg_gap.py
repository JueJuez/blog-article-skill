import json, os
from datetime import datetime

VAULT = r'D:\Code\Obsidian\obsidian-path\AI 总结笔记'

# registered urls
done = json.load(open('notes/_scraped/migrate/done_queue.json', encoding='utf-8'))
done_urls = {e.get('url', '') for e in done}

# registry
reg = json.load(open('notes/_meta/summary_registry.json', encoding='utf-8'))
reg_items = list(reg.values()) if isinstance(reg, dict) else reg

BATCH_START = datetime(2026, 9, 10, 17, 0).timestamp()

# session-scoped summarized notes
sess = [v for v in reg_items if isinstance(v, dict) and v.get('source_url') and v.get('ts', 0) >= BATCH_START]
print('会话窗口(09-10 17:00后)内 registry 标记条数:', len(sess))

sess_not_reg = [v for v in sess if v.get('source_url') not in done_urls]
print('其中 未登记(done_queue 无 url):', len(sess_not_reg))
if sess_not_reg:
    print()
    print('--- 会话内 已总结未登记 清单 ---')
    for v in sess_not_reg:
        fn = v.get('filename', '')
        print(f"  ts={datetime.fromtimestamp(v.get('ts', 0)).strftime('%H:%M')} | {fn.split('/')[-1][:42]} | {v.get('source_url', '')[:62]}")

# Also: done_queue entries whose url NOT in registry (phantom registration: registered but no vault note)
reg_urls = {v.get('source_url') for v in reg_items if isinstance(v, dict) and v.get('source_url')}
phantom = [e for e in done if e.get('url', '') not in reg_urls]
print()
print('done_queue 中 url 不在 registry(疑似幻影登记):', len(phantom))
for e in phantom[:15]:
    print('  ', e.get('folder', '').split('/')[-2], '|', e.get('title', '')[:34], '|', e.get('url', '')[:50])

# Comprehensive: ALL 【监控】-tree notes (pending_plan scope, any time) summarized but not registered
print()
print('=== 全量(不限时间) 【监控】树 已总结未登记 检查 ===')
mon = [v for v in reg_items if isinstance(v, dict) and v.get('source_url')
       and '【监控】' in (v.get('filename') or '')]
print('【监控】树 registry 标记条数:', len(mon))
mon_not_reg = [v for v in mon if v.get('source_url') not in done_urls]
print('其中 未登记:', len(mon_not_reg))
if mon_not_reg:
    # group by domain
    from collections import Counter
    domc = Counter()
    for v in mon_not_reg:
        fn = v.get('filename', '')
        # domain = 【监控】/<平台>/<域>
        parts = fn.split('/')
        dom = '/'.join(parts[1:3]) if len(parts) > 2 else fn
        domc[dom] += 1
    print('按域分布:', dict(domc))
    print('--- 明细(前30) ---')
    for v in mon_not_reg[:30]:
        fn = v.get('filename', '')
        print(f"  ts={datetime.fromtimestamp(v.get('ts', 0)).strftime('%Y-%m-%d')} | {fn.split('/')[-1][:40]} | {v.get('source_url', '')[:55]}")
else:
    print('=> 【监控】树全部已登记，无漏登')

