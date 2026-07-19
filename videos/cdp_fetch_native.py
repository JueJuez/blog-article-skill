import urllib.request, json, time, websocket

VID = "05vPagqJ7nM"
URL = f"https://www.youtube.com/watch?v={VID}"

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
pages = json.loads(op.open("http://127.0.0.1:9222/json/list", timeout=5).read())
ws_url = [p["webSocketDebuggerUrl"] for p in pages if "webSocketDebuggerUrl" in p][0]
ws = websocket.create_connection(ws_url, timeout=60)

_msg_id = 0
def call(method, params=None):
    global _msg_id
    _msg_id += 1
    ws.send(json.dumps({"id": _msg_id, "method": method, "params": params or {}}))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == _msg_id:
            return r

# enable domains
call("Page.enable")
call("Runtime.enable")
call("Network.enable")

# navigate
nav = call("Page.navigate", {"url": URL})
print("navigate frame:", nav.get("result", {}).get("frameId", "?")[:10])

# wait for Page.loadEventFired
for _ in range(60):
    ev = json.loads(ws.recv())
    if ev.get("method") == "Page.loadEventFired":
        break
    time.sleep(0.5)
else:
    time.sleep(3)

script = r"""
(async () => {
  const ytcfg = window.ytcfg;
  if (!ytcfg) return {err: 'no ytcfg'};
  const apiKey = ytcfg.get('INNERTUBE_API_KEY');
  const ctx = ytcfg.get('INNERTUBE_CONTEXT');
  if (!apiKey || !ctx) return {err: 'missing innertube key/context'};

  const payload = {context: ctx, videoId: '05vPagqJ7nM'};
  const url = `/youtubei/v1/player?key=${apiKey}&prettyPrint=false`;
  try {
    const r = await fetch(url, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-YouTube-Client-Name': ytcfg.get('INNERTUBE_CONTEXT_CLIENT_NAME', '1'),
        'X-YouTube-Client-Version': ytcfg.get('INNERTUBE_CONTEXT_CLIENT_VERSION', ''),
        'X-Origin': 'https://www.youtube.com'
      },
      body: JSON.stringify(payload)
    });
    const data = await r.json();
    const tracks = data?.captions?.captionTracks || [];
    if (tracks.length === 0) return {err: 'no caption tracks from player API'};
    const tr = tracks[0];
    const capUrl = tr.baseUrl;
    const capFmt = tr.format || 'unknown';
    const capResp = await fetch(capUrl, {credentials: 'include'});
    const capText = await capResp.text();
    return {
      player_status: r.status,
      track_name: tr.name?.simpleText || '?',
      track_lang: tr.languageCode,
      format: capFmt,
      cap_status: capResp.status,
      cap_len: capText.length,
      cap_first: capText.slice(0, 400),
      err: null
    };
  } catch (e) {
    return {err: e.toString(), stack: e.stack};
  }
})()
"""
res = call("Runtime.evaluate", {"expression": script, "awaitPromise": True, "returnByValue": True})
print(json.dumps(res, ensure_ascii=False, indent=2))
ws.close()
