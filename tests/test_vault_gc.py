"""tests/test_vault_gc.py — gc 淘汰门槛 + journal（PLAN-20260906 任务4）。

拍板背景（P0-1，B 方案）：gc 不清本地成品，只清中间产物（raw/transcripts）。
门槛矩阵（DECISION-20260907 + PLAN §7 P1-6/P2-2/P2-5/P2-6）：
- 系列分片 notes/<子目录>/*_raw.md：同名 .body.md 存在 → 可清；缺失 → 留。
- 文章 raw notes/_raw_*.md：标题命中登记表且命中记录 URL 不含不可再生域（默认
  mp.weixin.qq.com，.env GC_NONREGEN_HOSTS 覆盖）→ 可清；公众号等不可再生来源永久留。
- transcripts/BV*.md：BV 号出现在登记表任一 source_url → 可清；非 BV 命名/未命中 → 留。
- 淘汰动作全部进 .cache/sync_journal.jsonl；>7 天被动补跑判定读 journal 非 mtime。
"""
import importlib.util
import json
import os
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VL_PATH = os.path.join(_ROOT, "scripts", "vault_lifecycle.py")
_spec = importlib.util.spec_from_file_location("vault_lifecycle", _VL_PATH)
vl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vl)


def _rec(url: str = "", title: str = "") -> dict:
    return {
        "source_url": url, "title": title, "filename": "",
        "feishu_link": "", "obsidian_link": "", "ts": 0,
    }


def _raw_header(title: str) -> str:
    return (f"> 原始文章内容（自动暂存）\n> 标题：{title}\n"
            f"> 时间：2026-08-23 20:52:13\n\n---\n\n正文内容")


def _mk_series(notes_dir: str, series: str, ep: str, body: bool) -> str:
    d = os.path.join(notes_dir, series)
    os.makedirs(d, exist_ok=True)
    raw = os.path.join(d, f"{ep}_raw.md")
    with open(raw, "w", encoding="utf-8") as f:
        f.write("字幕内容")
    if body:
        with open(os.path.join(d, f"{ep}.body.md"), "w", encoding="utf-8") as f:
            f.write("总结")
    return raw


def _abs_paths(targets: list) -> set:
    return {os.path.abspath(t["path"]) for t in targets}


# ── journal 基础设施（P2-6：判定读 journal 而非文件 mtime）──────────────────


class TestJournal:
    def _patch(self, tmp_path, monkeypatch) -> str:
        jf = os.path.join(str(tmp_path), ".cache", "sync_journal.jsonl")
        monkeypatch.setattr(vl, "_JOURNAL_FILE", jf)
        return jf

    def test_append_and_last_run(self, tmp_path, monkeypatch):
        jf = self._patch(tmp_path, monkeypatch)
        vl.journal_append("gc_run", evicted=0)
        time.sleep(0.01)
        vl.journal_append("gc_run", evicted=3)
        assert vl.journal_last_run() is not None
        lines = [json.loads(x) for x in open(jf, encoding="utf-8") if x.strip()]
        assert vl.journal_last_run() == lines[-1]["ts"]

    def test_last_run_none_when_missing(self, tmp_path, monkeypatch):
        self._patch(tmp_path, monkeypatch)
        assert vl.journal_last_run() is None

    def test_last_run_skips_corrupt_lines(self, tmp_path, monkeypatch):
        jf = self._patch(tmp_path, monkeypatch)
        os.makedirs(os.path.dirname(jf), exist_ok=True)
        with open(jf, "w", encoding="utf-8") as f:
            f.write("{corrupt\n")
            f.write(json.dumps({"ts": 100, "action": "gc_run"}, ensure_ascii=False) + "\n")
        assert vl.journal_last_run() == 100

    def test_should_run_true_when_never(self, tmp_path, monkeypatch):
        self._patch(tmp_path, monkeypatch)
        assert vl.should_run_gc(days=7) is True

    def test_should_run_window(self, tmp_path, monkeypatch):
        self._patch(tmp_path, monkeypatch)
        now = time.time()
        vl.journal_append("gc_run", evicted=0, ts=int(now - 8 * 86400))
        assert vl.should_run_gc(days=7, now=now) is True
        vl.journal_append("gc_run", evicted=0, ts=int(now - 1 * 86400))
        assert vl.should_run_gc(days=7, now=now) is False


# ── 系列分片门槛（P1-6：按 .body.md 存在性判定）────────────────────────────


