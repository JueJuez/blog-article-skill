"""迁移内容门禁测试（2026-09-07 创建，2026-09-08 转 v3 重抓方案）。

背景：飞书历史存量存在「标题错位/重复落盘/来源块重复/链接冲突」，迁移是忠实
搬运会原样带过来。v3 语义（用户拍板）：**检测出清单、重抓替换**，不做正文级修复——
  dedup(正文指纹去重) → fm_sync(fm title=stem) → scan(异常检测+链接指纹分组)
  → queue(重抓队列，只生成不执行) → verify(三层核验)。
原 realign(H1 改名)已删除：H1 不可靠（有的缺、有的是总结生成、有的是原标题），
串位文件直接进重抓队列，重抓产物文件名天然规范。链接指纹分组解决用户指出的
盲区：同链接不同标题不同正文，正文指纹识别不了，链接才是唯一权威值。
"""
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import scripts.migrate_gate as mg


def _write(vault: Path, rel: str, text: str) -> Path:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="")
    return p


BODY_BYD = "# 第43集 比亚迪：460万辆和7000块利润\n\n正文内容比亚迪。" * 20
BODY_LONZA = "# 第44集 阿尔卑斯的小溪——龙沙\n\n正文内容龙沙。" * 20
BODY_NINGBO = "# 第03集 宁波银行投资价值分析\n\n正文内容宁波银行。" * 20


def _fm(title: str) -> str:
    return f"---\ntitle: {title}\nsource: x\n---\n"


class TestSplitFm:
    def test_with_fm(self):
        fm, body = mg.split_fm(_fm("a") + "正文")
        assert "title: a" in fm
        assert body == "正文"

    def test_without_fm(self):
        fm, body = mg.split_fm("没有frontmatter的正文")
        assert fm == ""
        assert body == "没有frontmatter的正文"

    def test_unclosed_fm_treated_as_body(self):
        # 异常场景: 只有开头的 --- 无闭合 → 整体视为正文, 不误剥
        fm, body = mg.split_fm("---\ntitle: a\n正文没有闭合")
        assert fm == ""
        assert "title: a" in body


class TestEpisode:
    def test_h1_episode(self):
        assert mg.h1_episode(BODY_BYD) == "43"

    def test_h1_episode_none(self):
        assert mg.h1_episode("# 没有集数的标题\n\n正文") is None

    def test_stem_episode(self):
        assert mg.stem_episode("第43集_比亚迪") == "43"

    def test_stem_episode_none(self):
        assert mg.stem_episode("20260519_日更标题") is None


class TestUnquote:
    """YAML 值剥引号：迁移产物 fm title 统一写法是 title: "..."，比较前必须剥掉。"""

    def test_double_quotes_stripped(self):
        assert mg._unquote('"第43集_比亚迪"') == "第43集_比亚迪"

    def test_unquoted_passthrough(self):
        assert mg._unquote("第43集_比亚迪") == "第43集_比亚迪"

    def test_mismatched_quotes_passthrough(self):
        # 异常场景: 引号不成对 → 原样返回不误剥
        assert mg._unquote('"第43集_比亚迪') == '"第43集_比亚迪'


