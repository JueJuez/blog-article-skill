import sys, json, time
sys.path.insert(0, "D:/Code/Skills/blog-article-skill/videos")
from cdp_helper import CDP, get_page_ws

cdp = CDP(get_page_ws())
cdp.navigate("https://www.youtube.com/watch?v=05vPagqJ7nM")
time.sleep(6)

JS = r"""
(() => {
  const wm = document.querySelector('ytd-watch-metadata');
  const out = {wmExists: !!wm};
  if (wm) {
    out.buttons = [...wm.querySelectorAll('button')].map(b => ({
      aria: b.getAttribute('aria-label'),
      text: (b.textContent||'').trim().slice(0,20),
      id: b.id
    }));
    // 也看 engagement panel 的 tab
    const tabs = [...document.querySelectorAll('ytd-engagement-panel-section-list-renderer tp-yt-paper-tab, ytd-engagement-panel-title-renderer, [role=tab]')]
      .map(t=>(t.textContent||'').trim().slice(0,20));
    out.engagementTabs = tabs;
  }
  return JSON.stringify(out);
})()
"""
raw = cdp.eval_js(JS, timeout_ms=20000)
cdp.close()
print(json.dumps(json.loads(raw), ensure_ascii=False, indent=2)[:2000])
