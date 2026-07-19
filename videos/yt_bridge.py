"""
YouTube 字幕桥接服务（yt_bridge）

为什么需要它：
    Clash Party 等代理只放行浏览器扩展客户端，本环境的 Python/curl/Playwright
    全部被拒（实测 8 种路径均失败）。但用户浏览器（带扩展代理）能正常访问 YouTube。
    因此让【浏览器里的 bookmarklet】负责抓取字幕，POST 到本机服务，由本服务落盘 + 总结。

工作流程：
    1. 用户在 YouTube 页面点一下 bookmarklet
    2. bookmarklet 从页面 ytInitialPlayerResponse 提取标题/描述/章节/字幕轨道
    3. 下载选中语言的字幕 JSON（json3），拼成纯文本
    4. POST 到 http://127.0.0.1:8899/submit
    5. 本服务：保存原始数据到 .cache，生成结构化笔记存 Obsidian
    6. （可选）若配置了 AI Provider，自动生成 AI 总结；否则存"转录+结构"笔记

启动：
    python -m videos.yt_bridge            # 默认 127.0.0.1:8899
    python -m videos.yt_bridge --port 8900

依赖：仅标准库（http.server / threading）。
"""

import sys
import os
import json
import re
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# 允许从项目根导入 articles / prompts
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_PORT = 8899


def _sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    return name[:80].strip()


def save_subtitle_note(payload: dict) -> dict:
    """保存 YouTube 字幕数据并生成笔记。

    payload: {url, video_id, title, description, chapters: [{title, start}],
              lang, text, fetched_at}
    返回: {ok, note_path, chars}
    """
    title = payload.get("title") or "YouTube视频"
    url = payload.get("url") or ""
    video_id = payload.get("video_id") or ""
    lang = payload.get("lang") or "unknown"
    text = payload.get("text") or ""
    description = payload.get("description") or ""
    chapters = payload.get("chapters") or []

    if not text.strip():
        return {"ok": False, "error": "empty text"}

    # 1) 缓存原始数据
    cache_dir = os.path.join(ROOT, ".cache")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"yt_{video_id or _sanitize_filename(title)}.json")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # 2) 生成结构化笔记（无需 AI 也能出可用版本）
    date_str = __import__("datetime").datetime.now().strftime("%Y%m%d")
    fname = f"【YouTube视频】{_sanitize_filename(title)}-{date_str}.md"

    lines = []
    lines.append(f"# 【YouTube】{title}")
    lines.append("")
    lines.append(f"- 原链接：{url}")
    lines.append(f"- 字幕语言：{lang}")
    lines.append(f"- 字幕字数：{len(text)}")
    if description:
        lines.append("")
        lines.append("## 视频简介")
        lines.append("")
        lines.append(description.strip()[:2000])
    if chapters:
        lines.append("")
        lines.append("## 章节")
        lines.append("")
        for ch in chapters:
            mm = int(ch.get("start", 0)) // 60
            ss = int(ch.get("start", 0)) % 60
            lines.append(f"- `{mm:02d}:{ss:02d}` {ch.get('title', '')}")
    lines.append("")
    lines.append("## 字幕全文")
    lines.append("")
    lines.append(text.strip())
    note_md = "\n".join(lines)

    # 3) 落盘 Obsidian（复用 articles 的 OutputManager）
    saved_paths = []
    try:
        from articles.manager import OutputManager
        mgr = OutputManager()
        outs = mgr.get_available_outputs()
        for o in outs:
            try:
                p = o.get_output_path(fname)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(note_md)
                saved_paths.append(p)
            except Exception as e:
                saved_paths.append(f"[FAIL {o.name}] {e}")
    except Exception as e:
        # 退化：写到本地 notes/
        fallback = os.path.join(ROOT, "notes", fname)
        os.makedirs(os.path.dirname(fallback), exist_ok=True)
        with open(fallback, "w", encoding="utf-8") as f:
            f.write(note_md)
        saved_paths.append(fallback)

    return {
        "ok": True,
        "note_paths": saved_paths,
        "chars": len(text),
        "cache": cache_path,
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        if urlparse(self.path).path != "/submit":
            self._send(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            result = save_subtitle_note(payload)
            self._send(200 if result.get("ok") else 400, result)
        except Exception as e:
            self._send(500, {"ok": False, "error": str(e)})

    def log_message(self, *args):
        pass  # 静默


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[yt_bridge] listening on http://{args.host}:{args.port}/submit")
    print(f"[yt_bridge] bookmarklet target: http://{args.host}:{args.port}/submit")
    print(f"[yt_bridge] Ctrl+C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[yt_bridge] stopped.")


if __name__ == "__main__":
    main()
