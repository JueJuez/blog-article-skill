"""test_routing_gaps_20260905 — 归类缺口 B1-B9 回归测试（2026-09-05）。

背景：全量管线审计发现 9 个「该归类不被正确归类」的漏网点（B1-B9），
本文件按 TDD 先行固化期望行为：

- B1 migrate_obsidian_vault：plan_one 的 item 补 category（tags 推断）
- B2 videos._finalize_single：单视频 folder 为空时自动路由（含 tags 补作者）
- B3/B4 videos._handle_bilibili_series：系列单集与总览都传 folder
- B5 videos._handle_playlist：playlist 总览传 folder
- B6 land_scys_batch：folder 走统一路由器（不再手拼「生财有术/<领域>」）
- B7 series_maintenance：verify 只查不建（防重建根容器）+ reland 传 folder
- B8 articles.run 场景2：透传 original_url
- B9 articles.autoroute_folder：透传 category（显式参数 > tags 推断）
- routing.category_from_tags：从 tags 提取分类的单一真源纯函数
"""
import inspect
import os
import sys

import pytest

from shared.routing import (
    CATEGORY_SKIP_TAGS,
    MYNOTES_ROOT,
    MONITOR_ROOT,
    category_from_tags,
    resolve_folder,
)


# ---------------------------------------------------------------------------
# A. routing.category_from_tags（分类提取单一真源）
# ---------------------------------------------------------------------------

def test_cft_picks_first_real_tag():
    assert category_from_tags(["生财有术", "AI"]) == "生财有术"


def test_cft_skips_system_tags():
    tags = ["文章总结", "转载", "投资交易", "🔥当日"]
    assert category_from_tags(tags) == "投资交易"


@pytest.mark.parametrize("tags", [[], None, ["转载", "总结", "更早"]])
def test_cft_empty_returns_blank(tags):
    assert category_from_tags(tags) == ""


def test_cft_skip_set_matches_legacy_decision():
    """skip 集合必须与 articles/main.py 2026-08-22 分类修复决策一致。"""
    assert CATEGORY_SKIP_TAGS == {"文章总结", "转载", "总结", "笔记",
                                  "动态速览", "短动态", "🔥当日", "本周", "更早"}


# ---------------------------------------------------------------------------
# B. articles.autoroute_folder 透传 category（B9）
# ---------------------------------------------------------------------------

def test_autoroute_explicit_category_routes_to_category_root():
    from articles.main import autoroute_folder
    folder, tags = autoroute_folder("", "", "", "手贴散文", tags=[], category="AI提效")
    assert folder == f"{MYNOTES_ROOT}/AI提效"
    assert tags == []


def test_autoroute_category_fallback_from_tags():
    """category 缺省时从 tags 推断（首个非系统标签）。"""
    from articles.main import autoroute_folder
    folder, tags = autoroute_folder("", "", "", "手贴散文", tags=["投资交易"])
    assert folder == f"{MYNOTES_ROOT}/投资交易"


def test_autoroute_explicit_folder_wins_over_category():
    from articles.main import autoroute_folder
    folder, tags = autoroute_folder("投资交易/手贴", "", "", "标题", tags=[], category="AI提效")
    assert folder == "投资交易/手贴"
    assert tags == []


def test_save_summarized_from_file_passes_category(tmp_path, monkeypatch):
    """215 行调用点：save_summarized_from_file 必须把 category 传进 autoroute。"""
    import articles.main as am
    f = tmp_path / "sum.md"
    f.write_text("总结文本", encoding="utf-8")
    captured = {}

    def fake_save(content, **kwargs):
        captured.update(kwargs)
        return "note", "x.md"

    monkeypatch.setattr(am, "save_summarized_article", fake_save)
    am.save_summarized_from_file(str(f), original_url="", author="",
                                 original_title="手贴散文", category="AI提效")
    assert captured.get("folder") == f"{MYNOTES_ROOT}/AI提效"


# ---------------------------------------------------------------------------
# C. videos.main.series_folder（B2/B3/B4/B5 的 folder 单一计算点）
# ---------------------------------------------------------------------------

def test_series_folder_explicit_wins():
    from videos.main import series_folder
    assert series_folder({"folder": "X/Y"}, "某UP", "千刀千法", "https://www.bilibili.com/video/BVx") == "X/Y"


def test_series_folder_monitored_up(monkeypatch):
    import shared.routing as rt
    reg = {"笨笨的韭菜": {"platform": "bilibili", "category": "", "aliases": [], "monitored": True}}
    monkeypatch.setattr(rt, "load_account_registry", lambda: reg)
    from videos.main import series_folder
    got = series_folder({}, "笨笨的韭菜", "千刀千法", "https://www.bilibili.com/video/BVx")
    assert got == f"{MONITOR_ROOT}/B站/笨笨的韭菜/千刀千法"