class TestDedup:
    def test_same_body_keeps_one_moves_other(self, tmp_path):
        a = _write(tmp_path, "d/第43集_比亚迪.md", _fm("第43集_比亚迪") + BODY_BYD)
        b = _write(tmp_path, "d/第43集_比亚迪-2.md", _fm("第43集_比亚迪-2") + BODY_BYD)
        rep = mg.dedup_step(str(tmp_path), str(tmp_path.parent / "archive"))
        assert len(rep["groups"]) == 1 and len(rep["groups"][0]) == 2
        assert rep["moved"] == [str(b)]
        assert a.exists() and not b.exists()

    def test_diff_body_keeps_both(self, tmp_path):
        _write(tmp_path, "d/第43集_比亚迪.md", _fm("t1") + BODY_BYD)
        _write(tmp_path, "d/第44集_龙沙.md", _fm("t2") + BODY_LONZA)
        rep = mg.dedup_step(str(tmp_path), str(tmp_path.parent / "archive"))
        assert rep["moved"] == []
        assert len(list((tmp_path / "d").glob("*.md"))) == 2

    def test_keeps_suffixless_preferred(self, tmp_path):
        # 保留策略: 无 -N 后缀者优先留下
        _write(tmp_path, "d/x-2.md", _fm("x") + BODY_BYD)
        _write(tmp_path, "d/x.md", _fm("x") + BODY_BYD)
        rep = mg.dedup_step(str(tmp_path), str(tmp_path.parent / "archive"))
        assert (tmp_path / "d" / "x.md").exists()
        assert not (tmp_path / "d" / "x-2.md").exists()

    def test_empty_vault(self, tmp_path):
        rep = mg.dedup_step(str(tmp_path), str(tmp_path.parent / "archive"))
        assert rep["moved"] == [] and rep["groups"] == []

    def test_crlf_normalization_counts_as_same(self, tmp_path):
        # 边界: 同正文仅换行符差异(CRLF vs LF) → 视为同一篇
        _write(tmp_path, "d/a.md", _fm("a") + BODY_BYD)
        p = _write(tmp_path, "d/b.md", _fm("b") + BODY_BYD.replace("\n", "\r\n"))
        rep = mg.dedup_step(str(tmp_path), str(tmp_path.parent / "archive"))
        assert rep["moved"] == [str(p)]


def _mig_fm(title: str = "t", url: str = "") -> str:
    """迁移产物 frontmatter 模板：migrated_from 是迁移文标志，source_url 是权威链接。"""
    lines = ["---", f"title: {title}", "source: feishu", "migrated_from: feishu-to-obsidian"]
    if url:
        lines.append(f"source_url: {url}")
    lines.append("---")
    return "\n".join(lines) + "\n"


SRC_BLOCK = "**作者**：素心 | **来源链接**：[原文标题](https://www.bilibili.com/video/BV1dFbv6wEpn)"


class TestNormUrl:
    """链接归一化：剥 query/fragment/尾斜杠；BV 号大小写敏感，绝不改写。"""

    def test_strips_query(self):
        assert mg._norm_url("https://a.com/x?p=1") == "https://a.com/x"

    def test_strips_fragment(self):
        assert mg._norm_url("https://a.com/x#sec") == "https://a.com/x"

    def test_strips_trailing_slash(self):
        assert mg._norm_url("https://a.com/x/") == "https://a.com/x"

    def test_bv_case_preserved(self):
        # 核心边界: BV 号大小写敏感，归一化不能 lower() 或改写大小写
        u = "https://www.bilibili.com/video/BV1dFbv6wEpn"
        assert mg._norm_url(u + "?p=2") == u

    def test_query_and_slash_combined(self):
        assert mg._norm_url("https://a.com/x/?p=1") == "https://a.com/x"

    def test_empty_passthrough(self):
        # 异常场景: 空/纯空白不炸
        assert mg._norm_url("") == ""
        assert mg._norm_url("   ") == ""


class TestExtractUrls:
    def test_from_md_link(self):
        assert mg._extract_urls("[标题](https://a.com/x)") == ["https://a.com/x"]

    def test_plain_url_in_text(self):
        assert mg._extract_urls("看这个 https://a.com/x 很有用") == ["https://a.com/x"]

    def test_dedup_after_norm(self):
        # ?p=1 与无参数归一后同链接 → 去重
        assert mg._extract_urls("https://a.com/x?p=1 https://a.com/x") == ["https://a.com/x"]

    def test_order_preserved(self):
        urls = mg._extract_urls("先 https://a.com/1 后 https://b.com/2")
        assert urls == ["https://a.com/1", "https://b.com/2"]

    def test_no_url(self):
        # 边界: 无链接返回空列表
        assert mg._extract_urls("纯文字没有链接") == []


