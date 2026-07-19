"""查字幕轨所有可能位置，避免循环引用。"""
import json, urllib.request, websocket

VID = '05vPagqJ7nM'
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
pages = json.loads(op.open('http://127.0.0.1:9222/json/list', timeout=5).read())
ws_url = [p['webSocketDebuggerUrl'] for p in pages if VID in p.get('url','') and p.get('webSocketDebuggerUrl')][0]
ws = websocket.create_connection(ws_url, timeout=60)
_m = 0
def call(m, p=None):
    global _m; _m += 1
    ws.send(json.dumps({'id': _m, 'method': m, 'params': p or {}}))
    while True:
        r = json.loads(ws.recv())
        if r.get('id') == _m:
            return r

call('Runtime.enable')
r = call('Runtime.evaluate', {
    'expression': """
(() => {
  const safe = (o) => { try { return JSON.stringify(o); } catch(e) { return 'CIRCULAR'; } };
  const out = {};
  const ytp = window.ytInitialPlayerResponse || {};
  out.ytp_caps_keys = Object.keys(ytp.captions || {});
  out.ytp_tracks = (ytp.captions && ytp.captions.captionTracks || []).length;
  // 搜索 ytInitialData 中的 playerCaptionsTracklistRenderer
  const ytd = window.ytInitialData || {};
  out.has_ytInitialData = !!window.ytInitialData;
  let found = null;
  const walk = (node, depth) => {
    if (found || depth > 12) return;
    if (node && typeof node === 'object') {
      if (node.playerCaptionsTracklistRenderer) { found = node.playerCaptionsTracklistRenderer; return; }
      for (const k in node) walk(node[k], depth+1);
    }
  };
  walk(ytd, 0);
  if (found) {
    out.found_in = 'ytInitialData';
    out.found_tracks = (found.captionTracks||[]).map(t => ({
      lang: t.languageCode, base: (t.baseUrl||'').slice(0,90), hasBase: !!t.baseUrl, fmt: t.format
    }));
  } else {
    out.found_in = 'NOT FOUND in ytInitialData';
  }
  return out;
})()
""",
    'returnByValue': True
})
v = r.get('result', {})
if v.get('exceptionDetails'):
    print('EXC:', json.dumps(v['exceptionDetails'], ensure_ascii=False)[:500])
else:
    print(json.dumps(v.get('value', {}), ensure_ascii=False, indent=2))
ws.close()
