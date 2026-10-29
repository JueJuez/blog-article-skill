# -*- coding: utf-8 -*-
"""数值保真检查 — 带单位读数必须能在源里逐字核对（2026-10-29）。

判据是**二值、不可调参**的：
1. 稿里带单位的数字串必须在源里逐字存在（千分位、空格、全角百分号归一后比较）；
2. 取整或换算出来的数字，同一行必须保留源原串（否则读者无法核对）；
3. 没有源可接地（例如纯汇编稿）→ 不检查，也不拿「零问题」冒充通过。

上线节奏（用户 2026-10-29 决策）：默认 `shadow`，只写 JSONL 不拦落盘；两周后把
`NUMERIC_FIDELITY_GATE` 切成 `block` 才进落盘门禁。`off` 关闭。未知取值一律回落
`shadow`——宁可少拦，也不许配置写错就悄悄拦盘。

日志默认 `notes/_meta/numeric_fidelity.jsonl`，可用 `NUMERIC_FIDELITY_LOG` 重定向；
写日志失败不影响主流程（`logged=False`）。
"""
from __future__ import annotations

import json
import os
import re
import time

from prompts.evidence import UNITS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(ROOT, "notes", "_meta", "numeric_fidelity.jsonl")

_UNIT_ALT = "|".join(UNITS)
_DIGITS = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
UNIT_NUMBER = re.compile(rf"(?:{_DIGITS})\s*(?:{_UNIT_ALT})")

_WS = re.compile(r"\s+")
_META = re.compile(r"^\s*(?:>\s*)?(?:\*\*)?(?:作者|来源|来源链接|抓取时间|发布时间|标题|渠道|标签|原文)"
                   r"(?:\*\*)?\s*[：:]")
_URL = re.compile(r"https?://\S+")
_FENCE = re.compile(r"^\s*(?:`{3,}|~{3,})")
_SEPARATOR = re.compile(r"^[\s\-=_*|]+$")

MODES = ("shadow", "block", "off")


def numeric_fidelity_mode() -> str:
    """`NUMERIC_FIDELITY_GATE`：shadow（默认）/ block / off；未知值回落 shadow。"""
    mode = (os.environ.get("NUMERIC_FIDELITY_GATE") or "shadow").strip().lower()
    return mode if mode in MODES else "shadow"


def _norm(token: str) -> str:
    return _WS.sub("", token).replace(",", "").replace("％", "%")


def unit_numbers(text: str) -> set:
    """带单位数字串的归一集合（`4,540 万元` 与 `4540万元` 视为同一串）。"""
    return {_norm(t) for t in UNIT_NUMBER.findall(text or "")}


def _note_lines(note: str):
    """逐行产出正文：跳过元信息、代码围栏、分隔线，并挖掉 URL（URL 里的数字不是读数）。"""
    fence = False
    for number, raw in enumerate((note or "").splitlines(), 1):
        if _FENCE.match(raw):
            fence = not fence
            continue
        if fence or _SEPARATOR.match(raw) or _META.match(raw):
            continue
        yield number, _URL.sub("", raw)


def check_numeric_fidelity(note: str, source: str) -> dict:
    """返回 {"passed","issues","checked","derived_with_source"}（纯函数，不写盘）。"""
    grounded = unit_numbers(source or "")
    issues, checked, derived = [], 0, 0
    for number, line in _note_lines(note):
        found = {_norm(t) for t in UNIT_NUMBER.findall(line)}
        if not found:
            continue
        checked += len(found)
        missing = found - grounded
        if not missing:
            continue
        if found & grounded:
            # 换算/取整：同行保留了源原串，读者可核对
            derived += 1
            continue
        for token in sorted(missing):
            issues.append(f"数值保真[{token}] 第{number}行：源中找不到该读数，且同行未保留源原串")
    return {"passed": not issues, "issues": issues,
            "checked": checked, "derived_with_source": derived}


def _append_log(record: dict, log_path: str) -> bool:
    path = log_path or os.environ.get("NUMERIC_FIDELITY_LOG") or LOG_FILE
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return False
    return True


def run_numeric_fidelity(note: str, source: str, *, url: str = "", title: str = "",
                         log_path: str = "") -> dict:
    """按当前模式执行检查并留痕。`shadow=True` 时调用方**不得**据此拦落盘。

    block 模式也只回传结论，拦截动作由落盘入口按 `passed` 决定（本模块不写业务状态）。
    """
    mode = numeric_fidelity_mode()
    blank = {"mode": mode, "passed": True, "issues": [], "checked": 0,
             "derived_with_source": 0, "shadow": mode != "block", "logged": False}
    if mode == "off" or not (source or "").strip():
        return dict(blank)
    report = check_numeric_fidelity(note, source)
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": mode,
              "url": url, "title": title,
              "passed": report["passed"], "checked": report["checked"],
              "derived_with_source": report["derived_with_source"],
              "issues": report["issues"]}
    logged = _append_log(record, log_path)
    return {**report, "mode": mode, "shadow": mode != "block", "logged": logged}


def apply_to_gate(gate: dict, note: str, source: str, *, url: str = "",
                  title: str = "", log_path: str = "") -> dict:
    """把数值保真结论并入机械门禁结果（落盘入口共用一处合并逻辑）。

    只有 `block` 模式且未通过才改写门禁：`passed=False` + 追加 issues；`shadow` / `off`
    只留痕、原样返回，绝不据此拦盘（2026-10-29 用户拍板的两周观测期）。
    """
    report = run_numeric_fidelity(note, source, url=url, title=title, log_path=log_path)
    if report["mode"] != "block" or report["passed"]:
        return gate
    merged = dict(gate)
    merged["passed"] = False
    merged["issues"] = list(gate.get("issues") or []) + list(report["issues"])
    return merged
