"""shared/series_manifest.py — 系列课「端到端状态机」（优化 A，续跑唯一真相源）。

问题根因：此前本地 raw/body 文件被删除后，就失去了「哪些集已落盘」的唯一信号，
会话中断/任务被杀后只能手动对账飞书 vs 磁盘（本次 5/15/18 与 29-38 漏跑即此坑）。
本模块用显式状态机取代「文件存在即续跑信号」，让 drain 在任意中断点都能无歧义续跑。

**缺口可见化（防"误报完成"）**：manifest 记录 `expected_total`（系列总集数，由发现/救回时填入）。
`gap_pages()` 返回「期望集数内、尚未 raw_ready+」的缺口（缺失或仍 pending），让"漏了哪几集"
一眼可见，绝不会因"字典里没有 key"就静默当成"都做完了"。详见 docs/RUNBOOK-series-rescue.md。

状态流转（强制单向，不允许倒退到更早态）：
  pending → raw_ready → summarized → landed → verified
    - raw_ready  : 字幕/ASR 稿已落盘（raw 在）
    - summarized : Agent 已产出笔记正文（body 在，待落盘）
    - landed     : 已调 _save_series_note 写飞书
    - verified   : 飞书节点回读确认存在（此时才可安全删除本地 body，见优化 B）

manifest 文件位置：notes/<系列名>/_manifest.json（运行时状态，随 notes/ 被 gitignore）。
与 monitors/series_state.json 分工：series_state 负责「每日监控增量去重（别重复抓）」，
本 manifest 负责「处理流水线续跑（别重复落/别漏落）」——两件不同性质的事，各管各的。
"""
import os
import json
import re
from datetime import datetime

# 状态常量
PENDING = "pending"
RAW_READY = "raw_ready"
SUMMARIZED = "summarized"
LANDED = "landed"
VERIFIED = "verified"
_STATE_ORDER = [PENDING, RAW_READY, SUMMARIZED, LANDED, VERIFIED]

# 允许被「重新落盘」的状态（即还没确认落定）
_RELANDABLE = {PENDING, RAW_READY, SUMMARIZED, LANDED}


def _manifest_path(series_title: str, notes_dir: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|\n\r\t]', '_', series_title).strip()[:80]
    return os.path.join(notes_dir, safe, "_manifest.json")