class TestScan:
    """scan_step：只读检测 5 类异常，输出 issues + 链接指纹分组 + 分类计数。"""

    def test_clean_migrated_note_no_issues(self, tmp_path):
        url = "https://www.bilibili.com/video/BV1dFbv6wEpn"
        _write(tmp_path, "AI 总结笔记/d/第43集_比亚迪.md", _mig_fm("第43集_比亚迪", url) + BODY_BYD + "\n" + SRC_BLOCK + "\n")
        rep = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        assert rep["issues"] == []
        assert rep["link_groups"] == []

    def test_dup_source_blocks(self, tmp_path):
        _write(tmp_path, "AI 总结笔记/d/a.md", _mig_fm() + BODY_BYD + "\n" + SRC_BLOCK + "\n" + SRC_BLOCK + "\n")
        rep = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        assert rep["counts"]["dup_source_blocks"] == 1
        assert "dup_source_blocks" in rep["issues"][0]["kinds"]

    def test_residual_title_line(self, tmp_path):
        _write(tmp_path, "AI 总结笔记/d/a.md", _mig_fm() + BODY_BYD + "\n<title>残留标题</title>\n")
        rep = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        assert "residual_lines" in rep["issues"][0]["kinds"]

    def test_residual_source_url_heading(self, tmp_path):
        _write(tmp_path, "AI 总结笔记/d/a.md", _mig_fm() + BODY_BYD + "\n## source_url: https://a.com/x\n")
        rep = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        assert "residual_lines" in rep["issues"][0]["kinds"]

    def test_residual_bare_keys(self, tmp_path):
        _write(tmp_path, "AI 总结笔记/d/a.md", _mig_fm() + BODY_BYD + "\nfreshness: 2026-09-01\npublished_at: 2026-09-01\n")
        rep = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        assert "residual_lines" in rep["issues"][0]["kinds"]

    def test_missing_fm_url_only_for_migrated(self, tmp_path):
        # 范围语义: 缺 source_url 只对迁移文(migrated_from)报警
        _write(tmp_path, "AI 总结笔记/d/a.md", _mig_fm() + BODY_BYD)
        rep = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        assert "missing_fm_url" in rep["issues"][0]["kinds"]

    def test_non_migrated_missing_url_clean(self, tmp_path):
        # 非迁移文没有 fm source_url 是常态，不算异常
        _write(tmp_path, "AI 总结笔记/d/a.md", _fm("t") + BODY_BYD)
        rep = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        assert rep["issues"] == []

    def test_multi_urls_conflict(self, tmp_path):
        # fm 权威链接与正文链接不同源 → multi_urls；fm_url 单独记录供裁决
        _write(tmp_path, "AI 总结笔记/d/a.md", _mig_fm("t", "https://a.com/x") + BODY_BYD + "\nhttps://b.com/y\n")
        rep = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        assert "multi_urls" in rep["issues"][0]["kinds"]
        assert rep["issues"][0]["fm_url"] == "https://a.com/x"

    def test_episode_mismatch(self, tmp_path):
        # 文件名43集、内容44集 → 串位（用户拍板: 不改名，直接进重抓队列）
        _write(tmp_path, "AI 总结笔记/d/第43集_比亚迪.md", _mig_fm("t") + BODY_LONZA)
        rep = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        assert "episode_mismatch" in rep["issues"][0]["kinds"]

    def test_missing_h1_not_issue(self, tmp_path):
        # 边界: 无 H1 不算异常——不为它单独花一次重抓
        _write(tmp_path, "AI 总结笔记/d/第43集_比亚迪.md", _mig_fm("第43集_比亚迪", "https://a.com/x") + "正文没有H1。" * 20)
        rep = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        assert rep["issues"] == []

    def test_scan_never_modifies_files(self, tmp_path):
        # scan 是纯只读检测
        p = _write(tmp_path, "AI 总结笔记/d/a.md", _mig_fm() + BODY_BYD + "\n" + SRC_BLOCK + "\n" + SRC_BLOCK + "\n")
        before = p.read_text(encoding="utf-8")
        mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        assert p.read_text(encoding="utf-8") == before


