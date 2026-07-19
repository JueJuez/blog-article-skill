"""通过 CDP 驱动本机真实 Chrome（临时 profile，走系统代理 7890）抓取 YouTube 字幕。

设计要点：
- 连接本机已开启 --remote-debugging-port 的 Chrome（localhost，不走 Clash，不会被拦）。
- 在页面内执行 innertube + caption 抓取逻辑（页面网络走系统代理，能上 YouTube）。
- 不直接依赖用户默认 profile 的扩展/登录：抓公开字幕不需要登录。
"""
import json
import time
import urllib.request
from websocket import create_connection

CDP_HTTP = "http://127.0.0.1:9222"


def _http_get_json(url):
    raw = urllib.request.urlopen(url, timeout=8).read()
    return json.loads(raw.decode("utf-8", "ignore"))


def list_targets():
    return _http_get_json(f"{CDP_HTTP}/json")


def connect_page(ws_url):
    return create_connection(ws_url, timeout=30)


class CDP:
    def __init__(self, ws_url):
        self.ws = connect_page(ws_url)
        self._id = 0
        self._pending = {}

    def send(self, method, params=None, await_id=False):
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}
        self.ws.send(json.dumps(msg))
        if not await_id:
            return None
        # 等匹配 id 的响应
        while True:
            raw = self.ws.recv()
            data = json.loads(raw)
            if data.get("id") == self._id:
                return data

    def navigate(self, url):
        self.send("Page.enable")
        self.send("Page.navigate", {"url": url})
        # 等待页面加载（简单轮询 title）
        for _ in range(40):
            time.sleep(0.5)
            try:
                r = self.send("Runtime.evaluate",
                              {"expression": "document.readyState + '|' + document.title",
                               "returnByValue": True}, await_id=True)
                val = r.get("result", {}).get("result", {}).get("value", "")
                if "complete" in val and val != "complete|":
                    return val
            except Exception:
                pass
        return None

    def eval_js(self, expression, timeout_ms=20000):
        r = self.send("Runtime.evaluate",
                      {"expression": expression,
                       "returnByValue": True,
                       "awaitPromise": True,
                       "timeout": timeout_ms},
                      await_id=True)
        if "result" in r and "exceptionDetails" in r["result"]:
            raise RuntimeError("JS eval error: " + json.dumps(r["result"]["exceptionDetails"]))
        return r.get("result", {}).get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def get_page_ws():
    """返回第一个 page 类型 target 的 ws url，必要时新建一个。"""
    targets = list_targets()
    for t in targets:
        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            return t["webSocketDebuggerUrl"]
    # 没有就新建
    new = _http_get_json(f"{CDP_HTTP}/json/new?about:blank")
    return new.get("webSocketDebuggerUrl")
