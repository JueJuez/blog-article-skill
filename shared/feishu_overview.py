"""shared/feishu_overview.py — 监控账号容器的「总览索引」自动维护。

问题根因（用户 2026-08-25 决策）：飞书 Wiki 节点没有 sort_order 参数，
节点默认按创建时间排序；监控补历史数据时创建时间与发布时间错乱，飞书左侧导航
顺序不可信。系列课有「集数」天然有序，但非系列课（公众号文章 / B站单视频·动态）
只有发布时间，无法靠飞书自身排序。

解决（用户拍板）：每个监控账号容器（如 【监控】/公众号/哥飞）下自动维护一个
「📋 总览-<账号>」文档，作为唯一有序索引。每次落盘新文章 → 自动把条目插入该总览
（按发布时间倒序，幂等可续跑）。用户看总览文档即可，不依赖飞书左侧导航顺序。

实现要点：
- 状态持久化 notes/_feishu_overviews.json（folder -> {node_token, domain, entries}），
  使续跑/中断后仍能正确追加、去重、排序，不依赖飞书节点顺序。
- 总览文档整体 overwrite 重写（已验证 overwrite 不污染文档标题，见 _test_overwrite_title.py）。
- 条目含 title/url/publish_time；url 取飞书返回的 document.url（点击直达）。
- 历史数据（用户手动在飞书建的旧节点）用 rebuild() 扫容器重建（见 scripts/rebuild_overviews.py）。
"""
import os
import re
import json
from datetime import datetime

NOTES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "notes")
STATE_PATH = os.path.join(NOTES_DIR, "_feishu_overviews.json")

# 总览文档标题前缀（加 📋 让其在账号容器内视觉上可识别；飞书按创建时间排，但用户已知其位置）
OVERVIEW_PREFIX = "📋 总览-"


