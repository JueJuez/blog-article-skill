import urllib.request, json, time, websocket, re

VID = "05vPagqJ7nM"
URL = f"https://www.youtube.com/watch?v={VID}"

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
pages = json.loads(op.open("http://127.0.0.1:9222/json/list", timeout=5).read())
ws_url = [p["webSocketDebuggerUrl"] for p in pages if "webSocketDebuggerUrl" in p][0]
ws = websocket.create_connection(ws_url, timeout=60)

_msg_id = 0
captured = {}  # requestId -> {url, mime, hasBody}

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
    m = r.get("method")
    p = r.get("params", {})
    if m == "Network.requestWillBeSent":
        req = p.get("request", {})
        url = req.get("url", "")
        if "timedtext" in url or "get_transcript" in url or "transcript" in url:
            captured[p["requestId"]] = {"url": url, "mime": None, "finished": False}
            print("CAPTURE REQUEST:", url[:120], flush=True)
    elif m == "Network.responseReceived":
        rid = p.get("requestId")
        if rid in captured:
            resp = p.get("response", {})
            captured[rid]["mime"] = resp.get("mimeType", "?")
            captured[rid]["status"] = resp.get("status", "?")
            print(f"CAPTURE RESPONSE: {rid[:20]} status={resp.get('status')} mime={resp.get('mimeType')}", flush=True)
    elif m == "Network.loadingFinished":
        rid = p.get("requestId")
        if rid in captured:
            captured[rid]["finished"] = True
            captured[rid]["encodedLen"] = p.get("encodedDataLength", 0)
            print(f"CAPTURE FINISHED: {rid[:20]}", flush=True)

# enable domains
call("Page.enable")
call("Runtime.enable")
call("Network.enable")

# navigate
call("Page.navigate", {"url": URL})

# wait for load + extra time for player to load captions
deadline = time.time() + 30
while time.time() < deadline:
    ev = json.loads(ws.recv())
    handle_event(ev)
    if ev.get("method") == "Page.loadEventFired":
        break

print(f"Captured {len(captured)} candidate requests. Waiting for player to init...", flush=True)
time.sleep(8)

# process events until queue drained or timeout
for _ in range(200):
    try:
        ws.settimeout(1)
        ev = json.loads(ws.recv())
        handle_event(ev)
    except websocket.WebSocketTimeoutException:
        break

print(f"After wait: {len(captured)} candidate requests.", flush=True)

# get response bodies for any finished requests
for rid, info in captured.items():
    if not info.get("finished"):
        continue
    try:
        r = call("Network.getResponseBody", {"requestId": rid})
        body = r.get("result", {}).get("body", "")
        print(f"\n=== BODY for {rid[:20]} ===")
        print(f"URL: {info['url'][:100]}")
        print(f"Status: {info.get('status')} | Mime: {info.get('mime')} | Body len: {len(body)}")
        print(body[:500])
        if len(body) > 1000:
            print("...")
    except Exception as e:
        print(f"Failed to get body for {rid}: {e}")

ws.close()
