import sys, json, time
sys.path.insert(0, "D:/Code/Skills/blog-article-skill/videos")
from cdp_helper import CDP, get_page_ws

cdp = CDP(get_page_ws())
cdp.navigate("https://www.youtube.com/watch?v=05vPagqJ7nM")
time.sleep(5)

JS = r"""
(async () => {
  const VID = new URLSearchParams(location.search).get('v');
  const key = window.ytcfg.get('INNERTUBE_API_KEY');
  const ctx = window.ytcfg.get('INNERTUBE_CONTEXT');
  const r = await fetch('https://www.youtube.com/youtubei/v1/player?key='+key, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({context: ctx, videoId: VID})
  });
  const j = await r.json();
  const tracks = j.captions.playerCaptionsTracklistRenderer.captionTracks;
  const out = [];
  for (const t of tracks) {
    let info = {lang: t.languageCode, baseHost: new URL(t.baseUrl).host};
    try {
      const res = await fetch(t.baseUrl);
      const txt = await res.text();
      info.status = res.status; info.ctype = res.headers.get('content-type');
      info.len = txt.length; info.head = txt.slice(0,120);
    } catch(e) { info.err = e.message; }
    out.push(info);
  }
  return JSON.stringify(out);
})()
"""
raw = cdp.eval_js(JS, timeout_ms=30000)
cdp.close()
print(json.dumps(json.loads(raw), ensure_ascii=False, indent=2))