def _load() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            return json.load(open(STATE_PATH, encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save(db: dict) -> None:
    os.makedirs(NOTES_DIR, exist_ok=True)
    json.dump(db, open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def _acct_name(folder: str) -> str:
    parts = [p for p in (folder or "").split("/") if p]
    return parts[-1] if parts else "总览"


def _overview_title(folder: str) -> str:
    return f"{OVERVIEW_PREFIX}{_acct_name(folder)}"


def _domain_from_url(url: str) -> str:
    if not url:
        return ""
    m = re.search(r"https?://([^/]+)/", url)
    return m.group(1) if m else ""


# 系列课自带序号识别：第01集 / 第 12 集 等
_EPISODE_RE = re.compile(r"第\s*(\d+)\s*集")


def _sort_entries(entries: list) -> list:
    """总览排序（用户 2026-08-25 决策）：
    - 标题带「第N集」序号 → 按序号升序（系列课自带序号，第01集在最前）
    - 否则 → 按发布时间倒序（日更 / 虚拟系列如直播回放 / SEO教程：最新在前）
    两类混排时，带序号整组在前（按序号），无序号整组在后（按时间）。
    """
    def key(e):
        title = e.get("title", "") or ""
        m = _EPISODE_RE.search(title)
        ep = int(m.group(1)) if m else None
        pt = e.get("publish_time", 0) or 0
        if ep is not None:
            return (0, ep, -pt)
        return (1, 0, -pt)
    return sorted(entries, key=key)


def ensure_overview(folder: str, parent_token: str = None) -> str:
    """确保 folder 对应的总览文档存在，返回其 node_token（wiki 节点 token）。

    parent_token：folder 对应账号容器节点 token（已存在则复用，避免重建）。
    不传则回退 wiki_parent_node（旧行为）。
    """
    db = _load()
    rec = db.get(folder) or {}
    from articles.feishu import FeishuOutput
    f = FeishuOutput()
    if not f.is_available():
        return ""
    if rec.get("node_token"):
        return rec["node_token"]
    if not parent_token:
        dirs = [d for d in folder.split("/") if d]
        parent_token = f.ensure_folder_path(dirs) if dirs else f.wiki_parent_node
    if not parent_token:
        return ""
    title = _overview_title(folder)
    res = f._run_cli_command([
        "docs", "+create", "--title", title, "--content", "-",
        "--doc-format", "markdown", "--as", "user",
    ] + f._resolve_parent(parent_token), input_text="# 初始化总览\n")
    if not (res and (res.get("ok") or res.get("code") == 0)):
        return ""
    _doc = (res.get("data", {}) or {}).get("document", {}) or {}
    node_token = _doc.get("document_id") or ""
    db[folder] = {
        "node_token": node_token,
        "parent_token": parent_token,
        "domain": _domain_from_url(_doc.get("url")),
        "entries": [],
    }
    _save(db)
    return node_token


def add_entry(folder: str, parent_token: str = None, entry: dict = None) -> None:
    """把一篇文章的条目插入 folder 的总览（按发布时间倒序，幂等去重，整体重写）。

    entry: {"title": str, "url": str, "publish_time": int(epoch)}
    """
    if not folder or not entry:
        return
    db = _load()
    rec = db.get(folder) or {}
    node_token = rec.get("node_token") or ensure_overview(folder, parent_token)
    if not node_token:
        return
    entries = rec.get("entries", []) or []
    url = entry.get("url", "")
    # 去重：同 url 不重复（重跑/续跑安全）
    entries = [e for e in entries if e.get("url") != url]
    entries.append({
        "title": entry.get("title", ""),
        "url": url,
        "publish_time": int(entry.get("publish_time", 0) or 0),
    })
    # 排序：带「第N集」序号按序号升序，否则按发布时间倒序（见 _sort_entries）
    entries = _sort_entries(entries)
    rec["node_token"] = node_token
    if parent_token:
        rec["parent_token"] = parent_token
    if not rec.get("domain") and url:
        rec["domain"] = _domain_from_url(url)
    rec["entries"] = entries
    db[folder] = rec
    _save(db)
    _rewrite_overview(node_token, folder, entries, rec.get("domain", ""))


def _rewrite_overview(node_token: str, folder: str, entries: list, domain: str) -> None:
    from articles.feishu import FeishuOutput
    f = FeishuOutput()
    if not f.is_available():
        return
    md = render_overview(folder, entries)
    f._run_cli_command([
        "docs", "+update", "--command", "overwrite", "--doc", node_token,
        "--doc-format", "markdown", "--as", "user", "--content", "-",
    ], input_text=md)


def render_overview(folder: str, entries: list) -> str:
    """把 entries 渲染成按月份分组的倒序 markdown。"""
    acct = _acct_name(folder)
    lines = [
        f"# {OVERVIEW_PREFIX}{acct}",
        "",
        f"> 自动维护 · 按发布时间倒序 · 共 {len(entries)} 篇",
        "",
    ]
    # 按月份分组
    groups = {}
    for e in entries:
        pt = e.get("publish_time", 0) or 0
        if pt:
            ym = datetime.fromtimestamp(pt).strftime("%Y-%m")
            ds = datetime.fromtimestamp(pt).strftime("%Y-%m-%d")
        else:
            ym = "未注明日期"
            ds = ""
        groups.setdefault(ym, []).append((ds, e))
    for ym in sorted(groups.keys(), reverse=True):
        lines.append(f"## {ym}")
        for ds, e in groups[ym]:
            title = e.get("title", "")
            url = e.get("url", "")
            link = f"[{title}]({url})" if url else title
            suffix = f" · {ds}" if ds else ""
            lines.append(f"- {link}{suffix}")
        lines.append("")
    return "\n".join(lines)


def rebuild(folder: str, parent_token: str = None) -> int:
    """扫飞书 folder 容器下所有子节点，重建总览（历史数据兜底）。

    日期来源：优先从节点内容 frontmatter 的 published_at 读取（fetch 每个节点一次，
    一次性操作可接受）；读不到则尝试从标题解析 YYYYMMDD；都没有归「未注明日期」。
    返回重建的条目数。
    """
    from articles.feishu import FeishuOutput
    f = FeishuOutput()
    if not f.is_available():
        return 0
    if not parent_token:
        dirs = [d for d in folder.split("/") if d]
        parent_token = f.ensure_folder_path(dirs) if dirs else f.wiki_parent_node
    if not parent_token:
        return 0
    node_token = ensure_overview(folder, parent_token)
    if not node_token:
        return 0
    db = _load()
    rec = db.get(folder, {})
    domain = rec.get("domain", "")
    entries = []
    for k in f.list_children(parent_token):
        t = k.get("title", "")
        nt = k.get("node_token", "")
        obj = k.get("obj_token", "")  # docx document_id，用于 fetch 正文
        if t.startswith(OVERVIEW_PREFIX):
            continue  # 跳过总览自身
        # 跳过容器节点（【日更】/系列子文件夹等本身有子节点的）：rebuild 只索引叶子文档
        if f.list_children(nt):
            continue
        pt = _peek_publish_time(f, obj) or _parse_date_in_title(t)
        # 链接：优先 wiki 节点（可跳转），domain 缺失时退 docx 直链
        if domain:
            url = f"https://{domain}/wiki/{nt}"
        elif obj:
            url = f"https://r1t40urlzrp.feishu.cn/docx/{obj}"
        else:
            url = ""
        entries.append({"title": t, "url": url, "publish_time": pt, "node_token": nt})
    entries = _sort_entries(entries)
    rec["node_token"] = node_token
    rec["parent_token"] = parent_token
    if entries and not domain:
        # domain 未知时从首个可用 url 反推
        for e in entries:
            if e["url"]:
                m = re.search(r"https?://([^/]+)/", e["url"])
                if m:
                    rec["domain"] = m.group(1)
                    break
    rec["entries"] = [{"title": e["title"], "url": e["url"], "publish_time": e["publish_time"]} for e in entries]
    db[folder] = rec
    _save(db)
    _rewrite_overview(node_token, folder, entries, rec.get("domain", ""))
    return len(entries)


def _peek_publish_time(f: "FeishuOutput", obj_token: str) -> int:
    """读节点正文，从 YAML frontmatter 的 published_at 取发布时间（epoch 秒）。

    obj_token 为 docx document_id（list_children 返回的 obj_token）。
    """
    if not obj_token:
        return 0
    try:
        res = f._run_cli_command([
            "docs", "+fetch", "--doc", obj_token, "--doc-format", "markdown",
            "--as", "user", "--json",
        ], timeout=30)
        content = (((res or {}).get("data", {}) or {}).get("document", {}) or {}).get("content", "")
        if not content:
            return 0
        m = re.search(r"published_at\s*:\s*([0-9T:\-\+]+)", content)
        if m:
            s = m.group(1)
            try:
                from datetime import datetime as _dt
                return int(_dt.fromisoformat(s).timestamp())
            except Exception:
                return 0
    except Exception:
        pass
    return 0


def _parse_date_in_title(title: str) -> int:
    """从标题里找 YYYYMMDD / YYYY-MM-DD 解析为 epoch（无则返回 0）。"""
    m = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", title or "")
    if not m:
        return 0
    try:
        return int(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).timestamp())
    except Exception:
        return 0
