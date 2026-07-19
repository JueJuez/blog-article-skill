"""诊断：target 0 的真实状态 + 直接 fetch 字幕接口，打印完整异常。"""
import json, time, urllib.request, websocket

VID = '05vPagqJ7nM'
URL = f'https://www.youtube.com/watch?v={VID}'

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
pages = json.loads(op.open('http://127.0.0.1:9222/json/list', timeout=5).read())
# choose the youtube watch page
ws_url = None
for p in pages:
    if VID in p.get('url', '') and p.get('webSocketDebuggerUrl'):
        ws_url = p['webSocketDebuggerUrl']
        break
if not ws_url:
    ws_url = [p['webSocketDebuggerUrl'] for p in pages if p.get('webSocketDebuggerUrl')][0]
print('using target:', ws_url[-40:], flush=True)

ws = websocket.create_connection(ws_url, timeout=60)
_msg_id = 0
def call(method, params=None):
    global _msg_id
    _msg_id += 1
    ws.send(json.dumps({'id': _msg_id, 'method': method, 'params': params or {}}))
    while True:
        r = json.loads(ws.recv())
        if r.get('id') == _msg_id:
            return r

call('Page.enable'); call('Runtime.enable')

# 1) 基础状态
r = call('Runtime.evaluate', {'expression': '({ytp: typeof window.ytInitialPlayerResponse, html: document.documentElement.outerHTML.length})', 'returnByValue': True})
print('STATE:', json.dumps(r.get('result', {}), ensure_ascii=False))
if r.get('result', {}).get('exceptionDetails'):
    print('EXC:', json.dumps(r['result']['exceptionDetails'], ensure_ascii=False))

# 2) 若 ytp 在，直接 fetch 它的 baseUrl
r2 = call('Runtime.evaluate', {
    'expression': """
(async () => {
  try {
    const ytp = window.ytInitialPlayerResponse;
    if (!ytp) return {step:'no ytp'};
    const tracks = (ytp.captions && ytp.captions.captionTracks) || [];
    if (!tracks.length) return {step:'no tracks', caps: !!ytp.captions};
    let url = (tracks[0].baseUrl || '').replace(/^http:/, 'https:');
    const r = await fetch(url, {credentials:'include', headers:{'Referer': location.href}});
    const t = await r.text();
    return {step:'fetched', status:r.status, len:t.length, head:t.slice(0,120), url:url.slice(0,80)};
  } catch(e) { return {step:'throw', err: e.toString()}; }
})()
""",
    'awaitPromise': True, 'returnByValue': True
})
print('FETCH:', json.dumps(r2.get('result', {}), ensure_ascii=False))
if r2.get('result', {}).get('exceptionDetails'):
    print('EXC2:', json.dumps(r2['result']['exceptionDetails'], ensure_ascii=False))
ws.close()