class TestLinkGroups:
    """链接指纹分组：同 main_url 的文件聚组——解决「同链接不同标题不同正文」盲区。"""

    def test_same_url_diff_body_grouped(self, tmp_path):
        # 盲区核心: 正文指纹不同识别不了，链接才是唯一权威值
        _write(tmp_path, "AI 总结笔记/d/第02集_甲.md", _mig_fm("标题甲", "https://a.com/x") + BODY_BYD)
        _write(tmp_path, "AI 总结笔记/d/第02集_乙.md", _mig_fm("标题乙", "https://a.com/x") + BODY_LONZA)
        rep = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        assert len(rep["link_groups"]) == 1
        assert len(rep["link_groups"][0]["paths"]) == 2
        assert rep["counts"]["dup_link_groups"] == 1

    def test_query_variant_same_group(self, tmp_path):
        _write(tmp_path, "AI 总结笔记/d/a.md", _mig_fm("t1", "https://a.com/x?p=1") + BODY_BYD)
        _write(tmp_path, "AI 总结笔记/d/b.md", _mig_fm("t2", "https://a.com/x") + BODY_LONZA)
        rep = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        assert len(rep["link_groups"]) == 1

    def test_url_from_body_when_fm_missing(self, tmp_path):
        # fm 缺 source_url 但正文有链接 → 仍按正文链接分组
        _write(tmp_path, "AI 总结笔记/d/a.md", _mig_fm("t1") + BODY_BYD + "\n" + SRC_BLOCK + "\n")
        _write(tmp_path, "AI 总结笔记/d/b.md", _mig_fm("t2", "https://www.bilibili.com/video/BV1dFbv6wEpn") + BODY_LONZA)
        rep = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        assert len(rep["link_groups"]) == 1

    def test_diff_urls_not_grouped(self, tmp_path):
        _write(tmp_path, "AI 总结笔记/d/a.md", _mig_fm("t1", "https://a.com/1") + BODY_BYD)
        _write(tmp_path, "AI 总结笔记/d/b.md", _mig_fm("t2", "https://b.com/2") + BODY_LONZA)
        rep = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        assert rep["link_groups"] == []

    def test_no_url_untracked(self, tmp_path):
        # 无链接文件不进链接分组（走 manual_no_url 队列）
        _write(tmp_path, "AI 总结笔记/d/a.md", _mig_fm("t1") + BODY_BYD)
        _write(tmp_path, "AI 总结笔记/d/b.md", _mig_fm("t2") + BODY_LONZA)
        rep = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        assert rep["link_groups"] == []


