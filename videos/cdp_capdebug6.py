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
  const vb = Array.from(VID).map(c=>c.charCodeAt(0));
  const params = btoa(String.fromCharCode(0x12, vb.length, ...vb));
  const out = {};
  for (const withAuth of [false, true]) {
    const headers = {'Content-Type':'application/json'};
    if (withAuth) {
      try {
        const ck = document.cookie.split('; ').reduce((o,c)=>{const i=c.indexOf('=');o[c.slice(0,i)]=c.slice(i+1);return o;},{});
        const sap = ck['SAPISID'] || ck['__Secure-1PAPISID'] || ck['APISID'];
        if (sap) {
          const now = Math.floor(Date.now()/1000);
          const data = new TextEncoder().encode(now+' '+sap+' https://www.youtube.com');
          const buf = await crypto.subtle.digest('SHA-1', data);
          const hex = [...new Uint8Array(buf)].map(b=>b.toString(16).padStart(2,'0')).join('');
          headers['Authorization'] = 'SAPISIDHASH '+now+'_'+hex;
          out.sapFound = true;
        } else out.sapFound = false;
      } catch(e){ out.authErr = e.message; }
    }
    try {
      const r = await fetch('https://www.youtube.com/youtubei/v1/get_transcript?key='+key, {
        method:'POST', headers:headers, body: JSON.stringify({context: ctx, params: params})
      });
      const j = await r.json();
      if (j.actions) {
        const cg = j.actions[0].updateEngagementPanelAction.content.transcriptRenderer.contentBody.transcriptBodyRenderer.cueGroups;
        out['auth_'+withAuth] = {ok:true, cueCount: cg.length,
          sample: cg.slice(0,3).map(g=>({t:g.transcriptCueRenderer.cue.text, s:g.transcriptCueRenderer.startOffsetSec}))};
      } else out['auth_'+withAuth] = {ok:false, err: j.error && j.error.message, head: JSON.stringify(j).slice(0,120)};
    } catch(e){ out['auth_'+withAuth] = {err:e.message}; }
  }
  return JSON.stringify(out);
})()
"""
raw = cdp.eval_js(JS, timeout_ms=30000)
cdp.close()
print(json.dumps(json.loads(raw), ensure_ascii=False, indent=2))
