"""一次性：从 scys tags 页捕获指定标签的 menuId。
方法（参照 scys-fetch-sop §7）：点标签 -> 拦截 searchTopic 请求的 menuId 参数。
"""
import sys, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from shared.cdp_session import SharedCdpSession

TARGETS = ["AI自媒体", "虚拟产品", "垂直小号"]
TAGS_URL = "https://scys.com/tags"


def main():
    sess = SharedCdpSession()
    sess.__enter__()
    try:
        page = sess.new_page()
        captured = []

        def on_request(req):
            u = req.url
            if "searchTopic" in u:
                body = None
                try:
                    body = req.post_data
                except Exception:
                    body = None
                captured.append({"url": u, "post": body})

        page.on("request", on_request)
        page.goto(TAGS_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(6000)

        # 先打印页面上所有可见的标签 chip 文本，便于定位
        chips = page.evaluate(
            """() => {
                const txt = [];
                document.querySelectorAll('*').forEach(el => {
                    const t = (el.innerText||'').trim();
                    if (t && t.length <= 12 && el.children.length === 0) {
                        // 可能是标签名
                        if (/自媒体|虚拟|垂直|AI|产品|小号|出海|小程序|电商|IP|编程/.test(t)) txt.push(t);
                    }
                });
                return Array.from(new Set(txt));
            }"""
        )
        print("[page chips sample]:", chips[:60])

        result = {}
        for tag in TARGETS:
            captured.clear()
            # 定位包含该文本的可见元素并点击
            try:
                loc = page.locator(f"text={tag}").first
                loc.click(timeout=8000)
            except Exception as e:
                print(f"  [click fail] {tag}: {e}")
                continue
            page.wait_for_timeout(4000)
            # 从 captured 里找 menuId（可能在 URL 查询或 POST 体）
            mids = []
            for c in captured:
                blob = (c.get("url") or "") + " " + (c.get("post") or "")
                for m in re.finditer(r"mustMenuIdList\"?\s*:?\s*\[(\d+)\]", blob):
                    mids.append(m.group(1))
                # 也试 projectId / id 字段
                for m in re.finditer(r"[?&]id=(\d+)", c.get("url") or ""):
                    mids.append(m.group(1))
            if mids:
                result[tag] = mids[0]
                print(f"  [OK] {tag}: menuId={mids[0]}  (candidates: {mids[:5]})")
            else:
                print(f"  [no menuId] {tag}: captured {len(captured)} reqs")
                for c in captured[:2]:
                    print(f"      url={c['url']}")
                    print(f"      post={c['post']}")
        print("=== RESULT ===")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        try:
            page.close()
        except Exception:
            pass
        sess.__exit__(None, None, None)


if __name__ == "__main__":
    main()
