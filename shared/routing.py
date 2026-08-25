"""shared/routing.py — 统一内容归档路由器（单一真相源）。

所有落盘入口（监控 drain / 手贴文章 / 手贴视频 / 系列课 / scys）都通过 resolve_folder()
决定归档路径，消除「手贴路径不看作者、同一作者内容散落多处」的根因（用户 2026-08-23 决策）。

归档结构（详见项目 MEMORY 2026-08-23 / 2026-08-25）：
  【监控】/B站/<账号名>/日更/           监控 B站 UP 的非系列视频+动态（默认归档层，纯「日更」无括号，对齐飞书存量节点）
  【监控】/公众号/<账号名>/日更/        监控公众号的非系列文章
  【监控】/生财有术/<领域>/             scys（生财有术）按领域分子节点
  【监控】/B站/<账号名>/<系列名>/       系列课（标题命中 series_patterns 或官方系列）：归属监控账号时挂其下
  【监控】/公众号/<账号名>/<系列名>/    系列课（如 哥飞SEO教程 / 直播回放）：同上
  【我的总结】/系列课/<系列名>/         独立系列（无所属账号，手贴归我的总结；飞书内该节点由用户自行迁移整理）
  【我的总结】/作者/<名>/               手贴：识别出非监控常驻作者（含其系列课 作者/<名>/<系列>）
  【我的总结】/<分类>/                  手贴：无作者、有分类的散文（与 作者/ 同级兄弟）
  【待归类】/                           兜底收件箱（作者未知且无分类）

节点注册表 node_registry(state.json) 记录 作者→节点路径，路由优先复用已存在节点，
避免「先手贴后订阅」导致同作者内容分裂两处。
"""
import os
import re
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MONITOR_ROOT = "【监控】"
MYNOTES_ROOT = "【我的总结】"
INBOX = "【待归类】"
# 非系列课（单视频 / 动态 / 公众号文章）统一归档到账号下的「日更」节点；
# 用户 2026-08-25 决策：飞书 Wiki 节点无 sort_order，靠「日更」节点下的总览文档排序，
# 不依赖飞书左侧导航顺序。
# 注：飞书里既有节点命名为纯「日更」（无【】括号，多账号共用、含存量内容），
# 为不新建重复节点、复用存量，代码常量跟随既命名为 "日更"（不加括号）。
DAILY = "日更"

# 平台 → 文件夹名（平台比分类稳定：一个 UP 可能跨多个主题，且最贴「按账号名找」心智）
_PLATFORM_FOLDER = {"bilibili": "B站", "wechat": "公众号", "scys": "生财有术"}
_SCYS_AUTHOR = "生财有术"


def _subs_path() -> str:
    return os.path.join(BASE_DIR, "monitors", "subscriptions.json")


def _state_path() -> str:
    return os.path.join(BASE_DIR, "monitors", "state.json")


def load_account_registry() -> dict:
    """从 subscriptions.json 派生 作者→{platform, category, aliases, monitored}。

    账号名 = 注册表主键（用户明确要「以 UP主/公众号名建子节点」）。
    aliases 用于公众号作者字段漂移（如「中金点睛」文章常标「中金研究/中金公司」）。
    """
    reg: dict = {}
    try:
        with open(_subs_path(), encoding="utf-8") as f:
            subs = json.load(f)
    except Exception:
        subs = {}
    for w in subs.get("wechat", []) or []:
        n = (w.get("name") or "").strip()
        if n:
            reg[n] = {"platform": "wechat", "category": w.get("category", "") or "",
                      "aliases": w.get("aliases", []) or [], "monitored": True}
    for b in subs.get("bilibili", []) or []:
        n = (b.get("name") or "").strip()
        if n:
            reg[n] = {"platform": "bilibili", "category": b.get("category", "") or "",
                      "aliases": b.get("aliases", []) or [], "monitored": True}
    # scys：作者固定为「生财有术」，平台维度单列（非公众号、非 B站）
    if subs.get("scys"):
        reg[_SCYS_AUTHOR] = {"platform": "scys", "category": "副业增长",
                             "aliases": [], "monitored": True}
    return reg


