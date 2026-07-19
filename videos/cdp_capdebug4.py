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
  const cver = (ctx.client && ctx.client.clientVersion) || window.ytcfg.get('INNERTUBE_CLIENT_VERSION') || '2.20240101.00.00';
  const r0 = await fetch('https://www.youtube.com/youtubei/v1/player?key='+key, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({context: ctx, videoId: VID})
  });
  const j = await r0.json();
  const tracks = j.captions.playerCaptionsTracklistRenderer.captionTracks;
  const H = {
    'X-YouTube-Client-Name':'1',
    'X-YouTube-Client-Version': cver,
    'Accept':'*/*'
  };
  const out = [];
  for (const t of tracks) {
    for (const fmt of ['', '&fmt=json3', '&fmt=srv3']) {
      const url = t.baseUrl + fmt;
      try {
        const res = await fetch(url, {headers: H});
        const txt = await res.text();
        out.push({lang:t.languageCode, fmt, status:res.status, ctype:res.headers.get('content-type'), len:txt.length, head:txt.slice(0,80)});
      } catch(e){ out.push({lang:t.languageCode, fmt, err:e.message}); }
    }
  }
  return JSON.stringify({cver, out});
})()
"""
raw = cdp.eval_js(JS, timeout_ms=30000)
cdp.close()
print(json.dumps(json.loads(raw), ensure_ascii=False, indent=2))
