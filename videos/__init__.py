"""videos — 视频总结模块

对视频/音频的字幕或 transcript 做总结，复用 prompts 共享模板与
articles 的抓取/保存能力。

- fetch:   获取层（P2.1）—— YouTube/Bilibili 字幕、playlist 迭代
- asr:     P3 —— 本地/任意视频 faster-whisper 转写
- multimodal: P4 —— 可选 Gemini 多模态理解
- main:    编排（P2.2 分块两段式 + P2.3 分集/playlist）
"""
from .main import summarize_video
from . import fetch, asr, multimodal

__all__ = ["summarize_video", "fetch", "asr", "multimodal"]