def load_series_patterns() -> dict:
    """从 subscriptions.json 派生 账号名 → series_patterns 列表。

    series_patterns: [{pattern: str（标题子串，命中即归入该系列）,
                       series: str（系列容器名）}, ...]
    仅 wechat / bilibili 段支持。用户 2026-08-25 决策：手动在 subscriptions.json
    给某账号加 series_patterns 即「自动把命中标题的内容归到该系列课」，
    未配置的账号一律走【日更】。
    """
    out: dict = {}
    try:
        with open(_subs_path(), encoding="utf-8") as f:
            subs = json.load(f)
    except Exception:
        return out
    for sec in ("wechat", "bilibili"):
        for w in subs.get(sec, []) or []:
            n = (w.get("name") or "").strip()
            pats = w.get("series_patterns") or []
            if n and pats:
                out[n] = pats
    return out


def match_series(account: str, title: str, body: str = "") -> str:
    """按账号的 series_patterns 匹配标题/正文，返回系列名或 ''。

    account 可为展示名 / 别名 / 规范名（内部先经 _match_account 归一化）；
    title 为内容标题，body 为正文（可选，用于标题未标注系列但正文可识别的情形，
    如 哥飞 SEO 文章标题泛化、正文讲 SEO/外链）。命中第一个 pattern 即返回其 series。
    确定性、零花费、可续跑。默认只看 title（用户指定「标题模式」）；传入 body 时追加
    正文匹配，用于存量内容补归档（见 scripts/promote_existing.py --body）。
    """
    if not account:
        return ""
    reg = load_account_registry()
    acct = _match_account(reg, account) or account
    pats = load_series_patterns().get(acct) or load_series_patterns().get(account) or []
    hay = f"{title or ''}\n{body or ''}"
    for p in pats:
        pat = (p.get("pattern") or "").strip()
        if pat and pat in hay:
            return (p.get("series") or "").strip()
    return ""


def _norm_name(s: str) -> str:
    """账号名归一化：去空格/下划线 + 小写，用于跨「展示名 vs 规范名」的模糊对齐。

    例：bilibili 展示名「Mark Huang」(带空格) vs 订阅规范名「Mark__Huang」(带下划线)
    归一后都为「markhuang」，避免活路径上系列匹配/路由落错节点。
    """
    return re.sub(r"[\s_]+", "", (s or "")).lower()


def _match_account(reg: dict, author: str):
    """账号命中（含别名 / 包含匹配 / 空格下划线归一化）。返回**规范账号名**或 None。

    关键：调用方必须用返回的规范名拼路径，不能用原始 author——否则别名命中后
    会落成「中金研究」而非规范「中金点睛」（2026-08-23 干跑发现的根因）。
    """
    if not author:
        return None
    if author in reg:
        return author
    na = _norm_name(author)
    if na:
        for name, info in reg.items():
            if _norm_name(name) == na:
                return name
            if author in (info.get("aliases") or []):
                return name
    # 宽松包含：作者字段可能是「中金点睛：XXX」这类带前缀
    for name, info in reg.items():
        if name and name in (author or ""):
            return name
    return None


def _infer_platform(item: dict) -> str:
    url = item.get("url", "") or ""
    route = item.get("route", "") or ""
    source = item.get("source", "") or ""
    if "scys.com" in url or source == "monitor_scys" or item.get("scys_domain"):
        return "scys"
    if "mp.weixin" in url or route in ("article", "cv") or source == "monitor_wechat":
        return "wechat"
    if "bilibili" in url or route in ("video", "dynamic") or "bilibili" in source:
        return "bilibili"
    return ""


def load_node_registry(state: dict) -> dict:
    return (state or {}).get("node_registry", {}) or {}


