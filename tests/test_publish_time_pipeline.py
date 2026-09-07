"""发布时间（publish_time）生成链路缺口测试（RED，2026-09-07）。

背景（用户需求）：生成的笔记信息块在「作者 / 来源链接」行下方增加
「发布时间：YYYY-MM-DD HH:MM」字段。展示层 prompts.templates.format_note_with_prompt
已支持 publish_time 渲染（>0 才显示）；本文件锁定上游数据缺口，GREEN 打通后
信息块才会出现发布时间：

① scys 单篇：fetch_web_content 解析正文独立「YYYY-MM-DD HH:MM」时间行
② scys 单篇降级链：summarize_and_save 降级记录 _LAST_PUBLISH_TIME，
   skill_main need_continue_summary 字典透出 publish_time
③ skill_continue_summary（子 Agent 补存入口）签名支持 publish_time
④ scys 批量落地：land_scys_batch 读队列条目 list_meta.gmtCreate，不再硬编码 0
⑤ B站系列课：entries[].pubdate → _save_series_note / 返回值
   （全系列发布时间唯一时采用；多值/缺失诚实置 0，不用处理时间冒充）
⑥ B站单视频：_bili_get_video_info 提取 view API pubdate → fetch_subtitle_only
   存模块级 LAST_PUBDATE → _handle_single_video 注入 input_data，
   降级返回携带 publish_time
⑦ 批量脚本 videos.run._run_batch 结果行携带 publish_time（编排层入队需要）
"""

import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from articles import fetch as af
from articles import main as am
from videos import fetch as vf
from videos import main as vm
from videos import run as vrun
from scripts import land_scys_batch as lsb

# 测试基准：2026-03-20 16:32（东八区）的 epoch 秒
PT = int(datetime(2026, 3, 20, 16, 32, tzinfo=timezone(timedelta(hours=8))).timestamp())

_DEGRADE_RESULT = {"summary": None, "usage": None, "model": None, "source": None}


def _patch_degrade(monkeypatch, tmp_path):
    """把 AI 总结打为不可用并拦截 raw 落盘，稳定触发降级路径。"""
    monkeypatch.setattr(am, "summarize_content", lambda *a, **k: dict(_DEGRADE_RESULT))
    monkeypatch.setattr(
        am, "save_raw_content_to_file",
        lambda content, title="", prefix="_raw_": str(tmp_path / "raw.md"))


def _scys_body() -> str:
    filler = "这是一段用于通过最短长度校验的正文内容，围绕自媒体与出海实践展开。" * 5
    return f"{filler}\n\n2026-03-20 16:32\n\n正文从这里开始。"


def _fake_series(pubdates):
    """构造 fetch_bilibili_series 形状的假系列（entries 带 pubdate 字段）。"""
    entries = []
    for i, pub in enumerate(pubdates, 1):
        entries.append({
            "page": i, "part": f"第{i}讲", "bvid": f"BV{i:010d}", "aid": i, "cid": i,
            "title": f"第{i}讲 标题", "pubdate": pub,
            "segments": [{"text": f"第{i}讲的字幕句子。"}],
        })
    return {"series_title": "测试系列课", "bvid": "BV1test0000", "kind": "ugc_season",
            "author": "UP主小张", "entries": entries}


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


# ---------------------------------------------------------------------------
# ① scys 单篇：正文独立时间行解析
# ---------------------------------------------------------------------------

class TestScysBodyPublishTime:
    def test_extracts_standalone_date_line(self):
        body = "前文" * 40 + "\n\n2026-03-20 16:32\n\n后续正文"
        assert af._scys_publish_time_from_body(body) == PT

    def test_no_date_line_returns_zero(self):
        assert af._scys_publish_time_from_body("没有时间行的正文。" * 30) == 0

    def test_empty_body_returns_zero(self):
        assert af._scys_publish_time_from_body("") == 0

    def test_time_inside_sentence_not_matched(self):
        body = "发布于 2026-03-20 16:32 的内容，行内时间不应命中。" * 5
        assert af._scys_publish_time_from_body(body) == 0