class TestRegenQueue:
    """regen_queue_from_scan：把 scan 结果折叠成重抓队列（只生成不执行）。"""

    def test_group_merges_into_one_entry(self, tmp_path):
        a = _write(tmp_path, "AI 总结笔记/d/a.md", _mig_fm("甲", "https://a.com/x") + BODY_BYD)
        b = _write(tmp_path, "AI 总结笔记/d/b.md", _mig_fm("乙", "https://a.com/x") + BODY_LONZA)
        scan = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        queue = mg.regen_queue_from_scan(str(tmp_path), scan)
        assert len(queue) == 1
        assert queue[0]["url"] == "https://a.com/x"
        assert set(queue[0]["old_paths"]) == {str(a), str(b)}
        assert queue[0]["old_titles"] == ["甲", "乙"]

    def test_url_authority_fm_first(self, tmp_path):
        # 链接权威链: fm source_url 优先，正文链接兜底
        _write(tmp_path, "AI 总结笔记/d/a.md", _mig_fm("t", "https://a.com/fm") + BODY_BYD + "\nhttps://b.com/body\n")
        scan = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        queue = mg.regen_queue_from_scan(str(tmp_path), scan)
        assert queue[0]["url"] == "https://a.com/fm"

    def test_bili_link_is_video_kind(self, tmp_path):
        # bilibili 视频链接（含 b23.tv 短链）→ bili_video，走视频字幕管线；
        # fm+正文双链接构成 multi_urls 异常，文件才进队列（干净文件不重抓）
        _write(
            tmp_path,
            "AI 总结笔记/d/a.md",
            _mig_fm("t1", "https://www.bilibili.com/video/BV1dFbv6wEpn") + BODY_BYD + "\nhttps://x.com/1\n",
        )
        _write(
            tmp_path,
            "AI 总结笔记/d/b.md",
            _mig_fm("t2", "https://b23.tv/abc123") + BODY_LONZA + "\nhttps://x.com/2\n",
        )
        scan = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        queue = mg.regen_queue_from_scan(str(tmp_path), scan)
        assert {q["kind"] for q in queue} == {"bili_video"}

    def test_article_kind(self, tmp_path):
        # fm 博客链接 + 正文另一链接 → multi_urls 异常进队列，kind 按权威链接（fm 优先）判 article
        _write(
            tmp_path,
            "AI 总结笔记/d/a.md",
            _mig_fm("t", "https://blog.example.com/post/1") + BODY_BYD + "\nhttps://x.com/1\n",
        )
        scan = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        queue = mg.regen_queue_from_scan(str(tmp_path), scan)
        assert queue[0]["kind"] == "article"

    def test_no_url_goes_manual(self, tmp_path):
        # 无链接异常文件 → manual_no_url，等人工/重抓工具补链接
        _write(tmp_path, "AI 总结笔记/d/a.md", _mig_fm() + BODY_BYD)
        scan = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        queue = mg.regen_queue_from_scan(str(tmp_path), scan)
        assert len(queue) == 1
        assert queue[0]["kind"] == "manual_no_url"
        assert queue[0]["url"] is None

    def test_old_titles_captured(self, tmp_path):
        # old_titles 取 fm title 剥引号——为 B站重传「标题相似度匹配」留数据（URL 会变不能只凭 URL）
        _write(
            tmp_path,
            "AI 总结笔记/d/a.md",
            _mig_fm('"真实标题"', "https://a.com/x") + BODY_BYD + "\nhttps://x.com/1\n",
        )
        scan = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        queue = mg.regen_queue_from_scan(str(tmp_path), scan)
        assert queue[0]["old_titles"] == ["真实标题"]

    def test_folder_hint_is_vault_relative_posix(self, tmp_path):
        _write(
            tmp_path,
            "AI 总结笔记/d/a.md",
            _mig_fm("t", "https://a.com/x") + BODY_BYD + "\nhttps://x.com/1\n",
        )
        scan = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        queue = mg.regen_queue_from_scan(str(tmp_path), scan)
        assert queue[0]["folder_hint"] == "AI 总结笔记/d"

    def test_single_issue_file_gets_entry(self, tmp_path):
        # 非组的单 issue 文件也进队列
        _write(tmp_path, "AI 总结笔记/d/第43集_比亚迪.md", _mig_fm("t") + BODY_LONZA)
        scan = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        queue = mg.regen_queue_from_scan(str(tmp_path), scan)
        assert len(queue) == 1
        assert queue[0]["kind"] == "manual_no_url"
        assert queue[0]["url"] is None


