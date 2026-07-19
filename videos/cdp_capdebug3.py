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
  function b64(arr){ return btoa(String.fromCharCode.apply(null, arr)); }
  const vb = Array.from(VID).map(c=>c.charCodeAt(0));
  // 试两种 field tag: 0x0a=field1, 0x12=field2
  const tries = {};
  for (const tag of [0x0a, 0x12]) {
    const inner = [tag, vb.length, ...vb];
    const params = b64(inner);
    try {
      const r = await fetch('https://www.youtube.com/youtubei/v1/get_transcript?key='+key, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({context: ctx, params: params})
      });
      const j = await r.json();
      if (j.actions) {
        const cg = j.actions[0].updateEngagementPanelAction.content.transcriptRenderer.contentBody.transcriptBodyRenderer.cueGroups;
        tries['tag_'+tag] = {ok:true, cueCount: cg.length,
          sample: cg.slice(0,2).map(g=>({t:g.transcriptCueRenderer.cue.text, s:g.transcriptCueRenderer.startOffsetSec}))};
      } else {
        tries['tag_'+tag] = {ok:false, err: j.error ? j.error.message : 'no actions', head: JSON.stringify(j).slice(0,150)};
      }
    } catch(e){ tries['tag_'+tag] = {ok:false, err: e.message}; }
  }
  return JSON.stringify(tries);
})()
"""
raw = cdp.eval_js(JS, timeout_ms=30000)
cdp.close()
print(json.dumps(json.loads(raw), ensure_ascii=False, indent=2))