def remember_node(state: dict, author: str, path: str) -> None:
    """持久化 作者→节点路径（防「先手贴后订阅」分裂）。state 为 monitors/state.json 字典。"""
    if not state or not author:
        return
    nr = load_node_registry(state)
    if nr.get(author) != path:
        nr[author] = path
        state["node_registry"] = nr
        try:
            with open(_state_path(), "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def resolve_folder(item: dict, state: dict = None) -> str:
    """返回归档路径（纯计算，零 IO/落盘）。

    输入 item 字段：author/mp_name/sub_name、url、source、route、category、series、
    scys_domain/project/domain。
    """
    author = (item.get("author") or item.get("mp_name") or item.get("sub_name") or "").strip()

    # 1) scys 特例：作者固定生财有术，按领域分子节点
    if item.get("scys_domain") or "scys.com" in (item.get("url", "") or ""):
        domain = item.get("scys_domain") or item.get("project") or item.get("domain") or "未分类"
        return f"{MONITOR_ROOT}/生财有术/{domain}"

    # 2) 系列课：标题命中账号 series_patterns 或显式传 series 都走这里。
    #    - 归属监控账号 → 【监控】/<平台>/<账号>/<系列>
    #    - 归属非监控作者（订阅名单外的作者字段）→ 【我的总结】/作者/<作者>/<系列>
    #    - 无作者归属的独立系列 → 【我的总结】/系列课/<系列>（手贴独立系列，归我的总结；飞书内该节点由用户自行迁移整理）
    #    系列排序：自带「第N集」序号的按序号排（见 shared/feishu_overview），
    #    无序号（如直播回放/SEO教程）按发布时间排。
    series = item.get("series") or match_series(author, item.get("title", ""))
    if series:
        acct_name = _match_account(load_account_registry(), author)
        if acct_name:
            acct = load_account_registry().get(acct_name, {})
            if acct.get("monitored"):
                pf = _PLATFORM_FOLDER.get(acct["platform"], acct["platform"])
                return f"{MONITOR_ROOT}/{pf}/{acct_name}/{series}"
            return f"{MYNOTES_ROOT}/作者/{acct_name}/{series}"
        if author:
            return f"{MYNOTES_ROOT}/作者/{author}/{series}"
        return f"{MYNOTES_ROOT}/系列课/{series}"

    # 3) 节点注册表复用（优先：已存在节点一律复用，防分裂）
    nr = load_node_registry(state)
    if author and author in nr:
        return nr[author]

    # 4) 监控账号命中（含别名）→ 【监控】/<平台>/<规范账号名>/【日更】
    #    非系列课（单视频/动态/公众号文章）统一落【日更】，由该节点下的总览文档排序。
    acct_name = _match_account(load_account_registry(), author)
    if acct_name:
        acct = load_account_registry().get(acct_name, {})
        pf = _PLATFORM_FOLDER.get(acct.get("platform", ""), acct.get("platform", ""))
        return f"{MONITOR_ROOT}/{pf}/{acct_name}/{DAILY}"

    # 5) 手贴：识别出非监控作者 → 【我的笔记】/作者/<名>
    if author and author not in ("未知", "", "匿名"):
        return f"{MYNOTES_ROOT}/作者/{author}"

    # 6) 手贴散文（有分类）→ 【我的笔记】/<分类>
    cat = (item.get("category") or "").strip()
    if cat:
        return f"{MYNOTES_ROOT}/{cat}"

    # 7) 兜底收件箱
    return INBOX


def extract_author(url: str) -> str:
    """从 URL 尽力提取作者（手贴路径 L7 修复）。失败返回 ''（不阻断，落兜底）。

    - B站视频：view API 取 owner.name（游客态通常可用）
    - 其他：抓首页正则 <meta name=author> / 作者：XXX
    """
    if not url:
        return ""
    try:
        if "bilibili" in url:
            m = re.search(r"BV\w+", url)
            if m:
                import urllib.request
                api = "https://api.bilibili.com/x/web-interface/view?bvid=" + m.group(0)
                req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    d = json.loads(r.read().decode())
                return (d.get("data", {}) or {}).get("owner", {}).get("name", "") or ""
        from articles.fetch import fetch_web_content
        txt = fetch_web_content(url)
        if isinstance(txt, tuple):
            txt = txt[1]
        if txt:
            m = re.search(r'<meta\s+name=["\']author["\']\s+content=["\']([^"\']+)', txt, re.I)
            if m:
                return m.group(1).strip()
            m = re.search(r'作者[：:]\s*([^\n]{1,30})', txt)
            if m:
                return m.group(1).strip()
    except Exception:
        return ""
    return ""
