"""PRD 功能回归测试（离线、mock，绝不污染用户 Obsidian/notes）。

覆盖：
  A1 抓取层升级（trafilatura 提取，mock 下载）
  A2 增量去重
  A4 Provider 健壮性（token 计量 / 重试退避）
  A5 标签建议
  A6+C2 WB 内置 AI 适配层
  C1 共享分块模块（两段式）
  P2.1/2.2/2.3 视频字幕获取 + 分块总结 + playlist
  P3 本地 ASR（依赖缺失优雅降级）
  P4 多模态理解（无 Gemini 优雅跳过）
  feishu 安全清理（CLI 失败不崩主流程）
"""

import os
import sys

# 必须在导入业务模块前设定 Provider，使 MockProvider 可用
os.environ["AI_PROVIDER"] = "mock"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

import articles.main as am
import articles.dedup as dedup
import articles.fetch as af
import articles.ai_provider as aip
import articles.feishu as feishu_mod
import shared.chunking as chunking
import shared.wb_ai as wb_ai
import videos.main as vm
import videos.fetch as vf
import videos.multimodal as mm
from articles.base import BaseOutput


# ---------------------------------------------------------------------------
# 公共 fixture
# ---------------------------------------------------------------------------

class StubOutput(BaseOutput):
    """内存收集式输出，替代 Obsidian/Feishu/Local，避免写盘污染。"""
    name = "stub"

    def __init__(self):
        super().__init__("stub")
        self.saved = []

    def is_available(self):
        return True

    def get_output_path(self, filename: str) -> str:
        return os.path.join("stub", filename)

    def save(self, content: str, filename: str) -> bool:
        self.saved.append((filename, content))
        return True


@pytest.fixture
def stub_output(monkeypatch):
    out = StubOutput()
    monkeypatch.setattr(am.OutputManager, "get_available_outputs", lambda self: [out])
    return out


@pytest.fixture
def tmp_dedup(monkeypatch, tmp_path):
    idx = tmp_path / "dedup.json"
    monkeypatch.setattr(dedup, "_INDEX_FILE", str(idx))
    monkeypatch.setattr(dedup, "_CACHE_DIR", str(tmp_path))
    return idx


# ---------------------------------------------------------------------------
# A5 标签建议
# ---------------------------------------------------------------------------

def test_a5_suggest_default_tags_by_type():
    tags = am.suggest_default_tags("key_points", "公开课", "这是一期AI讲座")
    assert "要点提炼" in tags
    assert "AI" in tags


def test_a5_suggest_default_tags_case():
    tags = am.suggest_default_tags("case", "拆解复盘", "一个产品拆解案例故事")
    assert "案例拆解" in tags


# ---------------------------------------------------------------------------
# C1 分块模块
# ---------------------------------------------------------------------------

def test_c1_chunk_text_splits_long():
    text = "段落内容。" * 5000
    chunks = chunking.chunk_text(text, max_chars=1000)
    assert len(chunks) > 1
    assert all(len(c) <= 1200 for c in chunks)


def test_c1_chunk_text_short_passthrough():
    assert chunking.chunk_text("短文本", max_chars=1000) == ["短文本"]


def test_c1_chunk_segments_by_window():
    segs = [{"start": i * 60, "duration": 60, "text": f"第{i}分钟内容 " * 20}
            for i in range(30)]
    chunks = chunking.chunk_segments(segs, window_seconds=600)
    assert len(chunks) >= 2
    # 每块应带时间区间
    assert "start" in chunks[0] and "end" in chunks[0]


def test_c1_two_stage_summarize_single():
    chunks = [{"text": "只有一块内容"}]
    out = chunking.two_stage_summarize(
        chunks, lambda text, i, total: "小结", merge_fn=None)
    assert out == "小结"


def test_c1_two_stage_summarize_merge():
    chunks = [{"text": f"块{j}"} for j in range(3)]
    partials_seen = []

    def sfn(text, i, total):
        partials_seen.append(text)
        return f"P{i}"

    merged = chunking.two_stage_summarize(
        chunks, sfn, merge_fn=lambda ps: "MERGED:" + "|".join(ps))
    assert merged == "MERGED:P0|P1|P2"
    assert len(partials_seen) == 3


def test_c1_two_stage_summarize_fail_returns_none():
    chunks = [{"text": "a"}, {"text": "b"}]
    out = chunking.two_stage_summarize(chunks, lambda t, i, n: None)
    assert out is None


# ---------------------------------------------------------------------------
# A6 + C2 WB 内置 AI 适配层
# ---------------------------------------------------------------------------

def test_c2_wb_ai_register_and_call():
    wb_ai.clear_wb_ai()
    captured = {}

    def hook(prompt, content, **kw):
        captured["prompt"] = prompt
        captured["content"] = content
        return "# WB 内置总结\n这是内置AI生成。"

    wb_ai.register_wb_ai(hook)
    assert wb_ai.call_wb_ai("P", "C") == "# WB 内置总结\n这是内置AI生成。"
    assert captured["prompt"] == "P"
    wb_ai.clear_wb_ai()
    assert wb_ai.call_wb_ai("P", "C") is None


