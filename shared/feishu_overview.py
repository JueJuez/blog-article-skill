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

# 日更总览放置到「作者/账号层」下（易查找），而非埋在 日更 子目录。
# 系列/其他容器保持原样。用户 2026-08-26 决策。
_OVERVIEW_DAILY = "日更"


def _overview_folder(folder: str) -> str:
    """总览归属文件夹：日更 → 上提到账号层；其余原样。

    例：【监控】/B站/价投小猪仔/日更 → 【监控】/B站/价投小猪仔
    这样 📋 总览-价投小猪仔 落在账号节点下，不在 日更 里，方便查找。
    """
    parts = [p for p in (folder or "").split("/") if p]
    if parts and parts[-1] == _OVERVIEW_DAILY:
        return "/".join(parts[:-1])
    return folder


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

# ── 坏标题判定（用户 2026-08-25：通过总览一眼看出哪些标题要改）──
# 两类：
#   1. 抓取故障占位：未命名*（某次抓取失败产生的节点，正文首#才是真标题）
#   2. 模型自创段标题：总结/要点/摘要/概览…（落盘本不该用，历史存量可能残留）
_BAD_GENERIC = {
    "总结", "总结：", "要点提炼", "要点", "摘要", "概览", "正文",
    "目录", "概述", "导读", "前言", "简介", "全文", "笔记",
}
# 公众号常见标题错误：模型把文章开头的寒暄/自我介绍当成了标题
_BAD_GREETINGS = (
    r"大家好[，,、]?\s*我是",
    r"大家好[，,]?",
    r"你好[，,]?",
    r"亲爱的[，,]?",
    r"各位朋友[，,]?",
    r"^(我是|我叫)[\u4e00-\u9fa5]",
)
_BAD_RE = re.compile(
    r"^(未命名|总结|要点|摘要|概览|正文|目录|概述|导读|前言|简介|复盘|全文|笔记|"
    + r"大家好|你好|亲爱的|各位朋友|我是|我叫)",
    re.IGNORECASE,
)


def is_bad_title(title: str) -> bool:
    """判断节点标题是否需要手动替换（占位 / 模型段标题 / 公众号寒暄首句）。"""
    t = (title or "").strip()
    if not t:
        return True
    if t.startswith("未命名"):
        return True
    if t in _BAD_GENERIC:
        return True
    if _BAD_RE.match(t):
        return True
    # 公众号典型误提：把开头寒暄/自我介绍当标题
    if any(re.search(p, t) for p in _BAD_GREETINGS):
        return True
    return False


def extract_real_title_from_body(body: str) -> str:
    """从笔记正文提取真实标题（用于给坏标题节点提供「建议标题」）。

    优先取第一个「非段标题」的 # 行（正文首行通常是模板写入的 original_title）；
    若全部是段标题，退回第一个 # 行；都没有返回空。
    """
    if not body:
        return ""
    fallback = ""
    for ln in body.splitlines():
        s = ln.strip()
        if not s.startswith("#"):
            continue
        h = s.lstrip("#").strip()
        if not h:
            continue
        if not fallback:
            fallback = h
        if not is_bad_title(h):
            return h
    return fallback


def extract_source_url(body: str) -> str:
    """从笔记正文提取原文链接（frontmatter 或 markdown 链接均可）。

    坏标题（未命名*）节点正文常只剩 source_url 元数据，无真实标题；
    此时把原文链接作为「去原文看真实标题」的入口，比瞎猜标题更有用。

    当正文里出现多个 markdown 链接时，优先取链接文本为真实标题的链接
    （而非通用占位「原文链接」），避免同名模板/元数据里残留的占位链接
    覆盖真正的来源 URL。
    """
    if not body:
        return ""
    # 1) YAML frontmatter: source_url: <url>
    m = re.search(r"source_url\s*:\s*(\S+)", body)
    if m:
        u = m.group(1).strip().strip('"').strip("'")
        if u.startswith("http"):
            return u
    # 2) markdown 链接: [text](url)
    #    若存在多个，优先非占位文本；否则回退第一个。
    _GENERIC_LINK_TEXTS = {"原文链接", "原文", "链接", "link", "source", "here", "查看原文"}
    links = re.findall(r"\[([^\]]*)\]\(\s*(https?://\S+?)\s*\)", body)
    if len(links) == 1:
        return links[0][1]
    if len(links) > 1:
        for text, url in links:
            t = text.strip()
            if t and t not in _GENERIC_LINK_TEXTS:
                return url
        return links[0][1]
    return ""


