import sys, json, time, websocket
sys.path.insert(0, "D:/Code/Skills/blog-article-skill/videos")
from cdp_helper import CDP, get_page_ws

cdp = CDP(get_page_ws())
cdp.navigate("https://www.youtube.com/watch?v=05vPagqJ7nM")
time.sleep(5)

# 1) 读 cookie 名称（含 httpOnly）
cdp.send("Network.enable")
cookies = cdp.send("Network.getCookies", {"urls": ["https://www.youtube.com", "https://www.google.com"]}, await_id=True)
names = sorted({c["name"] for c in cookies.get("result", {}).get("cookies", [])})
print("=== cookie 名称 ===")
print(names)
login_cookies = [n for n in names if n in ("SAPISID","APISID","__Secure-1PAPISID","__Secure-3PAPISID","SSID","HSID","LSID","SID")]
print("登录相关:", login_cookies)

# 2) get_transcript 带客户端头
JS = r"""
(async () => {
  const VID = new URLSearchParams(location.search).get('v');
  const key = window.ytcfg.get('INNERTUBE_API_KEY');
  const ctx = window.ytcfg.get('INNERTUBE_CONTEXT');
  const cver = (ctx.client && ctx.client.clientVersion) || '2.20260715.04.00';
  const vb = Array.from(VID).map(c=>c.charCodeAt(0));
  const params = btoa(String.fromCharCode(0x12, vb.length, ...vb));
  const out = {};
  for (const variant of ['base','clienthdr']) {
    const headers = {'Content-Type':'application/json'};
    if (variant === 'clienthdr') {
      headers['X-YouTube-Client-Name'] = '1';
      headers['X-YouTube-Client-Version'] = cver;
      headers['Origin'] = 'https://www.youtube.com';
      headers['Referer'] = 'https://www.youtube.com/watch?v='+VID;
    }
    try {
      const r = await fetch('https://www.youtube.com/youtubei/v1/get_transcript?key='+key, {
        method:'POST', headers:headers, body: JSON.stringify({context: ctx, params: params})
      });
      const j = await r.json();
      if (j.actions) {
        const cg = j.actions[0].updateEngagementPanelAction.content.transcriptRenderer.contentBody.transcriptBodyRenderer.cueGroups;
        out[variant] = {ok:true, cueCount: cg.length,
          sample: cg.slice(0,2).map(g=>({t:g.transcriptCueRenderer.cue.text, s:g.transcriptCueRenderer.startOffsetSec}))};
      } else out[variant] = {ok:false, err: j.error && j.error.message};
    } catch(e){ out[variant] = {err:e.message}; }
  }
  return JSON.stringify(out);
})()
"""
print("=== get_transcript 变体 ===")
print(json.dumps(json.loads(cdp.eval_js(JS, timeout_ms=30000)), ensure_ascii=False, indent=2))
cdp.close()