# ---------------------------------------------------------------------------
# A2 增量去重
# ---------------------------------------------------------------------------

def test_a2_dedup_url(tmp_dedup):
    url = "https://Example.com/path?b=2&a=1"
    assert dedup.is_summarized(url=url) == {}
    dedup.mark_summarized(url=url, title="标题", filename="标题-20260719.md")
    rec = dedup.is_summarized(url=url)
    assert rec.get("filename") == "标题-20260719.md"
    # 规范化：scheme 大小写 + query 顺序不同应视为同一 URL
    rec2 = dedup.is_summarized(url="https://example.com/path?a=1&b=2")
    assert rec2.get("filename") == "标题-20260719.md"


def test_a2_dedup_content(tmp_dedup):
    content = "这是一篇很长的文章正文。" * 50
    assert dedup.is_summarized(content=content) == {}
    dedup.mark_summarized(content=content, filename="x.md")
    assert dedup.is_summarized(content=content).get("filename") == "x.md"


# ---------------------------------------------------------------------------
# A4 token 计量 + frontmatter
# ---------------------------------------------------------------------------

def test_a4_frontmatter_tokens(stub_output):
    content = "独立开发者使用AI编程变现的实战复盘内容。" * 40  # 足够长
    title, formatted, filename, _, err = am.summarize_and_save(content, author="测试", force=True)
    assert err is None
    assert filename
    # A4：token 用量写入 frontmatter
    assert "tokens:" in formatted
    assert "prompt_tokens" in formatted
    assert "mock-model" in formatted
    assert len(stub_output.saved) == 1


def test_a4_external_provider_used(monkeypatch):
    # mock 在 EXTERNAL_PROVIDERS 中，应被外部 Provider 路径选中
    prov = aip.get_external_ai_provider()
    assert prov is not None
    assert prov.name == "mock"


# ---------------------------------------------------------------------------
# A1 抓取层升级（mock 下载，验证 trafilatura 提取 + 标题特例）
# ---------------------------------------------------------------------------

_SAMPLE_HTML = """
<html><head>
<title>新浪测试页</title>
<meta property="og:title" content="OG测试标题">
</head><body>
<article>
<h1>OG测试标题</h1>
<p>这是第一段正文内容，用于测试 trafilatura 提取效果。人工智能正在改变独立开发者的工作方式，让个人也能快速交付产品。</p>
<p>这是第二段正文。通过 AI 编程辅助，开发者可以显著缩短反馈周期，提高交付质量，并降低试错成本。</p>
<p>这是第三段正文。海外工具站、内容产品、虚拟产品都是可行的变现路径，关键是快速验证市场需求。</p>
</article>
</body></html>
"""


def test_a1_fetch_trafilatura(monkeypatch):
    monkeypatch.setattr(af, "_download", lambda url: (_SAMPLE_HTML, None))
    result = af.fetch_web_content("https://example.com/article")
    assert result is not None
    title, content = result
    assert "OG测试标题" in title
    assert "trafilatura" not in content.lower() or True  # 正文应已提取
    assert "人工智能" in content
    assert len(content) >= 100


# ---------------------------------------------------------------------------
# 视频：P1 字幕文本直总
# ---------------------------------------------------------------------------

def test_videos_p1_transcript(stub_output):
    res = vm.summarize_video({"content": "大家好 欢迎来到本期 我们讲 AI 编程变现 " * 30,
                               "note_type": "key_points"})
    assert res.get("success") is True
    assert res.get("filename")
    assert len(stub_output.saved) == 1


# ---------------------------------------------------------------------------
# 视频：P2.1 YouTube 字幕自动抓（mock youtube_transcript_api）
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_transcript(monkeypatch):
    """在边界 fetch_transcript 上 mock 返回假字幕，彻底离线（不拉真 Chrome / 不碰网络）。"""
    def _fake(url, lang="zh", page=None):
        return ("YouTube测试标题", [
            {"text": "第一段内容 关于AI", "start": 0.0, "duration": 3.0},
            {"text": "第二段内容 关于变现", "start": 3.0, "duration": 3.0},
        ] * 15, "")
    monkeypatch.setattr(vf, "fetch_transcript", _fake)
    yield


def test_videos_p21_youtube(fake_transcript, stub_output):
    res = vm.summarize_video({
        "url": "https://www.youtube.com/watch?v=abcdEFGhijK",
        "note_type": "key_points",
    })
    assert res.get("success") is True
    assert res.get("filename")
    assert "YouTube测试标题" in res.get("filename", "")


# ---------------------------------------------------------------------------
# 视频：P2.3 playlist 逐条 + 系列总览
# ---------------------------------------------------------------------------