class TestScysFetchWebContent:
    def test_scys_branch_returns_publish_time(self, tmp_path, monkeypatch):
        f = tmp_path / "scys_raw.md"
        f.write_text(_scys_body(), encoding="utf-8")
        monkeypatch.setattr(af, "_scys_cdp_fetch",
                            lambda url: {"title": "生财帖", "output": str(f)})
        res = af.fetch_web_content("https://scys.com/articleDetail/xq_topic/123")
        assert res is not None
        assert res[2] == PT


# ---------------------------------------------------------------------------
# ② scys 单篇降级链：_LAST_PUBLISH_TIME + need_continue 透出
# ---------------------------------------------------------------------------

class TestSummarizeDegradePublishTime:
    def test_degrade_records_last_publish_time(self, tmp_path, monkeypatch):
        _patch_degrade(monkeypatch, tmp_path)
        summarized, second, third, original_title, error_msg = am.summarize_and_save(
            "<p>直接粘贴的文章正文，用于触发降级路径。</p>" * 3,
            "生财有术圈友", ["测试"], publish_time=PT)
        assert summarized is None
        assert am._LAST_PUBLISH_TIME == PT


class TestSkillMainNeedContinuePublishTime:
    def test_content_need_continue_carries_publish_time(self, tmp_path, monkeypatch):
        _patch_degrade(monkeypatch, tmp_path)
        res = am.skill_main({"content": "<p>粘贴的正文内容。</p>" * 5,
                             "publish_time": PT, "author": "生财有术圈友", "tags": ["测试"]})
        assert res.get("need_continue_summary") is True
        assert res.get("publish_time") == PT

    def test_scys_url_need_continue_carries_publish_time(self, tmp_path, monkeypatch):
        _patch_degrade(monkeypatch, tmp_path)
        f = tmp_path / "scys_raw.md"
        f.write_text(_scys_body(), encoding="utf-8")
        monkeypatch.setattr(af, "_scys_cdp_fetch",
                            lambda url: {"title": "生财帖", "output": str(f)})
        res = am.skill_main({"url": "https://scys.com/articleDetail/xq_topic/123"})
        assert res.get("need_continue_summary") is True
        assert res.get("publish_time") == PT


# ---------------------------------------------------------------------------
# ③ skill_continue_summary 补存入口签名
# ---------------------------------------------------------------------------

class TestSkillContinueSummarySignature:
    def test_accepts_and_forwards_publish_time(self, monkeypatch):
        captured = {}

        def fake_save(summarized_content, original_url="", author="", tags=None,
                      original_title="", publish_time=0, **kw):
            captured["publish_time"] = publish_time
            return ("note", "file.md")

        monkeypatch.setattr(am, "save_summarized_article", fake_save)
        monkeypatch.setattr(
            am, "autoroute_folder",
            lambda folder, author, url, title, tags=None, **kw: (folder or "F", tags or []))
        res = am.skill_continue_summary("正文", "总结内容", publish_time=PT)
        assert res.get("success") is True
        assert captured["publish_time"] == PT


# ---------------------------------------------------------------------------
# ④ scys 批量落地：读队列 list_meta.gmtCreate
# ---------------------------------------------------------------------------

