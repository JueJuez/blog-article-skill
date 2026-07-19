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
  const out = {};
  // A) 裸 timedtext
  try {
    const r = await fetch('https://www.youtube.com/api/timedtext?lang=en&v='+VID+'&fmt=json3');
    const t = await r.text();
    out.bare = {status:r.status, ctype:r.headers.get('content-type'), len:t.length, head:t.slice(0,80)};
  } catch(e){ out.bare = {err:e.message}; }
  // B) get_transcript + SAPISIDHASH
  try {
    const ck = document.cookie.split('; ').reduce((o,c)=>{const i=c.indexOf('=');o[c.slice(0,i)]=c.slice(i+1);return o;},{});
    const sap = ck['SAPISID'] || ck['__Secure-1PAPISID'] || ck['__Secure-3PAPISID'];
    out.hasSapi = !!sap;
    if (sap) {
      const now = Math.floor(Date.now()/1000);
      const data = new TextEncoder().encode(now+' '+sap+' https://www.youtube.com');
      const buf = await crypto.subtle.digest('SHA-1', data);
      const hex = [...new Uint8Array(buf)].map(b=>b.toString(16).padStart(2,'0')).join('');
      const auth = 'SAPISIDHASH '+now+'_'+hex;
      const vb = Array.from(VID).map(c=>c.charCodeAt(0));
      const params = btoa(String.fromCharCode(0x12, vb.length, ...vb));
      const res = await fetch('https://www.youtube.com/youtubei/v1/get_transcript?key='+key, {
        method:'POST', headers:{'Content-Type':'application/json','Authorization':auth},
        body: JSON.stringify({context: ctx, params: params})
      });
      const j = await res.json();
      if (j.actions) {
        const cg = j.actions[0].updateEngagementPanelAction.content.transcriptRenderer.contentBody.transcriptBodyRenderer.cueGroups;
        out.gt = {ok:true, cueCount: cg.length, sample: cg.slice(0,2).map(g=>({t:g.transcriptCueRenderer.cue.text,s:g.transcriptCueRenderer.startOffsetSec}))};
      } else out.gt = {ok:false, head: JSON.stringify(j).slice(0,150)};
    }
  } catch(e){ out.gt = {err:e.message}; }
  return JSON.stringify(out);
})()
"""
raw = cdp.eval_js(JS, timeout_ms=30000)
cdp.close()
print(json.dumps(json.loads(raw), ensure_ascii=False, indent=2))