class TestCollectSeriesRaws:
    def test_body_present_collected(self, tmp_path):
        raw = _mk_series(str(tmp_path), "系列A", "第01集_老司机", body=True)
        targets = vl.collect_gc_targets(str(tmp_path), os.path.join(str(tmp_path), "t"), {})
        assert _abs_paths(targets) == {os.path.abspath(raw)}
        assert targets[0]["kind"] == "series_raw"

    def test_body_missing_kept(self, tmp_path):
        _mk_series(str(tmp_path), "系列A", "第02集_待总结", body=False)
        assert vl.collect_gc_targets(str(tmp_path), os.path.join(str(tmp_path), "t"), {}) == []

    def test_body_name_mismatch_kept(self, tmp_path):
        _mk_series(str(tmp_path), "系列A", "第01集_老司机", body=False)
        with open(os.path.join(str(tmp_path), "系列A", "第1集_老司机.body.md"), "w",
                  encoding="utf-8") as f:
            f.write("不同名 body")
        assert vl.collect_gc_targets(str(tmp_path), os.path.join(str(tmp_path), "t"), {}) == []

    def test_underscore_subdir_exempt(self, tmp_path):
        # scys 归档（notes/_scraped/scys/）等 _ 开头子目录永不扫描（P2-5 豁免落规则）
        raw = _mk_series(str(tmp_path), "_scraped", "x", body=True)
        assert os.path.exists(raw)
        assert vl.collect_gc_targets(str(tmp_path), os.path.join(str(tmp_path), "t"), {}) == []


# ── 文章 raw 门槛（登记表命中 + 不可再生域豁免）────────────────────────────


