import sys, json, time
sys.path.insert(0, "D:/Code/Skills/blog-article-skill/videos")
from cdp_helper import CDP, get_page_ws

cdp = CDP(get_page_ws())
cdp.navigate("https://www.youtube.com/watch?v=05vPagqJ7nM")
time.sleep(6)

JS = r"""
(() => {
  const out = {};
  const all = [...document.querySelectorAll('*')];
  const cand = [];
  for (const e of all) {
    const t = (e.textContent||'').trim().replace(/\s+/g,' ');
    if (/transcript|字幕|caption/i.test(t) && t.length < 60) {
      cand.push({tag:e.tagName, t, role:e.getAttribute('role'), aria:e.getAttribute('aria-label')});
    }
  }
  out.transcriptHits = cand.slice(0,20);
  // 顶栏更多按钮
  out.moreBtns = [...document.querySelectorAll('button')].map(b=>(b.getAttribute('aria-label')||b.textContent.trim())).filter(Boolean).slice(0,30);
  // 是否存在 transcript 相关 renderer
  out.hasSeg = !!document.querySelector('ytd-transcript-segment-renderer');
  out.hasEngagement = !!document.querySelector('ytd-engagement-panel-section-list-renderer');
  return JSON.stringify(out);
})()
"""
raw = cdp.eval_js(JS, timeout_ms=20000)
cdp.close()
print(json.dumps(json.loads(raw), ensure_ascii=False, indent=2)[:2000])
