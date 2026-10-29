# -*- coding: utf-8 -*-
"""shared/source_types.py — 源「内容类型」判定的单一真源（2026-10-29）。

判据是**信息半衰期 / 可沉淀性**，不是数值阈值：

- 教程（步骤可复用）与系列课（缠论 / 交易体系 / 波浪理论等成体系课程）→ 值得沉淀
  → 走「提炼 → 按 pack 写作」两阶段；
- 观点 / 研报 / 宏观 / 行情复盘 / 公司基本面分析 → 结论随行情失效 → 单轮
  （只看结论；要沉淀的框架另起一篇笔记，用户 2026-10-29 决策）；
- 框架 / 方法论 → 单轮（同上：框架本身值得单独成篇，不塞进源文笔记）；
- 未识别 → 按可沉淀处理。保守方向是「多花一次提炼成本」，不是「悄悄降质」。

为什么不沿用阈值：2026-10-29 复核旧路由的两道数字闸（密度 0.08 / 不同数字串 130），
全库 139 篇长源里闸①零作用，闸②落在空带（放行侧最大 115、拦截侧最小 155）；
它们拦的那批 B 类源的共同点是**时效**，不是「数字多」。可调阈值等于没有判据，
所以判据改为体裁类别词的存在性。

口径：标题 + 正文开头一句（≤ `BODY_HEAD_CHARS` 字，跳过空行 / `#` 标题 / `>` 元信息）。
只扫开头一句，是为了不让正文中段偶发的一次「估值」把整篇长文翻成时效——误判方向
必须落在「保守走两阶段」。类别词表以真实标题测量为据（见 `_tmp/measure_source_types.py`），
增删词要重新测量命中与误伤，不能凭手感调。
"""
from __future__ import annotations

import re

from shared.source_metrics import content_lines

TUTORIAL = "tutorial"
EPHEMERAL = "ephemeral"
FRAMEWORK = "framework"
NEWS = "news"

# 正文取样长度（只取开头一句，见模块说明）
BODY_HEAD_CHARS = 400

KEYWORDS: dict = {
    # 教学类：标题/开头点明「教你怎么做」→ 步骤可复用，值得两阶段提炼。
    # 系列课（缠论 / 交易体系 / 波浪理论 / 技术分析）按用户 2026-10-29 追加归此类：
    # 它们是成体系的课程材料，质量优先级等同教程。
    TUTORIAL: ("手把手", "保姆", "实操", "从零", "0基础", "零基础", "教程", "课程",
               "教学", "入门", "step by step", "step-by-step", "教你", "教会你",
               "缠论", "论缠", "缠中说禅", "技术分析", "交易系统", "交易体系",
               "波浪理论"),
    # 时效类：结论会随行情/时间失效
    EPHEMERAL: ("估值", "价值分析", "个股", "宏观", "中金", "黄金", "研报", "观点",
                "目标价", "财报", "业绩", "联合研究", "投资价值", "配置价值", "大盘",
                "牛市", "熊市", "市值", "净利润", "营业收入", "招股书", "值不值",
                "这笔账", "【深度】", "直播回放", "直播"),
    # 框架类：只留「解释性框架」，价值在框架本身（另起一篇笔记），源文笔记只要结论。
    # 系列课词已归教程（见上），这里不收「缠论/体系/技术分析」这类课程表征。
    FRAMEWORK: ("方法论", "底层逻辑", "知识体系", "分析框架", "心法"),
    # 资讯类：行情/周报/盘面播报
    NEWS: ("下周", "行情", "展望", "周复盘", "周报", "早盘", "收盘", "盘前", "盘后",
           "日报", "速览", "快讯", "午评", "收评", "本周", "复盘"),
}

# 命中优先级：先判「更值得沉淀」的类目，避免教程被时效词抢走（手把手教你做估值 → 教程）。
PRIORITY = (TUTORIAL, EPHEMERAL, FRAMEWORK, NEWS)

# 只有教程与「未识别」走两阶段；其余一律单轮。
SINGLE_TYPES = (EPHEMERAL, FRAMEWORK, NEWS)

_SEPARATOR = re.compile(r"^[\s\-=_*|]+$")
_SENTENCE_END = re.compile(r"[。！？；!?;…]+")


def _body_head(text: str, chars: int = BODY_HEAD_CHARS) -> str:
    """正文开头一句：累积到第一个句末标点为止，最多 `chars` 字。"""
    picked, size = [], 0
    for line in content_lines(text or ""):
        if _SEPARATOR.match(line):
            continue
        picked.append(line)
        joined = "".join(picked)
        end = _SENTENCE_END.search(joined)
        if end:
            return joined[:end.end()]
        size += len(line)
        if size >= chars:
            break
    return "".join(picked)[:chars]


def classify_source_type(title: str = "", text: str = "") -> dict:
    """返回 {"type", "hits"}；无类别词命中时 `type` 为空串（调用方按可沉淀处理）。"""
    hay = f"{title or ''}\n{_body_head(text or '')}".lower()
    for type_name in PRIORITY:
        hits = [word for word in KEYWORDS[type_name] if word.lower() in hay]
        if hits:
            return {"type": type_name, "hits": hits}
    return {"type": "", "hits": []}


def route_for_type(type_name: str) -> str:
    """类别 → 路由。空串与未知类别一律 `distill`（不得静默降级）。"""
    return "single" if type_name in SINGLE_TYPES else "distill"
