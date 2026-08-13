"""shared/series_naming.py — 系列课文件命名「唯一真相源」（优化 E）。

此前 16/17 集同时出现 `.body.md` 与 `_body.md`，drain 只认 `.body.md`，
导致更优内容被忽略。根因：raw→body 的文件名推导散落在 drain / 子 Agent 合约两处，
没有单一函数保证一致。本模块锁定这一推导，全项目（drain + 子 Agent 指令）只用它。

命名约定（与 videos.main._sanitize_filename + 系列课落盘一致）：
- raw : 第{page:02d}集_{sanitized_part}_raw.md
- body: 第{page:02d}集_{sanitized_part}.body.md   ← 注意是「点」分隔，非下划线
- 落盘: 第{page:02d}集_{sanitized_part}.md
"""
import os
import re

_RAW_RE = re.compile(r"^第(\d+)集_(.*?)(_raw)?\.md$")
_BODY_RE = re.compile(r"^第(\d+)集_(.*?)\.body\.md$")


def parse_raw_name(filename: str):
    """从 第NN集_xxx_raw.md 解析 (page:int, part:str)。失败返回 (None, None)。"""
    m = _RAW_RE.match(os.path.basename(filename))
    if not m:
        return None, None
    return int(m.group(1)), m.group(2)


def parse_body_name(filename: str):
    """从 第NN集_xxx.body.md 解析 (page:int, part:str)。失败返回 (None, None)。"""
    m = _BODY_RE.match(os.path.basename(filename))
    if not m:
        return None, None
    return int(m.group(1)), m.group(2)


def body_path(raw_abs: str) -> str:
    """raw 绝对路径 → body 绝对路径（唯一推导，禁止别处自行拼接）。"""
    if raw_abs.endswith("_raw.md"):
        return raw_abs[: -len("_raw.md")] + ".body.md"
    if raw_abs.endswith(".body.md"):
        return raw_abs
    return raw_abs + ".body.md"


def raw_path(body_abs: str) -> str:
    """body 绝对路径 → raw 绝对路径（逆推导，供 rescue / 校验用）。"""
    if body_abs.endswith(".body.md"):
        return body_abs[: -len(".body.md")] + "_raw.md"
    if body_abs.endswith("_raw.md"):
        return body_abs
    return body_abs + "_raw.md"


def detect_stray_underscore(series_dir: str) -> list:
    """扫描系列目录，返回所有「错误下划线命名」的 body 文件（*_body.md）。

    这些是子 Agent 命名不一致产生的残留，drain 不认，必须告警/纠正，
    否则更优内容会被静默忽略（16/17 集事故的根因）。
    """
    if not os.path.isdir(series_dir):
        return []
    stray = []
    for f in os.listdir(series_dir):
        # 命中 *_body.md 但非 .body.md 的正规命名
        if f.endswith("_body.md") and not f.endswith(".body.md"):
            stray.append(os.path.join(series_dir, f))
    return stray


def normalized_base(page: int, part: str) -> str:
    """生成 第NN集_xxx 基础名（不含后缀）。part 应已 sanitize。"""
    return f"第{page:02d}集_{part}"