class TestFmSync:
    def test_title_synced_to_stem(self, tmp_path):
        p = _write(tmp_path, "d/第43集_比亚迪.md", _fm("旧标题") + BODY_BYD)
        rep = mg.fm_sync_step(str(tmp_path), apply=True)
        assert rep["synced"] == [str(p)]
        text = p.read_text(encoding="utf-8")
        assert "title: 第43集_比亚迪.md" in text or f"title: {p.stem}" in text

    def test_already_same_untouched(self, tmp_path):
        stem = "第43集_比亚迪"
        p = _write(tmp_path, f"d/{stem}.md", _fm(stem) + BODY_BYD)
        rep = mg.fm_sync_step(str(tmp_path), apply=True)
        assert rep["synced"] == []

    def test_quoted_title_equal_stem_untouched(self, tmp_path):
        # 回归(2026-09-08 真实 vault): fm title 带引号且值==stem → 仅引号差异不算不一致
        stem = "第43集_比亚迪"
        p = _write(tmp_path, f"d/{stem}.md", _fm(f'"{stem}"') + BODY_BYD)
        rep = mg.fm_sync_step(str(tmp_path), apply=True)
        assert rep["synced"] == []
        assert f'title: "{stem}"' in p.read_text(encoding="utf-8")

    def test_quoted_title_real_diff_keeps_quote_style(self, tmp_path):
        # 带引号的 fm title 真不一致 → 同步后仍保留引号风格(YAML 安全, 不裸写特殊字符)
        p = _write(tmp_path, "d/第43集_比亚迪.md", _fm('"旧标题"') + BODY_BYD)
        rep = mg.fm_sync_step(str(tmp_path), apply=True)
        assert rep["synced"] == [str(p)]
        assert 'title: "第43集_比亚迪"' in p.read_text(encoding="utf-8")

    def test_no_fm_untouched(self, tmp_path):
        _write(tmp_path, "d/无fm.md", "# 正文而已\n")
        rep = mg.fm_sync_step(str(tmp_path), apply=True)
        assert rep["synced"] == []

    def test_non_episode_stem_untouched(self, tmp_path):
        # 边界: 无集数标记的文件名(如 20260519_日更)不动——fm title 保留原始标题(历史命名规范)
        p = _write(tmp_path, "d/20260519_日更标题.md", _fm("真实标题") + BODY_BYD)
        rep = mg.fm_sync_step(str(tmp_path), apply=True)
        assert rep["synced"] == []
        assert "title: 真实标题" in p.read_text(encoding="utf-8")


class TestVerify:
    def test_all_good_passes(self, tmp_path):
        stem = "第43集_比亚迪"
        _write(tmp_path, f"d/{stem}.md", _fm(stem) + BODY_BYD)
        rep = mg.verify_step(str(tmp_path))
        assert rep["passed"] is True and rep["failed"] == []

    def test_episode_mismatch_fails(self, tmp_path):
        _write(tmp_path, "d/第43集_比亚迪.md", _fm("t") + BODY_LONZA)
        rep = mg.verify_step(str(tmp_path))
        assert rep["passed"] is False
        assert any("集数" in f["reason"] for f in rep["failed"])

    def test_empty_body_fails(self, tmp_path):
        stem = "第43集_比亚迪"
        _write(tmp_path, f"d/{stem}.md", _fm(stem))
        rep = mg.verify_step(str(tmp_path))
        assert rep["passed"] is False

    def test_non_episode_fm_mismatch_not_flagged(self, tmp_path):
        # 边界: 无集数标记的文件名 fm title≠stem 不算失败(历史命名规范, fm title=原始标题)
        _write(tmp_path, "d/20260519_日更标题.md", _fm("真实标题") + BODY_BYD)
        rep = mg.verify_step(str(tmp_path))
        assert rep["passed"] is True

    def test_quoted_title_equal_stem_passes(self, tmp_path):
        # 回归(2026-09-08 真实 vault): fm title 带引号但值与 stem 一致 → 不算失败(225 篇误报根因)
        stem = "第43集_比亚迪"
        _write(tmp_path, f"d/{stem}.md", _fm(f'"{stem}"') + BODY_BYD)
        rep = mg.verify_step(str(tmp_path))
        assert rep["passed"] is True and rep["failed"] == []