class TestCollectArticleRaws:
    def _mk_article_raw(self, notes_dir: str, title: str) -> str:
        p = os.path.join(notes_dir, "_raw_test-20260823-205213.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(_raw_header(title))
        return p

    def test_matched_blog_url_collected(self, tmp_path):
        raw = self._mk_article_raw(str(tmp_path), "15万，绷不住了玄学按摩")
        index = {"h1": _rec("https://example.com/post/1", "15万，绷不住了玄学按摩")}
        targets = vl.collect_gc_targets(str(tmp_path), os.path.join(str(tmp_path), "t"), index)
        assert _abs_paths(targets) == {os.path.abspath(raw)}
        assert targets[0]["kind"] == "article_raw"

    def test_matched_wechat_url_kept(self, tmp_path):
        # 命中登记表但来源是公众号（不可再生域）→ 永久留（PLAN 默认决策 1）
        self._mk_article_raw(str(tmp_path), "深度：跨境出海的十个认知")
        index = {"h2": _rec("https://mp.weixin.qq.com/s/abc", "深度：跨境出海的十个认知")}
        assert vl.collect_gc_targets(str(tmp_path), os.path.join(str(tmp_path), "t"), index) == []

    def test_unmatched_kept(self, tmp_path):
        # 未总结完一直留（门槛主语义）
        self._mk_article_raw(str(tmp_path), "还没被总结的文章")
        assert vl.collect_gc_targets(str(tmp_path), os.path.join(str(tmp_path), "t"), {}) == []

    def test_short_title_kept(self, tmp_path):
        # 防误清：normalize 后过短的标题不参与匹配（如 _raw_1-*.md 的「1」）
        self._mk_article_raw(str(tmp_path), "1")
        index = {"h3": _rec("https://example.com/x", "1")}
        assert vl.collect_gc_targets(str(tmp_path), os.path.join(str(tmp_path), "t"), index) == []

    def test_filename_fallback_title(self, tmp_path):
        # header 缺标题行时从文件名提取（_raw_<标题>-<时间戳>.md）
        p = os.path.join(str(tmp_path), "_raw_我的博客文章标题-20260823-205213.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("正文（无 header）")
        index = {"h4": _rec("https://example.com/blog/a", "我的博客文章标题")}
        targets = vl.collect_gc_targets(str(tmp_path), os.path.join(str(tmp_path), "t"), index)
        assert _abs_paths(targets) == {os.path.abspath(p)}


# ── transcripts 门槛（BV 号 ↔ 登记表 source_url；P2-2 可再生来源正常淘汰）──


class TestCollectTranscripts:
    def _mk_transcript(self, trans_dir: str, name: str) -> str:
        p = os.path.join(trans_dir, name)
        os.makedirs(trans_dir, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("字幕")
        return p

    def test_bv_matched_collected(self, tmp_path):
        trans = os.path.join(str(tmp_path), "t")
        p = self._mk_transcript(trans, "BV18DwszGE8E.md")
        index = {"h1": _rec("https://www.bilibili.com/video/BV18DwszGE8E", "某视频")}
        targets = vl.collect_gc_targets(str(tmp_path), trans, index)
        assert _abs_paths(targets) == {os.path.abspath(p)}
        assert targets[0]["kind"] == "transcript"

    def test_bv_unmatched_kept(self, tmp_path):
        trans = os.path.join(str(tmp_path), "t")
        self._mk_transcript(trans, "BV18DwszGE8E.md")
        assert vl.collect_gc_targets(str(tmp_path), trans, {}) == []

    def test_non_bv_name_kept(self, tmp_path):
        # YouTube key / md5 名无法确定性反推 → 保守留（P2-2）
        trans = os.path.join(str(tmp_path), "t")
        self._mk_transcript(trans, "https___www_youtube_com_watch.md")
        self._mk_transcript(trans, "a1b2c3d4e5f60718.md")
        assert vl.collect_gc_targets(str(tmp_path), trans, {}) == []

    def test_empty_index_kept(self, tmp_path):
        trans = os.path.join(str(tmp_path), "t")
        self._mk_transcript(trans, "BV18DwszGE8E.md")
        assert vl.collect_gc_targets(str(tmp_path), trans, {}) == []


# ── gc 执行 + journal 落盘（P2-6）─────────────────────────────────────────


class TestGcApply:
    def _patch_journal(self, tmp_path, monkeypatch) -> str:
        jf = os.path.join(str(tmp_path), "j", "sync_journal.jsonl")
        monkeypatch.setattr(vl, "_JOURNAL_FILE", jf)
        return jf

    def _read_journal(self, jf: str) -> list:
        with open(jf, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def test_apply_deletes_and_journals(self, tmp_path, monkeypatch):
        jf = self._patch_journal(tmp_path, monkeypatch)
        raw = _mk_series(str(tmp_path), "系列A", "第01集_老司机", body=True)
        vl.cmd_gc(apply=True, notes_dir=str(tmp_path),
                  transcripts_dir=os.path.join(str(tmp_path), "t"))
        assert not os.path.exists(raw)
        recs = self._read_journal(jf)
        evicts = [r for r in recs if r["action"] == "gc_evict"]
        runs = [r for r in recs if r["action"] == "gc_run"]
        assert len(evicts) == 1 and "第01集_老司机_raw.md" in evicts[0]["path"]
        assert evicts[0]["kind"] == "series_raw"
        assert len(runs) == 1 and runs[0]["evicted"] == 1

    def test_dry_keeps_files(self, tmp_path, monkeypatch):
        jf = self._patch_journal(tmp_path, monkeypatch)
        raw = _mk_series(str(tmp_path), "系列A", "第01集_老司机", body=True)
        vl.cmd_gc(apply=False, notes_dir=str(tmp_path),
                  transcripts_dir=os.path.join(str(tmp_path), "t"))
        assert os.path.exists(raw)
        assert not os.path.exists(jf)

    def test_apply_empty_target_still_records_run(self, tmp_path, monkeypatch):
        jf = self._patch_journal(tmp_path, monkeypatch)
        vl.cmd_gc(apply=True, notes_dir=str(tmp_path),
                  transcripts_dir=os.path.join(str(tmp_path), "t"))
        recs = self._read_journal(jf)
        assert [r for r in recs if r["action"] == "gc_run"][0]["evicted"] == 0

    def test_apply_tolerates_missing_file(self, tmp_path, monkeypatch):
        # 竞态：collect 后文件被外部删除 → os.remove 抛 FileNotFoundError → 跳过不崩
        jf = self._patch_journal(tmp_path, monkeypatch)
        raw = _mk_series(str(tmp_path), "系列A", "第01集_老司机", body=True)

        def _boom(path: str) -> None:
            raise FileNotFoundError(path)

        monkeypatch.setattr(os, "remove", _boom)
        vl.cmd_gc(apply=True, notes_dir=str(tmp_path),
                  transcripts_dir=os.path.join(str(tmp_path), "t"))
        assert os.path.exists(raw)  # 删除失败 → 文件仍在
        recs = self._read_journal(jf)
        assert not [r for r in recs if r["action"] == "gc_evict"]
        assert [r for r in recs if r["action"] == "gc_run"][0]["evicted"] == 0
