"""shared/wb_ai.py — 可选 WorkBuddy 内置 AI 适配层（C2，对应 PRD A6）

设计原则（PRD 架构约束 3）：WorkBuddy 内置 AI 为可选增强，best-effort 调用，
**绝不阻断主流程**。无 WB AI（Trae / CLI / 其他 AI 平台运行时）时静默返回 None，
原降级行为保持不变。

接入方式（二选一，按优先级）：
1. 代码注入：调用 register_wb_ai(func)，func 签名 (prompt, content, **kwargs) -> str|None
2. 环境变量注入：WORKBUDDY_AI_CALLBACK="module.path:func_name"
   由宿主（如 WorkBuddy 对话运行时）在启动技能前设置，技能 best-effort 加载。

任何异常都被吞掉并返回 None，保证主流程不受 WB AI 状态影响。
"""

import os
import importlib
from typing import Optional, Callable

_WB_AI_CALLBACK: Optional[Callable] = None


def register_wb_ai(func: Callable) -> None:
    """注册一个 WB AI 总结回调。

    宿主（或测试）通过此函数把"内置 AI 总结能力"挂到技能上。
    func 签名建议：(prompt: str, content: str, **kwargs) -> Optional[str]
    """
    global _WB_AI_CALLBACK
    _WB_AI_CALLBACK = func


def clear_wb_ai() -> None:
    """清除已注册的回调（测试或卸载用）。"""
    global _WB_AI_CALLBACK
    _WB_AI_CALLBACK = None


def _resolve_callback() -> Optional[Callable]:
    """解析可用的 WB AI 回调：先注册对象，再环境变量指定。"""
    global _WB_AI_CALLBACK
    if _WB_AI_CALLBACK is not None:
        return _WB_AI_CALLBACK

    spec = os.getenv("WORKBUDDY_AI_CALLBACK", "").strip()
    if not spec:
        return None
    try:
        if ":" not in spec:
            return None
        mod_path, fn_name = spec.split(":", 1)
        module = importlib.import_module(mod_path)
        func = getattr(module, fn_name, None)
        if callable(func):
            return func
    except Exception:
        # 解析失败不影响主流程
        return None
    return None


def call_wb_ai(prompt: str, content: str, **kwargs) -> Optional[str]:
    """best-effort 调用 WorkBuddy 内置 AI 对内容做总结。

    Args:
        prompt: 笔记模板 prompt
        content: 待总结内容
        **kwargs: 透传给回调（如 temperature）

    Returns:
        总结文本；无 WB AI 或调用失败返回 None（绝不抛异常、绝不阻断）
    """
    func = _resolve_callback()
    if func is None:
        return None
    try:
        result = func(prompt=prompt, content=content, **kwargs)
        if isinstance(result, str) and result.strip():
            return result.strip()
        return None
    except Exception:
        # 任何错误都静默降级，保证主流程稳定
        return None