def test_series_folder_unknown_author(monkeypatch):
    import shared.routing as rt
    monkeypatch.setattr(rt, "load_account_registry", lambda: {})
    monkeypatch.setattr(rt, "load_series_patterns", lambda: {})
    from videos.main import series_folder
    got = series_folder({}, "某路人UP", "千刀千法", "https://www.bilibili.com/video/BVx")
    assert got == f"{MYNOTES_ROOT}/作者/某路人UP/千刀千法"


def test_series_folder_no_author():
    import shared.routing as rt
    from videos.main import series_folder
    got = series_folder({}, "", "千刀千法", "https://www.bilibili.com/video/BVx")
    assert got == f"{MYNOTES_ROOT}/系列课/千刀千法"


def test_series_folder_monoreg_hit_also_routes(monkeypatch):
    """resolve_folder 直接喂 series 字段时监控 UP 命中第②级（与 series_folder 同源）。"""
    import shared.routing as rt
    reg = {"笨笨的韭菜": {"platform": "bilibili", "category": "", "aliases": [], "monitored": True}}
    monkeypatch.setattr(rt, "load_account_registry", lambda: reg)
    got = resolve_folder({"author": "笨笨的韭菜", "series": "千刀千法",
                          "title": "千刀千法", "url": "https://www.bilibili.com/video/BVx"})
    assert got == f"{MONITOR_ROOT}/B站/笨笨的韭菜/千刀千法"


# ---------------------------------------------------------------------------
# D. videos._handle_bilibili_series 传 folder（B3/B4）
# ---------------------------------------------------------------------------

_FAKE_SERIES = {
    "kind": "videos",
    "series_title": "测试系列甲",
    "entries": [{"page": 1, "part": "开篇", "segments": [{"t": 0, "text": "hello"}], "title": "第1集"}],
    "author": "某路人UP",
}


@pytest.fixture()
def mock_series_pipeline(monkeypatch, tmp_path):
    """mock 系列管线的 IO 面，捕获 _save_series_note / _generate_series_overview 的 folder。"""
    import articles.main as am
    import shared.routing as rt
    import videos.main as vm

    monkeypatch.setattr(rt, "load_account_registry", lambda: {})
    monkeypatch.setattr(rt, "load_series_patterns", lambda: {})
    monkeypatch.setattr(vm.articles_main, "NOTES_DIR", str(tmp_path))
    monkeypatch.setattr(vm.series_state, "get_pending", lambda title, bases: set(bases))
    monkeypatch.setattr(vm, "_summarize_segments", lambda segs, nt, title, **k: "总结文本")
    monkeypatch.setattr(vm, "_local_write_enabled", lambda: False)

    captured = {"save": [], "ov": []}

    def fake_save(content, series_dir, base, author, url, tags, note_type,
                  obsidian=False, folder=""):
        captured["save"].append({"folder": folder, "base": base})
        return os.path.join(series_dir, f"{base}.md")

    def fake_ov(series_title, series_dir, url, obsidian=False, folder=""):
        captured["ov"].append({"folder": folder})
        return os.path.join(series_dir, "00_系列总览.md")

    monkeypatch.setattr(vm, "_save_series_note", fake_save)
    monkeypatch.setattr(vm, "_generate_series_overview", fake_ov)
    return captured


def test_bili_series_passes_folder_to_episode_and_overview(mock_series_pipeline):
    from videos.main import _handle_bilibili_series
    r = _handle_bilibili_series("https://www.bilibili.com/video/BVtest",
                                {"tags": ["测试系列甲"]}, series=_FAKE_SERIES)
    assert r.get("success") is True
    want = f"{MYNOTES_ROOT}/作者/某路人UP/测试系列甲"
    assert mock_series_pipeline["save"] and mock_series_pipeline["save"][0]["folder"] == want
    assert mock_series_pipeline["ov"] and mock_series_pipeline["ov"][0]["folder"] == want


def test_bili_series_explicit_folder_wins(mock_series_pipeline):
    from videos.main import _handle_bilibili_series
    _handle_bilibili_series("https://www.bilibili.com/video/BVtest",
                            {"tags": ["测试系列甲"], "folder": "手工/目录"}, series=_FAKE_SERIES)
    assert mock_series_pipeline["save"][0]["folder"] == "手工/目录"
    assert mock_series_pipeline["ov"][0]["folder"] == "手工/目录"


# ---------------------------------------------------------------------------
# E. videos._finalize_single 自动路由（B2）
# ---------------------------------------------------------------------------

def test_finalize_single_autoroutes_empty_folder(monkeypatch):
    import videos.main as vm
    captured = {}

    def fake_sas(segments, url, title, author, tags, note_type, force, **kwargs):
        captured["folder"] = kwargs.get("folder", "")
        captured["tags"] = tags
        return ("xx.md", "final", False, None, "structured")

    monkeypatch.setattr(vm, "_summarize_and_save", fake_sas)
    r = vm._finalize_single("标题", ["seg"], "https://www.bilibili.com/video/BVx",
                            {"author": "某路人UP", "tags": []})
    assert r.get("success") is True
    assert captured["folder"] == f"{MYNOTES_ROOT}/作者/某路人UP"
    assert "某路人UP" in captured["tags"]


