# -*- coding: utf-8 -*-
"""守卫：asr.py 对 videos.fetch 的跨模块引用不得断裂（防重构改名遗留）。

背景：2026-09-03 cookie 获取重构拍板「唯一方法 = _bili_extract_cookies_cdp」，
旧函数 _bili_auto_extract_cookies 被删除，但 asr.py 的刷新重试调用点未同步，
AttributeError 被 except 吞掉 → ASR cookie 刷新兜底链长期静默死亡（无CC视频
的 ASR 兜底全部失败却无人察觉）。

本守卫机械断言：asr.py 源码引用的每个 fetch._bili_* 函数都必须真实存在。
运行：pytest tests/test_asr_fetch_refs.py
"""
import re
from pathlib import Path

import videos.fetch as fetch_mod

ASR_SRC = Path(__file__).resolve().parent.parent / "videos" / "asr.py"


def test_asr_does_not_reference_deleted_cookie_func():
    """已删除的旧函数名不得再出现在 asr.py 源码中。"""
    src = ASR_SRC.read_text(encoding="utf-8")
    assert "_bili_auto_extract_cookies" not in src, (
        "asr.py 引用了已删除的 fetch._bili_auto_extract_cookies（2026-09-03 重构遗留）")


def test_asr_fetch_bili_refs_all_exist():
    """asr.py 引用的每个 fetch._bili_* 函数都必须在 videos.fetch 中真实存在。"""
    src = ASR_SRC.read_text(encoding="utf-8")
    refs = sorted(set(re.findall(r"fetch\.(_bili_\w+)", src)))
    assert refs, "asr.py 应至少引用一个 fetch._bili_* 函数（否则守卫失去意义）"
    missing = [name for name in refs if not hasattr(fetch_mod, name)]
    assert not missing, f"asr.py 引用的函数在 videos.fetch 中不存在（改名断裂）: {missing}"
