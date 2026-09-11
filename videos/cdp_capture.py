"""
通过 CDP 驱动【用户本机带代理插件的 Chrome】抓取 YouTube 字幕（拦截版）。

原理：
    YouTube 的 ASR 字幕 baseUrl 现在必须带会话绑定的 pot(PoToken) 才返回内容，
    手动拼 URL 拿到的是空 200。解决办法是让 YouTube 播放器自己开字幕——
    它会用内部现生成的 pot 发出真正成功的 timedtext 请求。
    本脚本用 CDP Network 域监听并抓取那条响应体（等于在 F12 Network 抓包）。

前置：
    CDP 端点就绪即可（共享克隆 profile + 调试端口，机制见用户级技能
    cdp-automation-profile）。port=None（默认）时经 shared.cdp_session.ensure_endpoint()
    自动编排：探测复用 → 端口自愈 → 必要时冷启动；仅 clone 陈旧需复制时才关 Chrome，
    会话结束只注销持有者、最后持有者才关灯（D5/D6 契约）。

用法（CLI）：
    python videos/cdp_capture.py --url "https://www.youtube.com/watch?v=XXXX"
作为模块：
    from videos.cdp_capture import capture_transcript
    title, text = capture_transcript(url)   # text 为纯字幕文本

输出：
    .cache/yt_transcript_<vid>.txt 与 .json（含标题/作者/时长/简介/章节/字幕）
"""
import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.parse
import websocket

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)  # 直跑 CLI 时保证 shared/ 可导入（同 yt_bridge.py 惯例）

_ENDPOINT_CACHE = None  # 模块级端点缓存：多消费方同轮共享一次三段式编排


def _ensure_endpoint() -> "EnsureResult":
    """经技能 ensure_endpoint() 解析 CDP 端点（三段式编排），结果跨调用缓存。"""
    global _ENDPOINT_CACHE
    if _ENDPOINT_CACHE is None:
        from shared.cdp_session import ensure_endpoint

        _ENDPOINT_CACHE = ensure_endpoint()
    return _ENDPOINT_CACHE


def hj(url, method="GET"):
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_body(text):
    """timedtext 响应可能是 json3 或 xml(srv1/srv3)，统一转纯文本。"""
    text = (text or "").strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            data = json.loads(text)
            parts = []
            for ev in data.get("events", []):
                s = "".join(seg.get("utf8", "") for seg in ev.get("segs", []))
                s = s.replace("\n", " ").strip()
                if s:
                    parts.append(s)
            return " ".join(parts)
        except Exception:
            pass
    # xml fallback
    import re
    import html
    segs = re.findall(r"<text[^>]*>(.*?)</text>", text, re.S)
    out = []
    for s in segs:
        s = re.sub(r"<[^>]+>", "", s)
        s = html.unescape(s).replace("\n", " ").strip()
        if s:
            out.append(s)
    return " ".join(out)


