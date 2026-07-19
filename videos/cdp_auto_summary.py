import urllib.request, json, time, websocket, base64
from datetime import timedelta

VID = "05vPagqJ7nM"
URL = f"https://www.youtube.com/watch?v={VID}"
BRIDGE = "http://127.0.0.1:8899/submit"

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
pages = json.loads(op.open("http://127.0.0.1:9222/json/list", timeout=5).read())
ws_url = [p["webSocketDebuggerUrl"] for p in pages if "webSocketDebuggerUrl" in p][0]
ws = websocket.create_connection(ws_url, timeout=90)

_msg_id = 0
captured = {}

def call(method, params=None):
    global _msg_id
    _msg_id += 1
    ws.send(json.dumps({"id": _msg_id, "method": method, "params": params or {}}))
    while True:
        r = json.loads(ws.recv())
        if r.get("method") in ("Network.requestWillBeSent", "Network.responseReceived", "Network.loadingFinished"):
            handle_event(r)
        if r.get("id") == _msg_id:
            return r

def handle_event(r):
    p = r.get("params", {})
    m = r.get("method")
    if m == "Network.requestWillBeSent":
        url = p.get("request", {}).get("url", "")
        if "timedtext" in url and VID in url:
            captured[p["requestId"]] = {"url": url}
            print("CAPTURE REQ:", url[:110], flush=True)
    elif m == "Network.loadingFinished":
        rid = p.get("requestId")
        if rid in captured:
            captured[rid]["finished"] = True

call("Page.enable"); call("Runtime.enable"); call("Network.enable")

# force fresh load
call("Network.clearBrowserCache")
call("Page.navigate", {"url": URL})

# wait for page load
for _ in range(80):
    ev = json.loads(ws.recv())
    if ev.get("method") == "Page.loadEventFired":
        break
    time.sleep(0.5)

# wait for video player to be ready
time.sleep(5)

def wait_for_request(timeout):
    end = time.time() + timeout
    while time.time() < end:
        if captured:
            return True
        try:
            ev = json.loads(ws.recv())
            handle_event(ev)
        except websocket.WebSocketTimeoutException:
            pass
        time.sleep(0.5)
    return bool(captured)

print("waiting for timedtext (player auto-load)...", flush=True)
if not wait_for_request(20):
    print("not auto-loaded; enabling captions via CC button...", flush=True)
    call("Runtime.evaluate", {"expression": """
    (() => {
      const v = document.querySelector('video');
      if (v) { v.muted = true; v.play().catch(()=>{}); }
      const btn = document.querySelector('button.ytp-subtitles-button');
      if (btn) btn.click();
      return btn ? 'clicked' : 'no-btn';
    })()
    """})
    wait_for_request(20)
    if not captured:
        print("still none; trying player API...", flush=True)
        call("Runtime.evaluate", {"expression": """
        (() => {
          const pl = document.getElementById('movie_player');
          try { if (pl && pl.loadModule) pl.loadModule('captions'); } catch(e){}
          try { if (pl && pl.setOption) pl.setOption('captions','track',{}); } catch(e){}
          return 'tried';
        })()
        """})
        wait_for_request(15)

print(f"captured {len(captured)} timedtext request(s)", flush=True)

# get response body (do NOT rely on "finished" flag; redirects can change requestId)
body = None
for rid in list(captured.keys()):
    for attempt in range(5):
        try:
            r = call("Network.getResponseBody", {"requestId": rid})
            res = r.get("result", {})
            raw = res.get("body", "")
            if res.get("base64Encoded"):
                raw = base64.b64decode(raw).decode("utf-8", "ignore")
            if raw:
                body = raw
                print(f"GOT body len={len(body)}", flush=True)
                break
        except Exception as e:
            print(f"getResponseBody attempt {attempt} failed: {e}", flush=True)
            time.sleep(1)
    if body:
        break

if not body:
    print("FAILED to get timedtext body"); ws.close(); raise SystemExit(1)

# parse json3
cap = json.loads(body)
lines = []
for ev in cap.get("events", []):
    start = ev.get("tStartMs", 0)
    text = "".join(s.get("utf8", "") for s in ev.get("segs", []))
    text = text.replace("\n", " ").strip()
    if not text:
        continue
    t = timedelta(milliseconds=start)
    ts = f"{t.seconds // 60:02d}:{t.seconds % 60:02d}"
    lines.append(f"[{ts}] {text}")
transcript = "\n".join(lines)
print(f"transcript lines={len(lines)} chars={len(transcript)}", flush=True)
print(transcript[:400], flush=True)

# metadata
meta = call("Runtime.evaluate", {
    "expression": """
    (() => {
      const ytp = window.ytInitialPlayerResponse || {};
      const vd = ytp.videoDetails || {};
      const title = (document.querySelector('h1.title.style-scope.ytd-video-primary-info-renderer yt-formatted-string')?.textContent || '').trim()
                  || (document.querySelector('h1.style-scope.ytd-watch-flexy')?.textContent || '').trim()
                  || vd.title || '';
      const desc = (document.querySelector('#description-inline-expander, ytd-text-inline-expander span')?.textContent || '').slice(0, 2000).trim();
      return {title, description: desc, videoId: vd.videoId || '05vPagqJ7nM'};
    })()
    """,
    "returnByValue": True
})
mv = meta.get("result", {}).get("value", {})
print("meta:", json.dumps(mv, ensure_ascii=False), flush=True)

payload = {
    "url": URL, "video_id": VID, "title": mv.get("title", "Untitled"),
    "description": mv.get("description", ""), "chapters": [], "lang": "en", "text": transcript
}
req = urllib.request.Request(BRIDGE, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                             headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(req, timeout=15)
print("bridge:", resp.status, resp.read().decode("utf-8", "ignore"))
ws.close()