class SeriesManifest:
    def __init__(self, series_title: str, url: str = "", author: str = "",
                 notes_dir: str = ""):
        from articles import main as articles_main
        self.notes_dir = notes_dir or articles_main.NOTES_DIR
        self.series_title = series_title
        self.url = url
        self.author = author
        self.episodes = {}  # page(str) -> dict
        self.expected_total = 0  # 系列总集数（发现/救回时填入）；0 表示未知→不做缺口检测
        self.path = _manifest_path(series_title, self.notes_dir)
        self.series_dir = os.path.join(self.notes_dir,
                                       re.sub(r'[\\/:*?"<>|\n\r\t]', '_', series_title).strip()[:80])

    # ---- 持久化 ----
    def load(self) -> "SeriesManifest":
        if os.path.exists(self.path):
            try:
                d = json.load(open(self.path, encoding="utf-8"))
                self.url = d.get("url", self.url)
                self.author = d.get("author", self.author)
                self.expected_total = d.get("expected_total", 0) or 0
                self.episodes = d.get("episodes", {}) or {}
            except Exception:
                pass
        return self

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = {
            "series_title": self.series_title,
            "url": self.url,
            "author": self.author,
            "expected_total": self.expected_total,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "episodes": self.episodes,
        }
        json.dump(payload, open(self.path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)

    # ---- 单集操作 ----
    def upsert(self, page: int, part: str = "", bvid: str = "", url: str = "",
               state: str = None, **meta) -> None:
        key = f"{page:02d}"
        ep = self.episodes.setdefault(key, {"page": page, "part": part,
                                            "bvid": bvid, "url": url, "state": PENDING})
        if part:
            ep["part"] = part
        if bvid:
            ep["bvid"] = bvid
        if url:
            ep["url"] = url
        ep.update(meta)
        if state:
            self.set_state(page, state)

    def get(self, page: int) -> dict:
        return self.episodes.get(f"{page:02d}", {})

    def set_state(self, page: int, state: str) -> None:
        key = f"{page:02d}"
        ep = self.episodes.setdefault(key, {"page": page, "state": PENDING})
        old = ep.get("state", PENDING)
        # 单向：不允许倒退（verified 不被 landed/summarized 覆盖）
        if _STATE_ORDER.index(state) >= _STATE_ORDER.index(old):
            ep["state"] = state
        ep["state_updated"] = datetime.now().isoformat(timespec="seconds")

    def state(self, page: int) -> str:
        return self.get(page).get("state", PENDING)

    # ---- 期望集数 / 缺口（防"误报完成"）----
    def set_expected_total(self, n: int) -> None:
        """记录系列总集数（发现/救回时填入）。仅当更大时更新，避免被小值覆盖。"""
        if isinstance(n, int) and n > self.expected_total:
            self.expected_total = n

    def gap_pages(self) -> list:
        """期望集数内、尚未进入 raw_ready+ 的缺口（缺失或仍 pending）。

        返回 page(int) 列表。expected_total=0（未知）时返回空（不做缺口检测）。
        这是"漏了哪几集"的权威来源——任何续跑/汇报前先查它，杜绝静默误报完成。
        """
        if not self.expected_total:
            return []
        gaps = []
        for p in range(1, self.expected_total + 1):
            ep = self.episodes.get(f"{p:02d}")
            if not ep or ep.get("state", PENDING) in (PENDING,):
                gaps.append(p)
        return gaps

    # ---- 查询 ----
    def pages_in(self, *states) -> list:
        return sorted(int(k) for k, v in self.episodes.items()
                      if v.get("state") in states)

    def to_land(self) -> list:
        """待落盘的集（已总结、尚未 verified）。"""
        return self.pages_in(SUMMARIZED, LANDED)

    def verified_pages(self) -> list:
        return self.pages_in(VERIFIED)

    # ---- 磁盘对账（自愈）----
    def reconcile_disk(self) -> None:
        """扫磁盘 raw/body，把状态推进到「磁盘能证明的最高态」，但不倒退 verified。"""
        from shared import series_naming as sn
        if not os.path.isdir(self.series_dir):
            return
        for f in os.listdir(self.series_dir):
            page, part = sn.parse_raw_name(f)
            if page is None:
                page, part = sn.parse_body_name(f)
            if page is None:
                continue
            absf = os.path.join(self.series_dir, f)
            if f.endswith("_raw.md"):
                self.upsert(page, part=part, raw=os.path.relpath(absf, self.notes_dir))
                if self.state(page) in (PENDING,):
                    self.set_state(page, RAW_READY)
            elif f.endswith(".body.md"):
                self.upsert(page, part=part, body=os.path.relpath(absf, self.notes_dir))
                # body 存在 ⇒ 至少 summarized（除非已 landed/verified）
                if self.state(page) in (PENDING, RAW_READY):
                    self.set_state(page, SUMMARIZED)

    # ---- 飞书对账（自愈，确认已落定）----
    def reconcile_feishu(self, parent_token: str = None) -> None:
        """读飞书系列容器，凡是已存在节点的集直接标 verified（防止重复落盘）。

        parent_token：系列容器挂哪个父节点（监控系列传 UP 节点 token，
        否则默认根）。必须与 save 路径一致（都用 folder 推导的父节点），
        否则对账会在根容器找、永远对不上，导致重复落盘 / 误判未落盘。
        """
        try:
            from articles.feishu import FeishuOutput
            f = FeishuOutput()
            if not f.is_available():
                return
            ctok = f.ensure_series_node(self.series_title, parent_token=parent_token)
            if not ctok:
                return
            nodes = f.list_children(ctok)
            for n in nodes:
                t = n.get("title", "")
                m = re.match(r"^第(\d{2})集_", t)
                if not m:
                    continue
                page = int(m.group(1))
                self.upsert(page, node=n.get("node_token", ""))
                # 飞书已有节点 ⇒ 视为已 verified（落盘成功且回读存在）
                self.set_state(page, VERIFIED)
        except Exception as e:
            print(f"   ⚠️ 飞书对账失败（非致命，下次续跑再试）：{e}")

    def summary_line(self) -> str:
        counts = {s: len(self.pages_in(s)) for s in _STATE_ORDER}
        line = (f"manifest[{self.series_title}] "
                f"raw_ready={counts[RAW_READY]} summarized={counts[SUMMARIZED]} "
                f"landed={counts[LANDED]} verified={counts[VERIFIED]} "
                f"(共 {len(self.episodes)} 集")
        if self.expected_total:
            line += f" / 期望 {self.expected_total}"
        line += ")"
        gaps = self.gap_pages()
        if gaps:
            line += f"  ⚠️ 缺口(待救回)={gaps}"
        return line


def load_or_init(series_title: str, url: str = "", author: str = "",
                 notes_dir: str = "", reconcile: bool = True,
                 expected_total: int = 0, parent_token: str = None) -> SeriesManifest:
    """加载已有 manifest；若不存在则新建。reconcile=True 时做磁盘+飞书对账自愈。

    expected_total>0 时同步记录系列总集数（用于缺口检测，防误报完成）。
    parent_token：飞书系列容器父节点（监控系列传 UP 节点 token，与 save 一致）。
    """
    m = SeriesManifest(series_title, url=url, author=author, notes_dir=notes_dir).load()
    if expected_total:
        m.set_expected_total(expected_total)
    if reconcile:
        m.reconcile_disk()
        m.reconcile_feishu(parent_token=parent_token)
        m.save()
    return m