class TestRunGate:
    def test_dry_run_leaves_vault_unchanged(self, tmp_path):
        _write(tmp_path, "d/第43集_比亚迪-2.md", _fm("t") + BODY_LONZA)
        _write(tmp_path, "d/第43集_比亚迪.md", _fm("t") + BODY_BYD)
        before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
        rep = mg.run_gate(str(tmp_path), apply=False, archive_dir=str(tmp_path.parent / "arch"))
        assert rep["passed"] is False
        after = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
        assert before == after

    def test_apply_dedups_but_mismatch_stays_for_regen(self, tmp_path):
        # v3 端到端: 同文重复照旧归档移出；串位文件不再按 H1 改名（用户拍板全部重抓替换），
        # verify 保持红，串位文件进重抓队列，等重抓成功后才删旧文
        _write(tmp_path, "d/第43集_比亚迪-2.md", _fm("t") + BODY_BYD)
        keep = _write(tmp_path, "d/第43集_比亚迪.md", _fm("t") + BODY_BYD)
        _write(tmp_path, "d/第44集_龙沙.md", _fm("t") + BODY_NINGBO)
        rep = mg.run_gate(str(tmp_path), apply=True, archive_dir=str(tmp_path.parent / "arch"))
        assert keep.exists() and not (tmp_path / "d" / "第43集_比亚迪-2.md").exists()
        assert (tmp_path / "d" / "第44集_龙沙.md").exists()
        assert rep["passed"] is False
        assert rep["scan"]["counts"]["episode_mismatch"] == 1
        assert len(rep["queue"]) == 1
        assert rep["queue"][0]["old_paths"] == [str(tmp_path / "d" / "第44集_龙沙.md")]
        assert rep["queue"][0]["kind"] == "manual_no_url"


class TestScope:
    """范围守卫：门禁只扫迁移管线目录（roots），绝不碰 personal/开源项目 等非管线内容。"""

    def test_verify_scope_limits_to_pipeline_dir(self, tmp_path):
        # 管线目录外个人笔记（空正文）不报告、不误伤
        _write(tmp_path, "AI 总结笔记/d/第43集_比亚迪.md", _fm("第43集_比亚迪") + BODY_BYD)
        _write(tmp_path, "personal/我的笔记.md", "   \n")
        rep = mg.verify_step(str(tmp_path), roots=("AI 总结笔记",))
        assert rep["passed"] is True and rep["failed"] == []

    def test_verify_without_roots_scans_all(self, tmp_path):
        # 默认 roots=None 保持全扫描（向后兼容既有直接调用）
        _write(tmp_path, "personal/我的笔记.md", "   \n")
        rep = mg.verify_step(str(tmp_path))
        assert rep["passed"] is False

    def test_dedup_scope_skips_outside_roots(self, tmp_path):
        # dedup 不得把管线目录外的用户笔记当重复移走
        _write(tmp_path, "AI 总结笔记/d/a.md", _fm("a") + BODY_BYD)
        _write(tmp_path, "personal/同文.md", _fm("p") + BODY_BYD)
        _write(tmp_path, "personal/同文-2.md", _fm("p") + BODY_BYD)
        rep = mg.dedup_step(str(tmp_path), str(tmp_path / "_arch"), roots=("AI 总结笔记",))
        assert rep["moved"] == []
        assert (tmp_path / "personal" / "同文.md").exists()
        assert (tmp_path / "personal" / "同文-2.md").exists()

    def test_scan_scope_skips_outside_roots(self, tmp_path):
        # scan 不报告管线目录外的异常文件，也不把它放进重抓队列
        _write(tmp_path, "personal/第43集_比亚迪.md", _mig_fm() + BODY_BYD + "\n" + SRC_BLOCK + "\n" + SRC_BLOCK + "\n")
        rep = mg.scan_step(str(tmp_path), roots=("AI 总结笔记",))
        assert rep["issues"] == [] and rep["link_groups"] == []

    def test_fm_sync_scope_skips_outside_roots(self, tmp_path):
        p = _write(tmp_path, "personal/第43集_比亚迪.md", _fm("旧标题") + BODY_BYD)
        rep = mg.fm_sync_step(str(tmp_path), apply=True, roots=("AI 总结笔记",))
        assert rep["synced"] == []
        assert "title: 旧标题" in p.read_text(encoding="utf-8")

    def test_run_gate_passes_roots_through(self, tmp_path):
        _write(tmp_path, "personal/空.md", "\n")
        rep = mg.run_gate(
            str(tmp_path), apply=False, archive_dir=str(tmp_path / "_arch"), roots=("AI 总结笔记",)
        )
        assert rep["verify"]["passed"] is True

    def test_scope_root_missing_ok(self, tmp_path):
        # 异常场景: 指定的管线目录不存在 → 空扫描不报错
        rep = mg.verify_step(str(tmp_path), roots=("不存在的目录",))
        assert rep["passed"] is True
