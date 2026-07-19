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
  const params = btoa(JSON.stringify({videoId: VID}));
  const r = await fetch('https://www.youtube.com/youtubei/v1/get_transcript?key='+key, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({context: ctx, params: params})
  });
  const j = await r.json();
  // 探测结构
  const out = {topKeys: Object.keys(j)};
  try {
    const cg = j.actions[0].updateEngagementPanelAction.content.transcriptRenderer.contentBody.transcriptBodyRenderer.cueGroups;
    out.cueCount = cg.length;
    out.sample = cg.slice(0,3).map(g => ({
      text: g.transcriptCueRenderer.cue.text,
      start: g.transcriptCueRenderer.startOffsetSec
    }));
  } catch(e) { out.structErr = e.message; out.rawHead = JSON.stringify(j).slice(0,400); }
  return JSON.stringify(out);
})()
"""
raw = cdp.eval_js(JS, timeout_ms=30000)
cdp.close()
print(json.dumps(json.loads(raw), ensure_ascii=False, indent=2))
