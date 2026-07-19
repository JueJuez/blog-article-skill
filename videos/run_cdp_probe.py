import sys, json
sys.path.insert(0, "D:/Code/Skills/blog-article-skill/videos")
from cdp_helper import CDP, get_page_ws

VID = "05vPagqJ7nM"
URL = f"https://www.youtube.com/watch?v={VID}"

ws = get_page_ws()
print("page ws:", ws[:60])
cdp = CDP(ws)
print("navigating ->", URL)
cdp.navigate(URL)
print("navigated, probing...")

JS = r"""
(async () => {
  const VID = new URLSearchParams(location.search).get('v');
  let ipr = window.ytInitialPlayerResponse;
  let key = window.ytcfg && window.ytcfg.get ? window.ytcfg.get('INNERTUBE_API_KEY') : null;
  let ctx = window.ytcfg && window.ytcfg.get ? window.ytcfg.get('INNERTUBE_CONTEXT') : null;
  let tracks = (ipr && ipr.captions && ipr.captions.playerCaptionsTracklistRenderer)
               ? ipr.captions.playerCaptionsTracklistRenderer.captionTracks : [];
  if (!tracks.length && key && ctx) {
    try {
      const resp = await fetch('https://www.youtube.com/youtubei/v1/player?key='+key, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({context: ctx, videoId: VID})
      });
      const j = await resp.json();
      tracks = (j.captions && j.captions.playerCaptionsTracklistRenderer) ? j.captions.playerCaptionsTracklistRenderer.captionTracks : [];
    } catch(e) { return JSON.stringify({error:'innertube fail: '+e.message}); }
  }
  const langs = tracks.map(t => t.languageCode);
  if (!tracks.length) return JSON.stringify({error:'no caption tracks', hasKey: !!key});
  let chosen = tracks.find(t=>t.languageCode==='en') || tracks[0];
  let xml;
  try { xml = await (await fetch(chosen.baseUrl)).text(); }
  catch(e) { return JSON.stringify({error:'fetch caption fail: '+e.message, langs}); }
  const re = /<text[^>]*>([\s\S]*?)<\/text>/g; let m; const parts=[];
  const dec = {'&amp;':'&','&lt;':'<','&gt;':'>','&#39;':"'",'&quot;':'"'};
  while((m=re.exec(xml))){ parts.push(m[1].replace(/&amp;|&lt;|&gt;|&#39;|&quot;/g, c=>dec[c]||c)); }
  return JSON.stringify({lang: chosen.languageCode, langs, count: parts.length,
                         text: parts.join(' ').slice(0,500)});
})()
"""

res = cdp.eval_js(JS)
cdp.close()
print("=== RESULT ===")
try:
    data = json.loads(res)
    print(json.dumps(data, ensure_ascii=False, indent=2)[:1500])
except Exception as e:
    print("raw:", res[:800])