def capture_transcript(url, port=None, wait=40, out=None):
    """抓取给定 YouTube 链接的字幕，返回 (title, text)。

    前提：CDP 端点已就绪；port=None 时经 _ensure_endpoint() 自动解析（结果跨调用缓存）。
    失败返回 ("", "") 。
    """
    if port is None:
        port = _ensure_endpoint().port
    base = f"http://127.0.0.1:{port}"
    vid = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("v", [""])[0]

    try:
        tab = hj(base + "/json/new?" + urllib.parse.quote(url, safe=""), method="PUT")
    except Exception as e:
        print(f"[cdp] 无法连接 {port}: {e}", flush=True)
        return ("", "")

    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=wait + 30,
                                     suppress_origin=True, max_size=None)
    _id = [0]

    def send(method, params=None):
        _id[0] += 1
        ws.send(json.dumps({"id": _id[0], "method": method, "params": params or {}}))
        return _id[0]

    def wait_id(mid):
        while True:
            r = json.loads(ws.recv())
            if r.get("id") == mid:
                return r

    def call(method, params=None):
        return wait_id(send(method, params))

    call("Page.enable")
    call("Runtime.enable")
    call("Network.enable")

    title = [""]
    meta = {}

    # 等页面就绪，拿标题/章节/描述
    for _ in range(15):
        time.sleep(1)
        r = call("Runtime.evaluate", {
            "expression": "(function(){var P=window.ytInitialPlayerResponse;if(!P||!P.videoDetails)return null;var m=(P.microformat&&P.microformat.playerMicroformatRenderer)||{};var ch=(m.chapters||[]).map(function(c){return {t:(c.title&&c.title.simpleText)||'',s:c.startTimeSeconds||0}});return JSON.stringify({title:P.videoDetails.title||'',author:P.videoDetails.author||'',len:P.videoDetails.lengthSeconds||'',desc:(P.videoDetails.shortDescription||'').slice(0,1500),chapters:ch})})()",
            "returnByValue": True,
        })
        v = r.get("result", {}).get("result", {}).get("value")
        if v:
            meta = json.loads(v)
            title[0] = meta.get("title", "")
            break

    # 触发播放器开启字幕：优先英文轨，其次第一条
    call("Runtime.evaluate", {"expression": r"""
      (function(){
        try{
          var p=document.getElementById('movie_player');
          if(!p||!p.getOption) return 'no-player';
          var list=p.getOption('captions','tracklist')||[];
          if(!list.length) return 'no-track';
          var pick=list.find(function(t){return (t.languageCode||'').indexOf('en')===0})||list[0];
          p.setOption('captions','reload',true);
          p.setOption('captions','track',pick);
          return 'ok:'+(pick.languageCode||'');
        }catch(e){return 'err:'+e}
      })()
    """, "returnByValue": True})

    # 收集 timedtext 请求
    deadline = time.time() + wait
    timed_reqs = {}       # requestId -> url
    finished = set()
    captured_text = ""

    while time.time() < deadline:
        try:
            ws.settimeout(2)
            raw = ws.recv()
        except Exception:
            # 定时再次尝试开启字幕（有时首帧未就绪）
            call("Runtime.evaluate", {"expression": "(function(){try{var p=document.getElementById('movie_player');var l=p.getOption('captions','tracklist')||[];var t=l.find(function(x){return (x.languageCode||'').indexOf('en')===0})||l[0];if(t){p.setOption('captions','track',t);}return 1}catch(e){return 0}})()", "returnByValue": True})
            continue
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        m = msg.get("method")
        if m == "Network.requestWillBeSent":
            u = msg["params"]["request"]["url"]
            if "/api/timedtext" in u:
                timed_reqs[msg["params"]["requestId"]] = u
        elif m == "Network.loadingFinished":
            rid = msg["params"]["requestId"]
            if rid in timed_reqs and rid not in finished:
                finished.add(rid)
                try:
                    body = call("Network.getResponseBody", {"requestId": rid})
                    b = body.get("result", {}).get("body", "")
                    if body.get("result", {}).get("base64Encoded"):
                        import base64
                        b = base64.b64decode(b).decode("utf-8", "ignore")
                    txt = parse_body(b)
                    if txt and len(txt) > len(captured_text):
                        captured_text = txt
                except Exception:
                    pass
        if captured_text:
            # 再多等一小会，可能有更完整的一条
            if time.time() > deadline - wait + 6:
                break

    ws.close()

    if not captured_text:
        return (title[0], "")

    # 落盘（供外层读取/调试）
    out = out or os.path.join(ROOT, ".cache", f"yt_transcript_{vid}.txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    payload = {
        "video_id": vid,
        "title": meta.get("title", ""),
        "author": meta.get("author", ""),
        "length": meta.get("len", ""),
        "desc": meta.get("desc", ""),
        "chapters": meta.get("chapters", []),
        "text": captured_text,
    }
    with open(out, "w", encoding="utf-8") as f:
        f.write(captured_text)
    with open(out.replace(".txt", ".json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return (title[0] or meta.get("title", ""), captured_text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--port", type=int, default=None,
                    help="调试端口；缺省经 ensure_endpoint 自动编排")
    ap.add_argument("--out", default=None)
    ap.add_argument("--wait", type=int, default=40, help="最长等待秒数")
    args = ap.parse_args()

    if args.port is None:
        try:
            args.port = _ensure_endpoint().port
        except Exception as e:
            print(f"[cdp] 端点编排失败: {e}", flush=True)
            sys.exit(2)

    title, text = capture_transcript(args.url, port=args.port, wait=args.wait, out=args.out)
    if not text:
        print("[cdp] 未捕获到字幕响应（可能该视频无字幕或播放器未触发）", flush=True)
        sys.exit(3)
    print(f"[cdp] 字幕已保存: {args.out or os.path.join(ROOT, '.cache', 'yt_transcript_' + urllib.parse.parse_qs(urllib.parse.urlparse(args.url).query).get('v', [''])[0] + '.txt')}", flush=True)
    print(f"[cdp] 标题: {title}", flush=True)
    print(f"[cdp] 字数: {len(text)}", flush=True)
    print(text[:400], flush=True)


if __name__ == "__main__":
    main()