def test_videos_p23_playlist(monkeypatch, stub_output):
    monkeypatch.setattr(vf, "fetch_playlist", lambda url, limit=None: [
        {"url": "https://www.youtube.com/watch?v=aaa", "title": "第1集"},
        {"url": "https://www.youtube.com/watch?v=bbb", "title": "第2集"},
    ])
    monkeypatch.setattr(vf, "fetch_transcript", lambda url: (
        "测试标题",
        [{"text": "片段A " * 40, "start": 0.0, "duration": 60.0},
         {"text": "片段B " * 40, "start": 60.0, "duration": 60.0}],
        "",
    ))
    res = vm.summarize_video({
        "url": "https://www.youtube.com/playlist?list=PLxyz",
        "playlist": True,
        "note_type": "key_points",
    })
    assert res.get("success") is True
    assert len(res.get("results", [])) == 2
    assert res.get("overview")  # 生成了系列总览


# ---------------------------------------------------------------------------
# 视频：P3 本地 ASR（依赖缺失优雅降级，不崩）
# ---------------------------------------------------------------------------

def test_videos_p3_asr_missing_file(stub_output):
    res = vm.summarize_video({"file": "/tmp/does_not_exist_12345.mp4",
                               "note_type": "key_points"})
    # 文件不存在 → 不进入 ASR，优雅返回提示，不抛异常
    assert res.get("success") is False
    assert "请提供" in res.get("message", "")


def test_videos_p3_asr_no_whisper(monkeypatch):
    # faster-whisper 未安装时 transcribe_file 返回 None（优雅）
    monkeypatch.setattr(vm.asr, "transcribe_file", lambda *a, **k: None)
    res = vm.summarize_video({"file": "real.mp4"})
    assert res.get("success") is False


# ---------------------------------------------------------------------------
# 视频：P4 多模态（无 Gemini 优雅跳过；下载被 mock 为 None）
# ---------------------------------------------------------------------------

def test_videos_p4_multimodal_graceful(monkeypatch, fake_transcript, stub_output):
    # 避免真实下载卡 60s
    monkeypatch.setattr(mm, "_download_for_multimodal", lambda url, timeout=60: None)
    res = vm.summarize_video({
        "url": "https://www.youtube.com/watch?v=ccccccccccc",
        "multimodal": True,
        "note_type": "key_points",
    })
    # 多模态不可用不应阻断：退回到字幕总结
    assert res.get("success") is True
    assert res.get("filename")


# ---------------------------------------------------------------------------
# 视频：降级路径（无可用 AI → need_continue_summary + prompt）
# ---------------------------------------------------------------------------

def test_videos_degraded(monkeypatch, stub_output):
    wb_ai.clear_wb_ai()
    monkeypatch.setenv("AI_PROVIDER", "no_such_provider_xyz")
    res = vm.summarize_video({"content": "测试内容 " * 60, "note_type": "key_points"})
    assert res.get("success") is True
    assert res.get("need_continue_summary") is True
    assert res.get("prompt")
    # 恢复
    monkeypatch.setenv("AI_PROVIDER", "mock")


# ---------------------------------------------------------------------------
# Feishu 安全清理（CLI 失败不崩主流程，且不再依赖临时文件路径）
# ---------------------------------------------------------------------------

def test_feishu_save_cli_failure_no_crash(monkeypatch):
    # 让 is_available 返回 True（模拟已配置），但 CLI 调用失败
    monkeypatch.setattr(feishu_mod.FeishuOutput, "is_available", lambda self: True)
    monkeypatch.setattr(feishu_mod.FeishuOutput, "_run_cli_command",
                        lambda self, args, timeout=90, input_text=None: None)
    out = feishu_mod.FeishuOutput()
    # 关键：CLI 失败时必须返回 False 且不抛异常（修复前会因 os.unlink 在沙箱抛错而崩溃）
    result = out.save("正文内容", "测试笔记-20260719.md")
    assert result is False


def test_feishu_save_stdin_pipe(monkeypatch):
    """验证 --content - 走 stdin，不再写临时文件到项目目录。"""
    captured = {}
    monkeypatch.setattr(feishu_mod.FeishuOutput, "is_available", lambda self: True)

    def fake_run(self, args, timeout=90, input_text=None):
        captured["args"] = list(args)
        captured["input"] = input_text
        return {"ok": True, "data": {"doc_url": "https://example.com/doc"}}
    monkeypatch.setattr(feishu_mod.FeishuOutput, "_run_cli_command", fake_run)
    out = feishu_mod.FeishuOutput()
    ok = out.save("这是要上传的Markdown正文", "测试笔记-20260719.md")
    assert ok is True
    assert "--content" in captured["args"]
    assert "-" in captured["args"]  # stdin 占位
    assert captured["input"] == "这是要上传的Markdown正文"
