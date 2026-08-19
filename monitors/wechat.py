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

from .state import get_seen, mark_seen, effective_window_days
from .ad_filter import is_ad_by_title, today_start_ts
from . import backfill as bf

PLATFORM_URL = os.environ.get("WEREAD_PLATFORM_URL", "https://weread.111965.xyz")
TIMEOUT = 15
DEFAULT_COUNT = 20  # wewe-rss 的 defaultCount

# 公众号 discover 空轮重试：token 有效但代理返空列表（冷启动/懒加载未预热）
# 时的自愈次数。仅「原始列表为空」才重试；列表非空但全被 seen 过滤属正常，不重试。
WECHAT_EMPTY_RETRIES = int(os.environ.get("WECHAT_EMPTY_RETRIES", "3"))

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
    def resolve_mp(self, share_url: str, force: bool = False) -> Dict:
        cache = _load_mp_cache()
        if not force and share_url in cache:
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

    def iter_articles(self, mp_id: str, max_pages: int = 20):
        """翻页拉历史文章。新到老自然排序。

        稳定窗口（默认最近 35 天）内翻页：开头允许少量连续空页（多后端乱序代理偶发空窗），
        拿到数据后再空 3 页即停。阈值比深历史回溯时小，因为只补近月，不需要翻很深。
        """
        empty_streak = 0
        got_any = False
        for page in range(1, max_pages + 1):
            try:
                items = self.list_articles(mp_id, page)
            except requests.HTTPError as e:
                # 401 鉴权失效必须上抛：否则会被下方空页逻辑吞掉，导致 discover_all 的
                # 「全源零结果 + 401 兜底重登」永远不触发（旧 backfill 静默空转、二维码不弹的根因）。
                # 与每日监控路径（_list_with_retry 上抛 401）保持对称。
                if e.response is not None and e.response.status_code == 401:
                    raise
                items = []
                print(f"  [warn] iter_articles page {page} HTTP 异常: {str(e)[:120]}", file=sys.stderr)
            except Exception as e:
                items = []
                print(f"  [warn] iter_articles page {page} 异常: {type(e).__name__} {str(e)[:120]}", file=sys.stderr)
            if not items:
                empty_streak += 1
                if got_any and empty_streak >= 3:
                    # 已拿到过数据后连续 3 页空 → 稳定窗口内大概率到底，停止。
                    break
                if not got_any and empty_streak >= 5:
                    # 开头连续 5 页空 → 代理当前空窗，快速退出，外层会短退避重试。
                    break
                time.sleep(2)
                continue
            empty_streak = 0
            got_any = True
            yield from items

    def is_token_valid(self, probe_share_url: str = "", retries: int = 3) -> bool:
        """探针：优先用 resolve_mp（wxs2mp）打一个需鉴权的请求。

        ⚠️ 历史坑根因：旧实现探针打的是 list_articles，而 weread 代理对【过期 token】
        在该接口返回 200 空列表（不返回 401），于是探针永远「健康」→ 不弹码 →
        公众号静默全挂。resolve_mp 端点对过期 token 稳定返回 401，才是可靠探针。

        仅当「重试后仍是 401」才判失效；代理冷启动/网络抖动的瞬时 401 会被重试覆盖，
        避免误判 token 失效、导致整轮跳过公众号源。其他异常保守返回 True（放行）。
        """
        last_status = None
        # 1) 优先用 share_url 探针（force=True 绕过缓存，强制打网络，过期即 401）
        probe_share = probe_share_url
        if not probe_share:
            cache = _load_mp_cache()
            probe_share = next(iter(cache.keys()), "")  # 缓存的 share_url 也可作探针
        if probe_share:
            for attempt in range(retries):
                try:
                    self.resolve_mp(probe_share, force=True)  # 需 token，401 即失效
                    return True
                except requests.HTTPError as e:
                    if e.response is not None and e.response.status_code == 401:
                        # 401 是「确定过期」的权威信号，立即判失效，绝不被重试掩盖
                        # （旧逻辑在此退避重试，再叠加代理 500 时会被下方 except 误判为有效）。
                        return False
                    # 其他 HTTP 错误（如代理 500）：无法确认 token 有效。静默放行曾导致
                    # 「过期+代理500」时不弹码、backfill 静默失败（比误弹码更糟），故记 unknown。
                    last_status = e.response.status_code if e.response is not None else None
                    time.sleep(2 * (attempt + 1))
                    continue
                except Exception:
                    # 网络/代理异常：无法确认 token 有效。偏「失效」触发重登（可恢复），
                    # 代价仅是代理纯抖动时多弹一次码，远好于静默永久卡死。
                    last_status = None
                    time.sleep(2 * (attempt + 1))
                    continue
            # 重试耗尽仍拿不到干净 200：要么确为过期、要么代理持续异常——统一判「失效」触发重登，
            # 杜绝「过期+代理500」场景的静默失败。
            return False
        # 2) 无 share_url 探针（首跑且缓存为空）时，退化为 list_articles 探测；
        #    注意：该端点对过期 token 可能返回 200 空，故仅作 best-effort，失效判定以
        #    discover_all 的「全源零结果 + 持续 401」兜底逻辑为准。
        cache = _load_mp_cache()
        probe_mp = next((v.get("id") for v in cache.values() if v.get("id")), "")
        if not probe_mp:
            return True  # 无任何探针，保守放行（交由 discover 自行处理）
        for attempt in range(retries):
            try:
                self.list_articles(probe_mp, 1)
                return True
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 401:
                    last_status = 401
                    time.sleep(2 * (attempt + 1))
                    continue
                return True
            except Exception:
                return True
        return last_status == 401


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

    def _list_with_retry(self, mp_id: str) -> List[Dict]:
        """拉最新一页文章列表；遇「空轮」（代理冷启动/懒加载未预热）退避重试。

        仅当「原始列表为空」才重试——列表非空但内容全被 seen 过滤（无新文）属正常，
        绝不重试；401/网络异常交给外层 _discover_wechat_retry 处理。
        配了 share_url 时，空轮先 force 重解（wxs2mp 绕过缓存）预热账号再试。
        返回列表（重试耗尽仍空则返回 []，由 caller 决定如何处置）。
        """
        for attempt in range(WECHAT_EMPTY_RETRIES + 1):
            items = self.client.list_articles(mp_id, 1)
            if items:
                return items
            # 空轮：配了 share_url 则 force 重解预热账号再试一次（账户级自愈）
            if self.share_url:
                try:
                    info = self.client.resolve_mp(self.share_url, force=True)
                    self.mp_id = (info.get("id") or self.mp_id) or self.mp_id
                    items = self.client.list_articles(self.mp_id, 1)
                    if items:
                        return items
                except Exception:
                    pass
            if attempt < WECHAT_EMPTY_RETRIES:
                backoff = 2 * (attempt + 1)
                print(f"[retry-empty] wechat {self.name} 第{attempt + 1}次列表空，"
                      f"{backoff}s 后退避重试({attempt + 1}/{WECHAT_EMPTY_RETRIES})...",
                      file=sys.stderr)
                time.sleep(backoff)
        return []

    def discover(self, state: Dict, first_run_limit: int = 5, mode: str = "auto") -> List[Dict]:
        """返回新文章条目；同时把本次抓到的候选 id 全部写入 seen（增量去重）。

        mode:
          "first" -> 强制首次逻辑：取最近 first_run_limit 篇，过滤广告后不补
          "daily" -> 强制增量逻辑：仅取当天发布、最多 5 篇，过滤广告后不补
          "auto"  -> 该源无 seen 记录则按首次，否则按增量
        """
        # backfill 范围过滤：仅处理指定公众号，避免波及其他订阅源的历史回溯
        backfill_names = os.environ.get("WECHAT_BACKFILL_NAMES")
        if backfill_names and os.environ.get("WECHAT_BACKFILL") == "1":
            names = {n.strip() for n in backfill_names.split(",") if n.strip()}
            if self.name not in names:
                return []
        # 范围保护（2026-08-17 加）：WECHAT_BACKFILL=1 必须同时指定 WECHAT_BACKFILL_NAMES，
        # 否则会翻全部微信订阅源并把它们的全历史 mark_seen，污染其他源（早期试跑踩过此坑）。
        if os.environ.get("WECHAT_BACKFILL") == "1" and not backfill_names:
            raise RuntimeError(
                "WECHAT_BACKFILL=1 必须同时指定 WECHAT_BACKFILL_NAMES（逗号分隔的公众号名），"
                "否则会污染所有微信订阅源的 seen。已拒绝执行。")
        mp_id = self._resolve()
        if not mp_id:
            raise ValueError("无法解析公众号 id（需提供 mp_id 或 share_url）")

        seen = get_seen(state, self.source_key())
        # backfill 续批：已完成（触底/到 since）的号直接跳过，避免重复拉取浪费代理配额
        if os.environ.get("WECHAT_BACKFILL") == "1":
            if state.get("backfill", {}).get(self.name, {}).get("backfill_done"):
                return []
        is_first = (mode == "first") or (mode == "auto" and not seen)
        # 列表拉取：默认只拉第 1 页（每日监控语义）；WECHAT_BACKFILL=1 时翻全历史页
        # （iter_articles 自然从新到老，配合 seen 去重 + first_run_limit 实现分批回溯）
        if os.environ.get("WECHAT_BACKFILL") == "1":
            backfill_pages = int(os.environ.get("WECHAT_BACKFILL_PAGES", "20"))
            items = list(self.client.iter_articles(mp_id, max_pages=backfill_pages))
        else:
            items = self._list_with_retry(mp_id)
        if not isinstance(items, list):
            items = []
        # 标题级广告过滤（无干货内容直接剔除，过滤后不补）
        items = [it for it in items if not is_ad_by_title(it.get("title", ""))]
        # ⚠️ 注意：代理返回的 publishTime 元数据**不可信**（实测对 2026 账号返回伪造的
        # 2024-08-16 时间戳），绝不可用它做完成判定或深度推断。见下方 backfill 分支注释。
        # 时间窗口（每日监控语义）：只保留最近 N 天发布的，不深挖历史；设为 0 则关闭窗口、抓全部最新 N 篇。
        window = int(os.environ.get("WECHAT_WINDOW_DAYS", "2"))
        if os.environ.get("WECHAT_BACKFILL") == "1":
            # 历史回溯：按 since 过滤，默认滚动最近 35 天（稳定窗口）。超过此边界代理
            # 乱序/伪造时间戳严重，不再追；如需显式深挖可用 --since 指定更远日期。
            since_env = os.environ.get("WECHAT_BACKFILL_SINCE")
            since = int(since_env) if since_env else bf.default_since()
            items = [it for it in items if it.get("publishTime", 0) >= since]
        elif window > 0:
            if is_first:
                eff_window = window  # 首次/首跑：用固定窗口（配合 first_run_limit 抓最近 N 篇）
            else:
                # 自动补齐：漏跑时按「距上次成功运行天数 + 缓冲」拉长窗口，抓回中间漏掉的内容；
                # 平时每日按时跑 gap≈window 天，eff_window 依旧是 window，行为不变。封顶 WECHAT_MAX_WINDOW_DAYS。
                last_check = state["sources"].get(self.source_key(), {}).get("last_check", 0)
                max_win = float(os.environ.get("WECHAT_MAX_WINDOW_DAYS", "30"))
                eff_window = effective_window_days(window, last_check, max_win)
            cutoff = int(time.time()) - int(eff_window * 86400)
            items = [it for it in items if it.get("publishTime", 0) >= cutoff]
        fetched_ids = [it["id"] for it in items]

        # 首次/增量统一：去重后取最近 N 篇（已先经时间窗口裁剪，不会深挖历史）
        new = [it for it in items if it["id"] not in seen][:first_run_limit]

        # backfill 分支：稳定窗口（默认最近 35 天）内补最近漏抓的文章。
        # 超过此边界代理乱序/伪造时间戳严重，不再追；本模块不写 backfill_done（见 backfill.py
        # 的队列 job 完成语义）。
        if os.environ.get("WECHAT_BACKFILL") == "1":
            if not items:
                print(f"[backfill-warn] {self.name} 本批 0 条（代理空/鉴权失败？），不标记完成",
                      file=sys.stderr)

        # seen 标记范围：
        # - 非 backfill（每日监控）：标记全部 fetched，避免次日重复拉取已见候选
        # - backfill（历史回溯）：只标记本批已选中的 new，保留后续批次继续往老翻
        if os.environ.get("WECHAT_BACKFILL") == "1":
            mark_ids = [it["id"] for it in new]
        else:
            mark_ids = fetched_ids
        mark_seen(state, self.source_key(), mark_ids,
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
_HERE = os.path.dirname(os.path.abspath(__file__))
QR_PATH = os.path.join(_HERE, "login_qr.png")          # 无 "." 前缀，便于预览
_LOGIN_UUID_PATH = os.path.join(_HERE, ".login_uuid.json")
_RELOGIN_LOCK = os.path.join(_HERE, ".relogin.lock")  # 幂等锁：5 分钟内复用同一二维码，不重复弹窗
_RELOGIN_TTL = 300  # 秒
_POLL_LOG = os.path.join(_HERE, ".poll_daemon.log")   # poll daemon 日志（不再 DEVNULL）
_POLL_PID_FILE = os.path.join(_HERE, ".poll_daemon.pid")  # 防止重复启动 poll 进程


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
    """后台轮询扫码结果并写入 .wechat_auth.json（扫到即自动续期）。

    改进：输出重定向到日志文件（不再 DEVNULL），便于诊断轮询失败；
          通过 PID 文件防止重复启动多个 poll 进程。
    """
    # 检查是否已有 poll daemon 在跑
    if os.path.exists(_POLL_PID_FILE):
        try:
            with open(_POLL_PID_FILE, "r") as f:
                old_pid = int(f.read().strip())
            # 检测进程是否还活着（跨平台）
            os.kill(old_pid, 0)
            print(f"[poll-daemon] 已有 poll 进程运行中 (PID={old_pid})，不重复启动", file=sys.stderr)
            return
        except (ValueError, OSError, ProcessLookupError):
            # PID 文件存在但进程已死，清理后继续
            try:
                os.remove(_POLL_PID_FILE)
            except Exception:
                pass

    try:
        auth_py = os.path.join(_HERE, "_auth.py")
        log_fd = open(_POLL_LOG, "a", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-u", auth_py, "poll"],  # -u: unbuffered stdout/stderr
            cwd=_HERE, stdout=log_fd, stderr=subprocess.STDOUT,
        )
        # 写 PID 文件
        try:
            with open(_POLL_PID_FILE, "w") as f:
                f.write(str(proc.pid))
        except Exception:
            pass
        print(f"[poll-daemon] 已启动 poll 进程 (PID={proc.pid})，日志: {_POLL_LOG}", file=sys.stderr)
    except Exception as e:
        print(f"[poll-daemon-fail] {e}", file=sys.stderr)


def _notify_user(qr_path: str) -> None:
    """best-effort 桌面通知（Windows 本机有效）：自动用图片查看器打开二维码。

    只打开二维码图片本身——用户看到码即知道 token 过期需扫码，无需额外文字弹窗。
    非 Windows 环境静默失败，不影响流水线。
    """
    try:
        if sys.platform.startswith("win"):
            if qr_path and os.path.exists(qr_path):
                os.startfile(qr_path)  # type: ignore[attr-defined]
    except Exception:
        pass


def trigger_relogin() -> Optional[str]:
    """token 失效时调用：生成扫码二维码 + 启动后台轮询（自动续期）。

    返回二维码路径（成功）或 None（失败）。扫码成功后 token 自动落盘，
    下次运行即可恢复公众号抓取；本次运行仍会跳过微信源。

    幂等：5 分钟内有未过期二维码则复用，不重复生成+弹窗（避免重复运行/多源触发多次弹窗）。
    互斥：跨进程文件锁，防止多进程同时触发导致重复弹窗。
    """
    # 跨进程互斥锁：同一时刻只允许一个进程执行 relogin 流程
    _MUTEX_PATH = os.path.join(_HERE, ".relogin.mutex")
    mutex_fd = None
    try:
        import msvcrt
        mutex_fd = open(_MUTEX_PATH, "w")
        try:
            msvcrt.locking(mutex_fd.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            # 另一个进程已持有锁，等 0.5s 再试一次
            time.sleep(0.5)
            try:
                msvcrt.locking(mutex_fd.fileno(), msvcrt.LK_NBLCK, 1)
            except Exception:
                print("[relogin] 另一进程正在执行 relogin，本进程跳过", file=sys.stderr)
                if os.path.exists(QR_PATH):
                    return QR_PATH
                return None
    except ImportError:
        pass  # 非 Windows，跳过互斥

    try:
        # 幂等保护：已有未过期二维码，直接复用并保活轮询，不重新生成
        if os.path.exists(_RELOGIN_LOCK) and os.path.exists(QR_PATH):
            age = time.time() - os.path.getmtime(_RELOGIN_LOCK)
            if age < _RELOGIN_TTL:
                print(f"[relogin] 已有未过期二维码（{int(age)}s 前），复用，不重复弹窗", file=sys.stderr)
                _start_poll_daemon()  # 确保轮询在跑（本次进程可能非上次触发者）
                return QR_PATH
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
    finally:
        if mutex_fd is not None:
            try:
                mutex_fd.close()
            except Exception:
                pass
            try:
                os.remove(_MUTEX_PATH)
            except Exception:
                pass
