"""shared/series_naming.py — 系列课文件命名「唯一真相源」（优化 E）。

此前 16/17 集同时出现 `.body.md` 与 `_body.md`，drain 只认 `.body.md`，
导致更优内容被忽略。根因：raw→body 的文件名推导散落在 drain / 子 Agent 合约两处，
没有单一函数保证一致。本模块锁定这一推导，全项目（drain + 子 Agent 指令）只用它。

命名约定（与 videos.main._sanitize_filename + 系列课落盘一致）：
- raw : 第{page:02d}集_{sanitized_part}_raw.md
- body: 第{page:02d}集_{sanitized_part}.body.md   ← 注意是「点」分隔，非下划线
- 落盘: 第{page:02d}集_{sanitized_part}.md

集数一致性校验（拍板3，2026-09-07）：parse_landed_name / find_page_conflict /
scan_landed_names 三函数只认「落盘成稿」命名——同页码已有不同标题成稿时，
新条目跳过（保留原标题，force 也绕不过），杜绝 Penny-Weight 类换标题重抓
导致的整集重复落盘。基线由 _collect_landed_series_names 汇总
（本地 notes 目录 + vault 容器）。
"""
import os
import re

from shared.sanitize import sanitize_filename

_RAW_RE = re.compile(r"^第(\d+)集_(.*?)(_raw)?\.md$")
_BODY_RE = re.compile(r"^第(\d+)集_(.*?)\.body\.md$")
_LANDED_RE = re.compile(r"^第(\d+)集_(.+)$")


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


def parse_landed_name(filename: str):
    """落盘成稿名（第NN集_xxx.md 或 base）→ (page, part)；raw/body/总览等返回 (None, None)。

    集数一致性校验（拍板3）的解析基座：只认「最终成稿」命名，
    中间产物（_raw.md / .body.md）与容器内其他文件（总览、随手记）一律不解析。
    """
    name = os.path.basename(str(filename or ""))
    if name.endswith("_raw.md") or name.endswith(".body.md"):
        return None, None
    if name.endswith(".md"):
        name = name[: -len(".md")]
    m = _LANDED_RE.match(name)
    if not m:
        return None, None
    return int(m.group(1)), m.group(2)


def find_page_conflict(existing_names: list, page: int, part: str) -> str:
    """同页码、sanitize 后不同标题 → 返回冲突名；否则 ""。

    不受 force 影响（拍板3：确定性防护，重跑也不得覆盖已有页码的其他标题）。
    标题按 sanitize 后比较，空白折叠/非法字符差异视为同名。
    """
    new_part = sanitize_filename(part or "")
    for name in existing_names or []:
        p, ex_part = parse_landed_name(name)
        if p is None or ex_part is None:
            continue
        if p == page and sanitize_filename(ex_part) != new_part:
            return name
    return ""


def scan_landed_names(series_dirs: list) -> list:
    """扫描系列目录（列表），返回落盘成稿 base 名（无 .md 后缀），去重。

    目录缺失忽略、raw/body/非成稿文件排除。供 _collect_landed_series_names
    汇总「本地 notes + vault 容器」两处已有成稿，作为页码冲突检测基线。
    """
    seen = []
    for d in series_dirs or []:
        if not d or not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            p, _part = parse_landed_name(f)
            if p is None:
                continue
            if f.endswith(".md"):
                f = f[: -len(".md")]
            if f not in seen:
                seen.append(f)
    return seen
