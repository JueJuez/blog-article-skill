"""标题机械规范化回归测试（离线、纯字符串断言，不写盘）。

覆盖 2026-08-25 决策（DECISION-20260825-title-mechanical）：
- 节点标题优先用来源侧标题，而非总结文件首个 # H1（模型自创段标题不可信）。
- normalize_title 确定性清洗：去控制符/折叠空白/去模型前缀/替换非法字符/截断。
- is_generic_section_header 拒绝"总结/要点/摘要…"等段标题。
- choose_node_title 优先序：来源 > H1(非段标题) > 来源兜底。

RED→GREEN：实现未落地前，下列用例应全部失败。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared.title_norm import normalize_title, is_generic_section_header, choose_node_title


# --------------------------------------------------------------------------
# normalize_title
# --------------------------------------------------------------------------

def test_normalize_strips_control_and_collapses_whitespace():
    # 换行被删（不凭空变空格）；tab 转空格；首尾空白去除
    assert normalize_title("  哥飞 ：\n\tSEO\n技巧  ") == "哥飞 ： SEO技巧"


def test_normalize_removes_model_prefix_brackets():
    assert normalize_title("【总结】哥飞的 SEO 实战复盘") == "哥飞的 SEO 实战复盘"


def test_normalize_removes_emoji_prefix():
    assert normalize_title("📌 海外工具站冷启动方法") == "海外工具站冷启动方法"


def test_normalize_replaces_illegal_chars():
    # 飞书标题忌讳 \ / : * ? " < > | → 全角化
    out = normalize_title('A/B:C*D?E"F<G>H|I')
    for ch in ('\\', '/', ':', '*', '?', '"', '<', '>', '|'):
        assert ch not in out
    assert '／' in out and '：' in out


def test_normalize_truncates_at_boundary_with_ellipsis():
    long = "这是一段非常长的标题用来测试截断功能是否按预期在字边界处正确截断而不会拆词"
    out = normalize_title(long, max_len=20)
    assert len(out) <= 21  # 20 + …
    assert out.endswith("…")


def test_normalize_strips_trailing_punctuation():
    assert normalize_title("哥飞 SEO 方法，") == "哥飞 SEO 方法"


def test_normalize_strips_markdown_heading_marker():
    # 防御直接传入带 # 的 H1
    assert normalize_title("# 总结") == "总结"
    assert normalize_title("## 哥飞 SEO 复盘") == "哥飞 SEO 复盘"


# --------------------------------------------------------------------------
# is_generic_section_header
# --------------------------------------------------------------------------

def test_rejects_section_headers():
    for t in ("总结", "要点", "摘要", "概览", "导读", "正文", "笔记",
              "总结：哥飞的分享", "要点提炼", "一、背景", "1. 引言",
              "第三章 方法"):
        assert is_generic_section_header(t), f"应判为段标题: {t}"


def test_accepts_real_titles():
    for t in ("哥飞：用 SEO 做冷启动", "7月19日哥飞的朋友们",
              "海外工具站从 0 到 1 的实操", "中金：2026 宏观展望"):
        assert not is_generic_section_header(t), f"不应判为段标题: {t}"


# --------------------------------------------------------------------------
# choose_node_title（核心：来源优先，不依赖模型）
# --------------------------------------------------------------------------

def test_choose_prefers_source_over_model_h1():
    # 来源是真实标题，H1 是模型段标题 → 用来源
    out = choose_node_title("哥飞：用 SEO 做冷启动", "总结")
    assert out == "哥飞：用 SEO 做冷启动"


def test_choose_falls_back_to_h1_when_source_generic():
    # 来源是泛化（空/段标题），H1 是真实标题 → 用 H1
    out = choose_node_title("", "海外工具站从 0 到 1 的实操")
    assert out == "海外工具站从 0 到 1 的实操"


def test_choose_rejects_generic_h1():
    # 来源缺失且 H1 也是段标题 → 退回来源(空)，绝不把"总结"当标题
    out = choose_node_title("", "总结")
    assert out != "总结"


def test_choose_normalizes_final_title():
    out = choose_node_title("  【复盘】  哥飞 SEO 复盘  ", "要点")
    assert out == "哥飞 SEO 复盘"
