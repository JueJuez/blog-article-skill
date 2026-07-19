"""直接调字幕接口（正确路径 playerCaptionsTracklistRenderer.captionTracks）。
页面内 fetch baseUrl -> 返回正文 -> 解析 -> 送桥生成笔记。不监听网络。
"""
import json, time, urllib.request, websocket
from datetime import timedelta

VID = '05vPagqJ7nM'
URL = f'https://www.youtube.com/watch?v={VID}'
BRIDGE = 'http://127.0.0.1:8899/submit'

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

call('Page.enable'); call('Runtime.enable')

# 取正确路径的 baseUrl，页面内 fetch 返回正文（存 window.__TRANSCRIPT__）
r = call('Runtime.evaluate', {
    'expression': """
(async () => {
  try {
    const ytp = window.ytInitialPlayerResponse || {};
    const list = (ytp.captions || {}).playerCaptionsTracklistRenderer || {};
    const tracks = list.captionTracks || [];
    if (!tracks.length) return {err: 'no tracks', keys: Object.keys(ytp.captions||{})};
    let url = (tracks[0].baseUrl || '').replace(/^http:/, 'https:');
    const resp = await fetch(url, {credentials: 'include', headers: {'Referer': location.href}});
    const txt = await resp.text();
    window.__TRANSCRIPT__ = txt;
    return {status: resp.status, len: txt.length, lang: tracks[0].languageCode, head: txt.slice(0,160)};
  } catch(e) { return {err: e.toString(), stack: (e.stack||'').slice(0,200)}; }
})()
""",
    'awaitPromise': True, 'returnByValue': True
})
val = r.get('result', {}).get('value')
print('FETCH:', json.dumps(val, ensure_ascii=False), flush=True)
if not val or not val.get('len'):
    print('FAILED:', json.dumps(val, ensure_ascii=False))
    ws.close(); raise SystemExit(1)

# 读正文
body = call('Runtime.evaluate', {'expression': 'window.__TRANSCRIPT__', 'returnByValue': True}).get('result', {}).get('value', '')
print('BODY len:', len(body), flush=True)

# 解析 json3
cap = json.loads(body)
lines = []
for ev in cap.get('events', []):
    start = ev.get('tStartMs', 0)
    text = ''.join(s.get('utf8', '') for s in ev.get('segs', []))
    text = text.replace('\n', ' ').strip()
    if not text:
        continue
    t = timedelta(milliseconds=start)
    ts = f'{t.seconds//60:02d}:{t.seconds%60:02d}'
    lines.append(f'[{ts}] {text}')
transcript = '\n'.join(lines)
print(f'LINES={len(lines)} CHARS={len(transcript)}', flush=True)
print(transcript[:500], flush=True)

# 元数据
meta = call('Runtime.evaluate', {
    'expression': """
(function(){
  const ytp = window.ytInitialPlayerResponse || {};
  const vd = ytp.videoDetails || {};
  const title = (document.querySelector('h1.title yt-formatted-string')||{}).textContent
            || (document.querySelector('h1.style-scope.ytd-watch-flexy')||{}).textContent
            || vd.title || '';
  const desc = ((document.querySelector('#description-inline-expander')||{}).textContent || '').slice(0,2000).trim();
  return {title: title.trim(), description: desc, videoId: vd.videoId || '05vPagqJ7nM'};
})()
""",
    'returnByValue': True
}).get('result', {}).get('value', {})
print('META:', json.dumps(meta, ensure_ascii=False), flush=True)

payload = {
    'url': URL, 'video_id': VID, 'title': meta.get('title', 'Untitled'),
    'description': meta.get('description', ''), 'chapters': [], 'lang': 'en', 'text': transcript
}
req = urllib.request.Request(BRIDGE, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                             headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req, timeout=15)
print('BRIDGE:', resp.status, resp.read().decode('utf-8', 'ignore'), flush=True)
ws.close()
