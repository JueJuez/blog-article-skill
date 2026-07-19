import sys, json, time
sys.path.insert(0, "D:/Code/Skills/blog-article-skill/videos")
from cdp_helper import CDP, get_page_ws

cdp = CDP(get_page_ws())
cdp.navigate("https://www.youtube.com/watch?v=05vPagqJ7nM")
time.sleep(6)  # 等 SPA 初始化

diag = cdp.eval_js(r"""
(() => {
  const out = {};
  out.title = document.title;
  out.href = location.href;
  out.hasYtcfg = typeof window.ytcfg;
  out.hasIPR = typeof window.ytInitialPlayerResponse;
  try { out.ytcfgKeys = Object.keys(window.ytcfg || {}); } catch(e){ out.ytcfgKeysErr = e.message; }
  try {
    const k = window.ytcfg && window.ytcfg.get ? window.ytcfg.get('INNERTUBE_API_KEY') : null;
    out.innertubeKey = k ? k.slice(0,8)+'...' : null;
  } catch(e){ out.keyErr = e.message; }
  try {
    const ipr = window.ytInitialPlayerResponse;
    out.iprExists = !!ipr;
    if (ipr) {
      out.videoDetails = ipr.videoDetails ? (ipr.videoDetails.title||'') : null;
      out.hasCaptions = !!(ipr.captions && ipr.captions.playerCaptionsTracklistRenderer);
      out.capCount = (ipr.captions && ipr.captions.playerCaptionsTracklistRenderer)
                     ? ipr.captions.playerCaptionsTracklistRenderer.captionTracks.length : 0;
    }
  } catch(e){ out.iprErr = e.message; }
  // 是否同意页
  out.bodyText = document.body ? document.body.innerText.slice(0,200) : '';
  return JSON.stringify(out);
})()
""")
cdp.close()
print("=== DIAG ===")
print(json.dumps(json.loads(diag), ensure_ascii=False, indent=2))
