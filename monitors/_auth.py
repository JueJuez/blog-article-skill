"""monitors/_auth.py — weread 扫码登录辅助（开发/测试用）。

用法：
  python monitors/_auth.py qr     # 创建登录、生成二维码 PNG、保存 uuid
  python monitors/_auth.py poll   # 轮询直到拿到 token，写入 .wechat_auth.json

auth 文件路径: monitors/.wechat_auth.json  -> {"token": ..., "vid": ...}
二维码路径:    monitors/.login_qr.png
"""
import os
import sys
import json
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from monitors.wechat import WereadClient  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
UUID_PATH = os.path.join(HERE, ".login_uuid.json")
AUTH_PATH = os.path.join(HERE, ".wechat_auth.json")
QR_PATH = os.path.join(HERE, ".login_qr.png")


def cmd_qr():
    c = WereadClient()
    info = c.create_login()
    uuid = info.get("uuid") or info.get("id")
    scan_url = info.get("scanUrl") or info.get("url") or ""
    if not uuid or not scan_url:
        print("LOGIN_INIT_FAILED:", json.dumps(info, ensure_ascii=False))
        return
    with open(UUID_PATH, "w", encoding="utf-8") as f:
        json.dump({"uuid": uuid, "scanUrl": scan_url}, f, ensure_ascii=False)

    # 生成二维码
    try:
        import segno
        qr = segno.make(scan_url, micro=False)
        qr.save(QR_PATH, scale=10, border=2)
        print("QR_SAVED:", QR_PATH)
    except Exception as e:
        print("QR_GEN_FAIL:", e, "| 直接用链接扫描:", scan_url)

    print("UUID:", uuid)
    print("SCAN_URL:", scan_url)
    print("请用微信扫描上方二维码完成登录。")


def cmd_poll(timeout: int = 300, interval: float = 3.0):
    if not os.path.exists(UUID_PATH):
        print("NO_UUID: 请先运行 `python monitors/_auth.py qr`")
        return
    with open(UUID_PATH, "r", encoding="utf-8") as f:
        meta = json.load(f)
    uuid = meta["uuid"]
    c = WereadClient()
    deadline = time.time() + timeout
    last_keys = None
    while time.time() < deadline:
        try:
            r = c.poll_login(uuid, timeout=int(interval) + 5)
        except Exception as e:
            print(f"[poll-error] {e}", file=sys.stderr)
            time.sleep(interval)
            continue
        last_keys = list(r.keys())
        token = r.get("token") or r.get("accessToken")
        vid = r.get("vid") or r.get("userId") or r.get("uid")
        if token:
            auth = {"token": token, "vid": vid or ""}
            with open(AUTH_PATH, "w", encoding="utf-8") as f:
                json.dump(auth, f, ensure_ascii=False, indent=2)
            print("LOGIN_SUCCESS vid=%s" % (vid or "(空)"))
            print("AUTH_SAVED:", AUTH_PATH)
            return
        # 未扫码/未确认：打印状态（不打敏感字段）
        status = r.get("status") or r.get("code") or "pending"
        print(f"[polling] status={status} keys={last_keys}", file=sys.stderr)
        time.sleep(interval)
    print("POLL_TIMEOUT last_keys=%s" % last_keys)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python monitors/_auth.py [qr|poll]")
    elif sys.argv[1] == "qr":
        cmd_qr()
    elif sys.argv[1] == "poll":
        cmd_poll()
