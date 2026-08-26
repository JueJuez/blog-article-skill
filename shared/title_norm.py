"""shared/title_norm.py — 飞书节点标题确定性规范化（机械层，零 AI）。

问题根因（用户 2026-08-25）：飞书总结文章标题大部分错/乱。
定位：monitors/drain_pending.py 旧逻辑 `node_title = _extract_h1(content) or title`
把「总结文件首个 # 标题」当节点标题，而该 H1 是子 Agent（模型）写的，
常是「# 总结」「# 要点提炼」「# 【XXX】复盘」这类模型自创的"段标题"，
并非真实内容标题 → 飞书里一堆乱/错标题。

机械解法（用户要求不依赖模型）：
- 标题是「内容来源的属性」，不是「总结的属性」。
  优先用来源侧标题（公众号列表标题 / B站视频标题 / derive_title_from_body 提炼的首句），
  仅在来源缺失时退化为总结 H1。
- 无论取哪个，都过 normalize_title 做确定性清洗：
  去控制符/折叠空白/去模型前缀/替换飞书非法字符/按字边界截断。
- is_generic_section_header 拒绝"段标题"类 H1（总结/要点/摘要/概览…），
  避免把模型段标题当节点标题。
"""
import re

# 模型常给总结文件加的"段标题"整体（命中即视为非真实标题）
_SECTION_HEADER_RE = re.compile(
    r'^(总结|要点|核心要点|内容要点|摘要|概览|导读|正文|笔记|复盘|解读|'
    r'一[、. ]|二[、. ]|三[、. ]|\d+[、. ]|'
    r'第[一二三四五六七八九十\d]+[章节部分篇])',
)
# 模型常加的标题前缀（【XX】/📌/总结：等），清洗时剥离
_MODEL_PREFIX_RE = re.compile(
    r'^(【[^】]{0,12}】\s*|📌\s*|▶[️ ]?\s*|🔹\s*|▍\s*|'
    r'(总结|要点|核心|摘要|概览|导读)[:：]\s*)',
)
# 飞书 Wiki 节点标题非法字符（文件名/标题都忌讳）→ 全角化
_ILLEGAL = str.maketrans({
    '\\': '＼', '/': '／', ':': '：', '*': '＊',
    '?': '？', '"': '＂', '<': '〈', '>': '〉', '|': '｜',
})


def is_generic_section_header(t: str) -> bool:
    """总结文件首个 # 标题若是模型自创的"段标题"（总结/要点/摘要…）则 True。

    用于判定 H1 是否可信：段标题不能当节点标题。
    """
    t = (t or "").strip()
    if not t:
        return True
    if t in ("总结", "要点", "摘要", "概览", "导读", "正文", "笔记", "复盘"):
        return True
    return bool(_SECTION_HEADER_RE.match(t))


def normalize_title(raw: str, max_len: int = 50) -> str:
    """确定性标题清洗（无 AI）：

    - 去控制符/换行、折叠内部空白
    - 去模型前缀（【XX】/📌/总结：）
    - 替换飞书非法字符（→ 全角）
    - 按字边界截断 + …
    - 去首尾残留标点/省略号
    """
    if not raw:
        return ""
    t = raw
    # 去 markdown 标题标记（# 总结 → 总结），防御直接传入带 # 的 H1
    t = re.sub(r"^#+\s*", "", t)
    # 去控制字符与换行（保留普通空格/制表符后统一为空格）
    t = "".join(ch for ch in t if ch in (" ", "\t") or ord(ch) >= 32)
    t = t.replace("\t", " ").strip()
    # 折叠连续空白
    t = re.sub(r"\s+", " ", t).strip()
    # 去模型前缀
    t = _MODEL_PREFIX_RE.sub("", t).strip()
    # 替换非法字符
    t = t.translate(_ILLEGAL)
    # 截断（按字符边界，中文不拆词但保边界）；先标记是否截断，避免末尾 strip 把 … 删掉
    truncated = len(t) > max_len
    if truncated:
        t = t[:max_len].rstrip("，,、；;：: ")
    # 去首尾残留标点（注意不含 …，否则会把截断省略号洗掉）
    t = t.strip(" ，,、；;：:。.!！?？-—_|｜")
    if truncated:
        t = t + "…"
    return t


def choose_node_title(source_title: str, summary_h1: str = "", max_len: int = 50) -> str:
    """选飞书节点标题（机械优先序，零 AI）：

    1. 来源侧标题（来源可靠、非模型自创）→ 首选
    2. 总结 H1（仅当来源缺失且其非"段标题"）→ 次选
    3. 兜底来源标题（即便空也返回，由上游 L1 门禁卡空标题）

    旧逻辑 _extract_h1(content) or title 把"模型段标题"当节点标题，
    是飞书标题乱/错根因；本函数改为来源优先 + 全程 normalize。
    """
    def _clean(x):
        x = (x or "").strip()
        x = re.sub(r"^#+\s*", "", x)  # 去 markdown 标题标记（防御性）
        return x
    src = _clean(source_title)
    if src and not is_generic_section_header(src):
        return normalize_title(src, max_len)
    h1 = _clean(summary_h1)
    if h1 and not is_generic_section_header(h1):
        return normalize_title(h1, max_len)
    # 来源 / H1 都不可用（空或都是模型段标题）→ 返回空，
    # 由上游 L1 门禁（drain_pending 标题为空直接跳过）处理，绝不把"总结"当标题。
    return ""