def _suggest_for_bad_title(body: str) -> str:
    """为坏标题节点生成总览里的「建议」文本（已含前缀标签）。

    改用原文链接真实标题（最可靠）：节点名可能是寒暄首句/未命名，
    正文 H1 是模型自创的总结标题，两者都不可信；唯一可靠来源是
    source_url 原文页面。
    公众号优先走 CDP 登录态抓原文页（shared/fetch_title.fetch_real_title），
    不先走不稳定的 weread 代理；B站裸 og:title；scys 登录墙返回空。
    返回空串表示无可提示（如登录墙站点取不到，调用方应展示原文链接）。
    """
    if not body:
        return ""
    from shared.fetch_title import fetch_real_title
    src = extract_source_url(body)
    if src:
        real = fetch_real_title(src)
        if real:
            return f"建议：{real}"
        return f"原文：{src}"
    return ""


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

    总览归属：经 _overview_folder 归一——日更 → 上提到账号层；
    其余（系列等）原样。总览文档创建在 ov_folder 对应的容器节点下，
    因此日更总览会出现在「账号节点」下，而非埋在 日更 子目录。
    """
    ov_folder = _overview_folder(folder)
    db = _load()
    rec = db.get(ov_folder) or {}
    from articles.feishu import FeishuOutput
    f = FeishuOutput()
    if not f.is_available():
        return ""
    if rec.get("node_token"):
        return rec["node_token"]
    # 总览放置位置由 ov_folder 决定（账号层 for 日更），不依赖传入的 parent_token
    dirs = [d for d in ov_folder.split("/") if d]
    parent_token = f.ensure_folder_path(dirs) if dirs else f.wiki_parent_node
    if not parent_token:
        return ""
    title = _overview_title(ov_folder)
    res = f._run_cli_command([
        "docs", "+create", "--title", title, "--content", "-",
        "--doc-format", "markdown", "--as", "user",
    ] + f._resolve_parent(parent_token), input_text="# 初始化总览\n")
    if not (res and (res.get("ok") or res.get("code") == 0)):
        return ""
    _doc = (res.get("data", {}) or {}).get("document", {}) or {}
    node_token = _doc.get("document_id") or ""
    db[ov_folder] = {
        "node_token": node_token,
        "parent_token": parent_token,
        "domain": _domain_from_url(_doc.get("url")),
        "entries": [],
    }
    _save(db)
    return node_token


def add_entry(folder: str, parent_token: str = None, entry: dict = None) -> None:
    """把一篇文章的条目插入总览（按发布时间倒序，幂等去重，整体重写）。

    总览归属：经 _overview_folder 归一（日更 → 账号层）。
    entry: {"title": str, "url": str, "publish_time": int(epoch), "suggested": str}
    parent_token：实际容器节点（如 日更 节点），用于 rebuild 枚举子节点；
                 与总览放置位置（账号层）解耦。
    """
    if not folder or not entry:
        return
    ov_folder = _overview_folder(folder)
    db = _load()
    rec = db.get(ov_folder) or {}
    node_token = rec.get("node_token") or ensure_overview(ov_folder)
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
        "suggested": entry.get("suggested", ""),
    })
    # 排序：带「第N集」序号按序号升序，否则按发布时间倒序（见 _sort_entries）
    entries = _sort_entries(entries)
    rec["node_token"] = node_token
    if parent_token:
        rec["parent_token"] = parent_token
    if not rec.get("domain") and url:
        rec["domain"] = _domain_from_url(url)
    rec["entries"] = entries
    db[ov_folder] = rec
    _save(db)
    _rewrite_overview(node_token, ov_folder, entries, rec.get("domain", ""))


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
    """把 entries 渲染成按月份分组的倒序 markdown。

    坏标题（未命名* / 模型段标题）→ 行首加 ⚠️，并附「建议标题」（从正文首#提取），
    方便人工直接复制替换（用户 2026-08-25：通过总览一眼看出哪些标题要改）。
    """
    acct = _acct_name(folder)
    bad_count = sum(1 for e in entries if is_bad_title(e.get("title", "")))
    lines = [
        f"# {OVERVIEW_PREFIX}{acct}",
        "",
        f"> 自动维护 · 按发布时间倒序 · 共 {len(entries)} 篇",
    ]
    if bad_count:
        lines.append(f"> ⚠️ 其中 {bad_count} 篇标题疑似需替换（见下方 ⚠️ 标记，括号内为建议标题）")
    lines.append("")
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
            sugg = e.get("suggested", "")
            # ⚠️ 标记：有建议且建议标题与当前标题不同（坏标题 / 标题不符原文）
            flag = "⚠️ " if (sugg and sugg != title) else ""
            sugg_txt = f" → {sugg}" if flag else ""
            lines.append(f"- {flag}{link}{suffix}{sugg_txt}")
        lines.append("")
    return "\n".join(lines)


def rebuild(folder: str, parent_token: str = None, skip_suggest: bool = False) -> int:
    """扫飞书 folder 容器下所有子节点，重建总览（历史数据兜底）。

    总览归属：经 _overview_folder 归一（日更 → 账号层）。即总览文档创建在
    账号节点下，但条目仍枚举自 folder 实际容器（如 日更 节点）。
    日期来源：优先从节点内容 frontmatter 的 published_at 读取；读不到则标题解析
    YYYYMMDD；都没有归「未注明日期」。
    skip_suggest：跳过「建议标题」的网络抓取（仅搬位置/刷新条目时用，省 ~数百次请求）。
    返回重建的条目数。
    """
    ov_folder = _overview_folder(folder)
    from articles.feishu import FeishuOutput
    f = FeishuOutput()
    if not f.is_available():
        return 0
    if not parent_token:
        dirs = [d for d in folder.split("/") if d]
        parent_token = f.ensure_folder_path(dirs) if dirs else f.wiki_parent_node
    if not parent_token:
        return 0
    node_token = ensure_overview(ov_folder)  # 账号层总览，不传 parent_token
    if not node_token:
        return 0
    db = _load()
    rec = db.get(ov_folder, {})
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
        body = _fetch_body(f, obj)
        pt = _parse_published_at(body) or _parse_date_in_title(t)
        # 链接：优先 wiki 节点（可跳转），domain 缺失时退 docx 直链
        if domain:
            url = f"https://{domain}/wiki/{nt}"
        elif obj:
            url = f"https://r1t40urlzrp.feishu.cn/docx/{obj}"
        else:
            url = ""
        # 建议标题：坏标题（未命名/段标题/寒暄）→ 用 og:title；
        # 非坏标题但「当前标题≠原文 og:title」→ 同样给出建议（用户 2026-08-26：全都要用原文标题）。
        suggested = ""
        if not skip_suggest:
            src = extract_source_url(body)
            og = ""
            if src:
                from shared.fetch_title import fetch_og_title, titles_differ
                og = fetch_og_title(src)
            if is_bad_title(t):
                if og:
                    suggested = f"建议：{og}"
                else:
                    suggested = _suggest_for_bad_title(body)  # 退化为原文链接入口
            elif og and titles_differ(t, og):
                suggested = f"建议：{og}"
        entries.append({"title": t, "url": url, "publish_time": pt,
                        "node_token": nt, "suggested": suggested})
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
    rec["entries"] = [{"title": e["title"], "url": e["url"],
                        "publish_time": e["publish_time"],
                        "suggested": e.get("suggested", "")} for e in entries]
    db[ov_folder] = rec
    # 清理：旧代码把总览建在 日更 节点下，迁移后其在 日更 容器里成为孤儿，删掉避免重复
    if ov_folder != folder:
        db.pop(folder, None)  # 清掉旧的 state 键
        for k in f.list_children(parent_token):
            if k.get("title", "").startswith(OVERVIEW_PREFIX) and k.get("node_token") != node_token:
                f.delete_node(k.get("node_token"))
    _save(db)
    _rewrite_overview(node_token, ov_folder, entries, rec.get("domain", ""))
    return len(entries)


def _fetch_body(f: "FeishuOutput", obj_token: str) -> str:
    """读节点正文（markdown），返回内容字符串；失败返回空。

    obj_token 为 docx document_id（list_children 返回的 obj_token）。
    """
    if not obj_token:
        return ""
    try:
        res = f._run_cli_command([
            "docs", "+fetch", "--doc", obj_token, "--doc-format", "markdown",
            "--as", "user", "--json",
        ], timeout=30)
        return (((res or {}).get("data", {}) or {}).get("document", {}) or {}).get("content", "") or ""
    except Exception:
        return ""


def _parse_published_at(body: str) -> int:
    """从正文 frontmatter 的 published_at 取发布时间（epoch 秒）；无则 0。"""
    if not body:
        return 0
    m = re.search(r"published_at\s*:\s*([0-9T:\-\+]+)", body)
    if m:
        s = m.group(1)
        try:
            from datetime import datetime as _dt
            return int(_dt.fromisoformat(s).timestamp())
        except Exception:
            return 0
    return 0


def _peek_publish_time(f: "FeishuOutput", obj_token: str) -> int:
    """兼容旧调用：读正文并解析 published_at。"""
    return _parse_published_at(_fetch_body(f, obj_token))


def _parse_date_in_title(title: str) -> int:
    """从标题里找 YYYYMMDD / YYYY-MM-DD 解析为 epoch（无则返回 0）。"""
    m = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", title or "")
    if not m:
        return 0
    try:
        return int(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).timestamp())
    except Exception:
        return 0
