# -*- coding: utf-8 -*-
"""shared/source_metrics.py — 源/稿「正文数字串」口径的单一真源。

为什么单独下沉（2026-10-27，S9 第 8 条①）：
- `scripts/measure_fact_density.py` 早就定过一套口径（对照轮判据 3 的事实密度）：
  只数**正文**阿拉伯数字串、**跳过 `#` 标题行**、**源侧另跳过抓取注入的元信息块**
  （`> 标题：`/`> 时间：`/`> 原始文章内容`），数值按串比对、忽略 `%` 与单位差异。
- S9 路由需要一个「源里到底有多少不同数字」的可复用量，如果再造一份正则就会出现
  同名不同义两份口径（本仓库已踩过 `normalize_title` 的坑）→ 下沉到这里共享。
- 本模块**只做口径**，不做取舍判断：判阈值是 `shared/distill_dispatch.route_for_source`
  的事，密度分数是 `scripts/measure_fact_density.density` 的事。

口径改动必须同步：`scripts/measure_fact_density.py:CALIBER`、`docs/PIPELINES.md` 的量尺条目。
"""
from __future__ import annotations

import re

# 数字串：整数或一位小数；`%` 与单位不入串（源写 97%、稿写 97 算同一串）。
NUM = re.compile(r"\d+(?:\.\d+)?")

# 抓取管线注入的元信息块：这里的数字是集数 / 发布日期 / 时间戳，不属于源内容。
META_LINE = re.compile(r"^>\s*(标题|时间|原始文章内容|来源|链接|URL|作者|发布)")


def content_lines(text: str):
    """逐行产出「正文」行：跳过空行、`#` 标题行与元信息块。"""
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or META_LINE.match(s):
            continue
        yield s


def content_text(text: str) -> str:
    """正文拼接（按口径过滤后）。"""
    return "\n".join(content_lines(text))


def source_numbers(text: str) -> set:
    """正文里不同数字串的集合（去重）。"""
    return set(NUM.findall(content_text(text)))


def unique_number_count(text: str) -> int:
    """不同数字串的**绝对个数**（不看长度）——S9 路由判据之一。"""
    return len(source_numbers(text))


def numbers_total(text: str) -> int:
    """数字串出现总次数（不去重）。"""
    return len(NUM.findall(content_text(text)))
