"""monitors/wechat.py — 微信公众号源（路线 B）。

直接移植 wewe-rss 的 weread 代理调用（见 trpc.service.ts）：
- 登录: GET  /api/v2/login/platform            -> {uuid, scanUrl}
        GET  /api/v2/login/platform/{uuid}      -> {vid, token, username}
- 解析: POST /api/v2/platform/wxs2mp  {url}     -> [{id,cover,name,intro}]
- 文章: GET  /api/v2/platform/mps/{mpId}/articles?page=
        -> [{id, title, picUrl, publishTime}]
请求头: Authorization: Bearer {token}, xid: {vid}
历史文章 = 翻页同一接口（page+1），直到返回 <20 条。
"""
import os
import sys
import json
import time
import subprocess
from typing import Dict, List, Optional

import requests

from .state import get_seen, mark_seen
from .ad_filter import is_ad_by_title, today_start_ts

PLATFORM_URL = os.environ.get("WEREAD_PLATFORM_URL", "https://weread.111965.xyz")
TIMEOUT = 15
DEFAULT_COUNT = 20  # wewe-rss 的 defaultCount

# 解析缓存：share_url -> {id,name,...}，避免每次运行都打 wxs2mp（省 token、抗偶发 401）
_MP_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".mp_cache.json")


def _load_mp_cache() -> Dict:
    try:
        with open(_MP_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_mp_cache(cache: Dict) -> None:
    try:
        with open(_MP_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class WereadClient:
    """封装 weread 代理（weread.111965.xyz）的接口。"""

    def __init__(self, token: str = "", vid: str = "",
                 platform_url: str = PLATFORM_URL):
        self.token = token
        self.vid = vid
        self.base = platform_url.rstrip("/")
        self.session = requests.Session()
        self.session.timeout = TIMEOUT

    def _headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {}
        if self.vid:
            h["xid"] = str(self.vid)
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    # ---- 登录（扫码） ----
    def create_login(self) -> Dict:
        r = self.session.get(f"{self.base}/api/v2/login/platform", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def poll_login(self, uuid: str, timeout: int = 120) -> Dict:
        r = self.session.get(f"{self.base}/api/v2/login/platform/{uuid}", timeout=timeout)
        r.raise_for_status()
        return r.json()

    # ---- 解析公众号 ----
    def resolve_mp(self, share_url: str) -> Dict:
        cache = _load_mp_cache()
        if share_url in cache:
            return cache[share_url]
        r = self.session.post(
            f"{self.base}/api/v2/platform/wxs2mp",
            json={"url": share_url}, headers=self._headers(), timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        info = data[0] if isinstance(data, list) else data
        if info:
            cache[share_url] = info
            _save_mp_cache(cache)
        return info

    # ---- 文章列表 ----
    def list_articles(self, mp_id: str, page: int = 1) -> List[Dict]:
        r = self.session.get(
            f"{self.base}/api/v2/platform/mps/{mp_id}/articles",
            params={"page": page}, headers=self._headers(), timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()

    def iter_articles(self, mp_id: str, max_pages: int = 50):
        for page in range(1, max_pages + 1):
            items = self.list_articles(mp_id, page)
            if not items:
                break
            yield from items
            if len(items) < DEFAULT_COUNT:
                break

    def is_token_valid(self, probe_share_url: str = "") -> bool:
        """探针：用缓存的 mp_id 或给定 share_url 打一个需鉴权的请求，401 即失效。

        其他异常（网络抖动等）保守返回 True，交由 discover 自行处理。
        """
        cache = _load_mp_cache()
        probe_mp = next((v.get("id") for v in cache.values() if v.get("id")), "")
        try:
            if probe_mp:
                self.list_articles(probe_mp, 1)
            elif probe_share_url:
                self.resolve_mp(probe_share_url)  # 需 token，401 即失效
            else:
                return True  # 无探针可用，保守放行
            return True
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                return False
            return True
        except Exception:
            return True


class WechatSource:
    """单个公众号订阅源。"""

    def __init__(self, client: WereadClient, mp_id: str = "",
                 share_url: str = "", name: str = ""):
        self.client = client
        self.share_url = (share_url or "").strip()
        self.mp_id = mp_id or ""
        self.name = name or ""

    def _resolve(self) -> str:
        if not self.mp_id and self.share_url:
            info = self.client.resolve_mp(self.share_url)
            self.mp_id = info.get("id", "")
            self.name = self.name or info.get("name", "")
        return self.mp_id

    def source_key(self) -> str:
        self._resolve()
        return f"wechat:{self.mp_id}"

    def discover(self, state: Dict, first_run_limit: int = 5, mode: str = "auto") -> List[Dict]:
        """返回新文章条目；同时把本次抓到的候选 id 全部写入 seen（增量去重）。

        mode:
          "first" -> 强制首次逻辑：取最近 first_run_limit 篇，过滤广告后不补
          "daily" -> 强制增量逻辑：仅取当天发布、最多 5 篇，过滤广告后不补
          "auto"  -> 该源无 seen 记录则按首次，否则按增量
        """
        mp_id = self._resolve()
        if not mp_id:
            raise ValueError("无法解析公众号 id（需提供 mp_id 或 share_url）")

        seen = get_seen(state, self.source_key())
        items = self.client.list_articles(mp_id, 1)  # 最新一页（最多 20 条）
        # 标题级广告过滤（无干货内容直接剔除，过滤后不补）
        items = [it for it in items if not is_ad_by_title(it.get("title", ""))]
        fetched_ids = [it["id"] for it in items]

        is_first = (mode == "first") or (mode == "auto" and not seen)
        if is_first:
            # 首次：最近 N 篇，过滤广告后不补
            new = [it for it in items if it["id"] not in seen][:first_run_limit]
        else:
            # 增量：抓最近 N 篇，去重后只处理新文。
            # 不限制「当天」——state 去重已保证不重复，且避免跨天边界文章永久遗漏。
            new = [it for it in items if it["id"] not in seen][:first_run_limit]

        mark_seen(state, self.source_key(), fetched_ids,
                  last_check=int(time.time()))

        results = []
        for it in new:
            aid = it["id"]
            results.append({
                "source": "wechat",
                "mp_name": self.name,
                "id": aid,
                "title": it.get("title", ""),
                "publish_time": it.get("publishTime", 0),
                "url": f"https://mp.weixin.qq.com/s/{aid}",
                "route": "article",  # -> articles.skill_main
            })
        return results


# ---------------------------------------------------------------------------
# 自动重新登录（token 自愈）：生成二维码 + 后台轮询续期 + 桌面通知
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
QR_PATH = os.path.join(HERE, "login_qr.png")          # 无 "." 前缀，便于预览
_LOGIN_UUID_PATH = os.path.join(HERE, ".login_uuid.json")
_RELOGIN_LOCK = os.path.join(HERE, ".relogin.lock")  # 幂等锁：5 分钟内复用同一二维码，不重复弹窗
_RELOGIN_TTL = 300  # 秒


def _save_login_uuid(uuid: str, scan_url: str) -> None:
    try:
        with open(_LOGIN_UUID_PATH, "w", encoding="utf-8") as f:
            json.dump({"uuid": uuid, "scanUrl": scan_url}, f, ensure_ascii=False)
    except Exception:
        pass


def _gen_qr(scan_url: str) -> Optional[str]:
    try:
        import segno
        segno.make(scan_url, micro=False).save(QR_PATH, scale=10, border=2)
        return QR_PATH
    except Exception as e:
        print(f"[qr-gen-fail] {e} | 直接用链接扫描: {scan_url}", file=sys.stderr)
        return None


def _start_poll_daemon() -> None:
    """后台轮询扫码结果并写入 .wechat_auth.json（扫到即自动续期）。"""
    try:
        auth_py = os.path.join(HERE, "_auth.py")
        subprocess.Popen(
            [sys.executable, auth_py, "poll"],
            cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"[poll-daemon-fail] {e}", file=sys.stderr)


def _notify_user(qr_path: str) -> None:
    """best-effort 桌面通知（Windows 本机有效）：自动用图片查看器打开二维码 + 弹窗说明。

    定时任务无人值守场景下，用户需要「被动被提醒 + 直接看到码」。本函数：
      1) 用系统默认程序打开二维码图片（Windows 图片查看器 / 浏览器）——用户直接看到码；
      2) 弹一个置顶 MessageBox 说明「token 已失效，请扫码，扫完自动恢复」。
    非 Windows 环境静默失败，不影响流水线。
    """
    try:
        if sys.platform.startswith("win"):
            # 1) 先打开二维码图片（非阻塞，图片查看器在后台弹出）
            if qr_path and os.path.exists(qr_path):
                os.startfile(qr_path)  # type: ignore[attr-defined]
            # 2) 再弹说明框（置顶，确保用户注意到）
            # 注意：PowerShell 单引号字符串不支持裸换行，故用空格分隔，避免引号未闭合报错
            msg = ("微信读书 token 已失效，公众号监控本次已跳过。 "
                   "请用微信「扫一扫」扫描刚才自动打开的二维码完成登录， "
                   "扫码成功后 token 自动续期，下次运行自动恢复公众号抓取。")
            safe = msg.replace("'", "''")
            ps = ("powershell -NoProfile -Command "
                  "Add-Type -AssemblyName System.Windows.Forms; "
                  "[System.Windows.Forms.MessageBox]::Show('" + safe + "')")
            subprocess.Popen(ps, shell=True)
    except Exception:
        pass


def trigger_relogin() -> Optional[str]:
    """token 失效时调用：生成扫码二维码 + 启动后台轮询（自动续期）。

    返回二维码路径（成功）或 None（失败）。扫码成功后 token 自动落盘，
    下次运行即可恢复公众号抓取；本次运行仍会跳过微信源。

    幂等：5 分钟内有未过期二维码则复用，不重复生成+弹窗（避免重复运行/多源触发多次弹窗）。
    """
    # 幂等保护：已有未过期二维码，直接复用并保活轮询，不重新生成
    if os.path.exists(_RELOGIN_LOCK) and os.path.exists(QR_PATH):
        age = time.time() - os.path.getmtime(_RELOGIN_LOCK)
        if age < _RELOGIN_TTL:
            print(f"[relogin] 已有未过期二维码（{int(age)}s 前），复用，不重复弹窗", file=sys.stderr)
            _start_poll_daemon()  # 确保轮询在跑（本次进程可能非上次触发者）
            return QR_PATH
    try:
        c = WereadClient()
        info = c.create_login()
        uuid = info.get("uuid") or info.get("id")
        scan_url = info.get("scanUrl") or info.get("url") or ""
        if not uuid or not scan_url:
            print("LOGIN_INIT_FAILED:", json.dumps(info, ensure_ascii=False), file=sys.stderr)
            return None
        _save_login_uuid(uuid, scan_url)
        qr = _gen_qr(scan_url)
        _start_poll_daemon()
        try:
            open(_RELOGIN_LOCK, "w").close()  # 标记：新二维码已生成，TTL 内复用
        except Exception:
            pass
        if qr:
            _notify_user(qr)  # 无人值守场景下弹图片+提示，让用户看到码
        return qr
    except Exception as e:
        print(f"[relogin-error] {e}", file=sys.stderr)
        return None
