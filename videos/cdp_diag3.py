"""最小诊断，打印原始返回。"""
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
(function(){
  var out = {};
  out.ytp_caps_keys = Object.keys((window.ytInitialPlayerResponse||{}).captions || {});
  out.ytp_track_count = (((window.ytInitialPlayerResponse||{}).captions||{}).captionTracks||[]).length;
  out.hasYTD = !!window.ytInitialData;
  return out;
})()
""",
    'returnByValue': True
})
print('RAW:', json.dumps(r, ensure_ascii=False)[:1500])
ws.close()