class TestLandScysBatchPublishTime:
    def _prepare(self, tmp_path, with_gmt):
        raw_file = str(tmp_path / "raw_1.md")
        entry = {"output": raw_file, "topicId": "1", "title": "T",
                 "url": "https://scys.com/articleDetail/xq_topic/9"}
        if with_gmt:
            entry["list_meta"] = {"gmtCreate": PT}
        pending_path = tmp_path / "pending_summaries.json"
        pending_path.write_text(json.dumps([entry], ensure_ascii=False), encoding="utf-8")
        map_path = tmp_path / "_dispatch_map.json"
        map_path.write_text(json.dumps(
            {"temp": str(tmp_path), "items": [
                {"index": 1, "raw_file": raw_file, "project": "AI产品开发",
                 "title": "T", "url": "https://scys.com/articleDetail/xq_topic/9"}]},
            ensure_ascii=False), encoding="utf-8")
        (tmp_path / "sum_1.md").write_text("总结正文\n\n#AI #生财有术", encoding="utf-8")
        return str(pending_path), str(map_path)

    def _run_landing(self, tmp_path, monkeypatch, with_gmt=True):
        pending_path, map_path = self._prepare(tmp_path, with_gmt)
        monkeypatch.setattr(lsb, "PENDING", pending_path)
        monkeypatch.setattr(lsb, "MAP", map_path)
        monkeypatch.setattr("shared.routing.resolve_folder",
                            lambda d: "【监控】/生财有术/AI产品开发")
        captured = {}

        def fake_save_summary_only(payload):
            captured.update(payload)
            return {"success": True, "filename": "x.md"}

        monkeypatch.setattr(am, "save_summary_only", fake_save_summary_only)
        lsb.main()
        return captured

    def test_uses_list_meta_gmt_create(self, tmp_path, monkeypatch):
        captured = self._run_landing(tmp_path, monkeypatch, with_gmt=True)
        assert captured.get("publish_time") == PT

    def test_missing_list_meta_falls_back_to_zero(self, tmp_path, monkeypatch):
        captured = self._run_landing(tmp_path, monkeypatch, with_gmt=False)
        assert captured.get("publish_time") == 0


# ---------------------------------------------------------------------------
# ⑤ B站系列课：entries pubdate → 落盘与返回值
# ---------------------------------------------------------------------------

class TestBiliSeriesPublishTime:
    def _patch_series_env(self, monkeypatch, tmp_path, pending_bases):
        monkeypatch.setattr(am, "NOTES_DIR", str(tmp_path))
        monkeypatch.setattr(vm.series_state, "get_pending",
                            lambda title, bases: list(pending_bases))
        monkeypatch.setattr(vm.series_state, "mark_done", lambda *a, **k: None)
        monkeypatch.setattr(
            vm, "_summarize_segments",
            lambda segs, note_type, title="", visual_context="": f"总结：{title}")
        monkeypatch.setattr(vm, "_generate_series_overview", lambda *a, **k: "")
        monkeypatch.setattr(vm, "_collect_landed_series_names", lambda *a, **k: [])

    def _patch_series_save(self, monkeypatch):
        saved = []

        def fake_save(content, series_dir, base_name, author, url, tags, note_type,
                      obsidian=False, folder="", publish_time=0):
            saved.append((base_name, publish_time))
            return os.path.join(series_dir, f"{base_name}.md")

        monkeypatch.setattr(vm, "_save_series_note", fake_save)
        return saved

    def _run(self, monkeypatch, tmp_path, pubdates, pending_bases=None):
        self._patch_series_env(monkeypatch, tmp_path,
                               pending_bases if pending_bases is not None
                               else [f"第{e['page']:02d}集_{e['part']}"
                                     for e in _fake_series(pubdates)["entries"]])
        saved = self._patch_series_save(monkeypatch)
        url = "https://www.bilibili.com/video/BV1test0000"
        res = vm._handle_bilibili_series(url, {"url": url}, series=_fake_series(pubdates))
        return res, saved

    def test_uniform_pubdate_flows_to_notes_and_result(self, monkeypatch, tmp_path):
        res, saved = self._run(monkeypatch, tmp_path, [PT, PT])
        assert res["success"] is True
        assert res["publish_time"] == PT
        assert saved and all(pub == PT for _, pub in saved)

    def test_mixed_pubdates_honest_zero(self, monkeypatch, tmp_path):
        res, saved = self._run(monkeypatch, tmp_path, [PT, PT + 86400])
        assert res["publish_time"] == 0
        assert saved and all(pub == 0 for _, pub in saved)

    def test_missing_pubdates_zero(self, monkeypatch, tmp_path):
        res, saved = self._run(monkeypatch, tmp_path, [None, None])
        assert res["publish_time"] == 0
        assert saved and all(pub == 0 for _, pub in saved)

    def test_all_done_early_return_carries_publish_time(self, monkeypatch, tmp_path):
        res, saved = self._run(monkeypatch, tmp_path, [PT], pending_bases=[])
        assert res["success"] is True
        assert res["publish_time"] == PT
        assert saved == []


# ---------------------------------------------------------------------------
# ⑥ B站单视频：view pubdate → LAST_PUBDATE → input_data 注入
# ---------------------------------------------------------------------------