def test_finalize_single_keeps_explicit_folder(monkeypatch):
    import videos.main as vm
    captured = {}

    def fake_sas(segments, url, title, author, tags, note_type, force, **kwargs):
        captured["folder"] = kwargs.get("folder", "")
        return ("xx.md", "final", False, None, "structured")

    monkeypatch.setattr(vm, "_summarize_and_save", fake_sas)
    vm._finalize_single("标题", ["seg"], "https://www.bilibili.com/video/BVx",
                        {"author": "某路人UP", "tags": [], "folder": "手工/目录"})
    assert captured["folder"] == "手工/目录"


def test_finalize_single_no_author_no_category_goes_inbox(monkeypatch):
    """彻底无归属时兜底【待归类】（不悄悄伪造归属）。"""
    import shared.routing as rt
    import videos.main as vm
    monkeypatch.setattr(rt, "load_account_registry", lambda: {})
    monkeypatch.setattr(rt, "load_series_patterns", lambda: {})
    captured = {}

    def fake_sas(segments, url, title, author, tags, note_type, force, **kwargs):
        captured["folder"] = kwargs.get("folder", "")
        return ("xx.md", "final", False, None, "structured")

    monkeypatch.setattr(vm, "_summarize_and_save", fake_sas)
    vm._finalize_single("标题", ["seg"], "", {"author": "", "tags": []})
    assert captured["folder"] == "【待归类】"


# ---------------------------------------------------------------------------
# F. articles.run 场景2 透传 original_url（B8）
# ---------------------------------------------------------------------------

def test_run_scene2_passes_original_url(tmp_path, monkeypatch):
    import articles.main as am
    import articles.run as arun

    f = tmp_path / "sum.md"
    f.write_text("总结文本", encoding="utf-8")
    captured = {}

    def fake_save(**kwargs):
        captured.update(kwargs)
        return "note", "x.md"

    monkeypatch.setattr(am, "save_summarized_from_file", fake_save)
    monkeypatch.setattr(sys, "argv",
                        ["run.py", str(f), "--url", "https://example.com/post/1",
                         "--author", "某人"])
    rc = arun.main()
    assert rc == 0
    assert captured.get("original_url") == "https://example.com/post/1"


# ---------------------------------------------------------------------------
# G. land_scys_batch 走统一路由器（B6）
# ---------------------------------------------------------------------------

def test_land_scys_folder_from_resolver():
    import land_scys_batch
    src = inspect.getsource(land_scys_batch)
    assert "生财有术/{project}" not in src, "手拼路径必须废除，改走 resolve_folder"
    assert "resolve_folder" in src


def test_land_scys_resolver_output_matches_scys_route():
    """等价性：resolve_folder 对 scys 条目必须产出【监控】/生财有术/<领域>。"""
    got = resolve_folder({"author": "", "url": "https://scys.com/post/123",
                          "scys_domain": "AI产品开发"})
    assert got == f"{MONITOR_ROOT}/生财有术/AI产品开发"


# ---------------------------------------------------------------------------
# H. series_maintenance：verify 只查不建 + reland 传 folder（B7）
# ---------------------------------------------------------------------------

def test_series_maint_no_ensure_series_node():
    """verify 路径绝不能 ensure_series_node（空根会新建根容器=复发事故）。"""
    import series_maintenance
    src = inspect.getsource(series_maintenance)
    assert "ensure_series_node" not in src


def test_series_maint_reland_passes_folder():
    import series_maintenance
    src = inspect.getsource(series_maintenance.cmd_reland)
    assert "folder=folder" in src


def test_series_maint_verify_and_regen_accept_author():
    import series_maintenance
    v_src = inspect.getsource(series_maintenance.cmd_verify)
    r_src = inspect.getsource(series_maintenance.cmd_regen_overview)
    assert "args.author" in v_src
    assert "args.author" in r_src


# ---------------------------------------------------------------------------
# I. migrate_obsidian_vault：item 补 category（B1）
# ---------------------------------------------------------------------------

def test_migrate_item_has_category_from_tags():
    import migrate_obsidian_vault as mv
    src = inspect.getsource(mv.plan_one)
    assert '"category"' in src
    assert "category_from_tags" in src


def test_migrate_category_routes_legacy_loose_file():
    """端到端语义：无作者但有标签的散落文件应落【我的总结】/<分类> 而非【待归类】。"""
    item = {"url": "", "author": "", "title": "散落笔记", "tags": ["AI提效"],
            "category": category_from_tags(["AI提效"])}
    assert resolve_folder(item) == f"{MYNOTES_ROOT}/AI提效"