class TestBiliVideoInfoPubdate:
    def test_view_info_includes_pubdate(self, monkeypatch):
        payload = {"code": 0, "data": {
            "aid": 1, "cid": 2, "title": "视频标题", "desc": "",
            "owner": {"name": "UP主小张"}, "pubdate": PT,
            "pages": [{"cid": 2, "page": 1, "part": "P1"}],
        }}
        monkeypatch.setattr(vf, "_bili_urlopen",
                            lambda api, headers, timeout=15, tag="": FakeResp(payload))
        info = vf._bili_get_video_info("BV1abc")
        assert info is not None
        assert info["pubdate"] == PT


class TestFetchSubtitleOnlyLastPubdate:
    def test_sets_last_pubdate(self, monkeypatch):
        monkeypatch.setattr(vf, "_bili_get_video_info", lambda bvid: {
            "aid": 1, "cid": 2, "title": "视频标题", "author": "UP主小张",
            "pubdate": PT, "pages": [{"cid": 2, "page": 1, "part": ""}]})
        monkeypatch.setattr(vf, "_bili_fetch_page_subtitle",
                            lambda aid, cid, lang: [{"text": "句一。", "start": 0.0}])
        res = vf.fetch_subtitle_only("https://www.bilibili.com/video/BV1abc")
        assert res is not None
        assert vf.LAST_PUBDATE == PT


class TestHandleSingleVideoPublishTime:
    URL = "https://www.bilibili.com/video/BV1abc"

    def _patch_fetch(self, monkeypatch):
        monkeypatch.setattr(
            vm, "fetch",
            SimpleNamespace(
                fetch_transcript=lambda url: ("视频标题", [{"text": "内容句子。"}], "UP主小张"),
                LAST_PUBDATE=PT))

    def test_injects_fetch_pubdate_into_save(self, monkeypatch, tmp_path):
        self._patch_fetch(monkeypatch)
        captured = {}

        def fake_sas(segments, url, title, author, tags, note_type, force,
                     visual_context="", publish_time=0, folder="", obsidian=False):
            captured["publish_time"] = publish_time
            return ("f.md", "正文", False, "原文内容", note_type or "structured")

        monkeypatch.setattr(vm, "_summarize_and_save", fake_sas)
        res = vm._handle_single_video(self.URL, {"url": self.URL})
        assert res.get("success") is True
        assert captured["publish_time"] == PT

    def test_degraded_result_carries_publish_time(self, monkeypatch, tmp_path):
        self._patch_fetch(monkeypatch)

        def fake_sas(segments, url, title, author, tags, note_type, force,
                     visual_context="", publish_time=0, folder="", obsidian=False):
            return ("", "", True, "字幕原文", "key_points")

        monkeypatch.setattr(vm, "_summarize_and_save", fake_sas)
        monkeypatch.setattr(
            am, "save_raw_content_to_file",
            lambda content, title="", prefix="_raw_": str(tmp_path / "raw.md"))
        res = vm._handle_single_video(self.URL, {"url": self.URL})
        assert res.get("need_continue_summary") is True
        assert res.get("publish_time") == PT


# ---------------------------------------------------------------------------
# ⑦ 批量脚本结果行
# ---------------------------------------------------------------------------

class TestRunBatchPublishTime:
    def test_result_row_carries_publish_time(self, tmp_path, monkeypatch, capsys):
        batch_file = tmp_path / "batch.json"
        batch_file.write_text(json.dumps([
            {"idx": 1, "url": "https://www.bilibili.com/video/BV1abc",
             "title": "T", "author": "UP主小张", "publish_time": PT, "lang": "zh"},
        ], ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr("videos.summarize_video",
                            lambda input_data: {"success": True})
        monkeypatch.setattr(vrun.time, "sleep", lambda s: None)
        args = SimpleNamespace(batch_file=str(batch_file),
                               delay_min=0, delay_max=0, max_per_hour=0)
        rc = vrun._run_batch(args)
        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(
            out.split("=====BATCH_RESULTS_START=====")[1].split("=====BATCH_RESULTS_END=====")[0])
        assert payload["videos"][0]["publish_time"] == PT
