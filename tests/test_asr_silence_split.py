# -*- coding: utf-8 -*-
"""静音切段 + 并发转写守护测试（2026-10-06，借鉴 video-report-agent 中点静音切段）。

锁定 `videos/asr.py` whisper 分支的四件事（FunASR 分支自带 fsmn-vad，不在此列）：
1. `_detect_silences`：ffmpeg silencedetect stderr 解析；任何失败返回 [] 不阻断。
2. `_pick_silence_boundaries`：理想切点 ±60s 内有静音取静音中点、无静音回退理想切点、
   min_span 保证每片 ≥30s 且严格递增。
3. `_ffmpeg_segment_silence`：切点 → (路径, 偏移) 列表；切片失败返回 [] 走回退。
4. `transcribe_audio_chunked` 新路径：静音切 N 片 → 并发转写 → 按真实偏移拼时间戳、
   按 offset 排序输出；任一片失败整段 None；ASR_SILENCE_SPLIT=0 走旧固定切片串行路径。
5. 开关与默认值：ASR_SPLIT_CONCURRENCY / ASR_MODEL_NUM_WORKERS / num_workers 进缓存键。

本测试不下载模型、不跑 ffmpeg（全部 mock）。

运行：
    python tests/test_asr_silence_split.py
"""
import os
import sys
import types
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import videos.asr as asr


def _ffmpeg_result(returncode=0, stderr=""):
    return types.SimpleNamespace(returncode=returncode, stderr=stderr, stdout="")


SILENCE_STDERR = """
[silencedetect @ 0x0] silence_start: 599.2
[silencedetect @ 0x0] silence_end: 601.8 | silence_duration: 2.6
[silencedetect @ 0x0] silence_start: 1199.0
[silencedetect @ 0x0] silence_end: 1200.5 | silence_duration: 1.5
"""


class TestDetectSilences:
    def test_parse_pairs_in_order(self):
        with mock.patch.object(asr, "_ffmpeg_exe", return_value="ffmpeg"):
            with mock.patch.object(asr.subprocess, "run",
                                   return_value=_ffmpeg_result(stderr=SILENCE_STDERR)):
                got = asr._detect_silences("x.wav")
        assert got == [(599.2, 601.8), (1199.0, 1200.5)]

    def test_failure_returns_empty(self):
        with mock.patch.object(asr, "_ffmpeg_exe", return_value=None):
            assert asr._detect_silences("x.wav") == []
        with mock.patch.object(asr, "_ffmpeg_exe", return_value="ffmpeg"):
            with mock.patch.object(asr.subprocess, "run", side_effect=OSError("boom")):
                assert asr._detect_silences("x.wav") == []

    def test_unclosed_silence_dropped(self):
        stderr = "[x] silence_start: 100.0\n"  # 无 silence_end（音频尾部静音）
        with mock.patch.object(asr, "_ffmpeg_exe", return_value="ffmpeg"):
            with mock.patch.object(asr.subprocess, "run",
                                   return_value=_ffmpeg_result(stderr=stderr)):
                assert asr._detect_silences("x.wav") == []


class TestPickSilenceBoundaries:
    def test_snaps_to_silence_midpoint(self):
        # 理想切点 600s，静音 (599.2, 601.8) → 中点 600.5
        pts = asr._pick_silence_boundaries(
            1200.0, [(599.2, 601.8), (1199.0, 1200.5)], target_sec=600, tolerance=60)
        assert pts == [600.5]

    def test_fallback_to_ideal_when_no_silence(self):
        pts = asr._pick_silence_boundaries(1200.0, [], target_sec=600, tolerance=60)
        assert pts == [600.0]

    def test_out_of_tolerance_ignored(self):
        # 静音中点距理想切点 > 60s → 回退理想切点
        pts = asr._pick_silence_boundaries(
            1200.0, [(700.0, 702.0)], target_sec=600, tolerance=60)
        assert pts == [600.0]

    def test_strictly_increasing_with_min_span(self):
        # 两个候选切点相距 < 30s：第二个被丢弃
        pts = asr._pick_silence_boundaries(
            1240.0, [(599.0, 601.0), (620.0, 622.0)], target_sec=600,
            tolerance=60, min_span=30)
        assert pts == [600.0]

    def test_boundaries_clamped_away_from_edges(self):
        # 靠近首尾（< 30s）的切点被丢弃：duration 610s，切点 600.5 只留 9.5s 尾巴
        pts = asr._pick_silence_boundaries(
            610.0, [(599.2, 601.8)], target_sec=600, tolerance=60)
        assert pts == []


class TestFfmpegSegmentSilence:
    def test_returns_path_offset_pairs(self):
        with mock.patch.object(asr, "_ffmpeg_exe", return_value="ffmpeg"), \
             mock.patch.object(asr.subprocess, "run",
                               return_value=_ffmpeg_result()) as mr, \
             mock.patch.object(asr.os.path, "isfile", return_value=True):
            got = asr._ffmpeg_segment_silence(
                "x.wav", "segdir", [600.5], 1200.0)
        assert [off for _, off in got] == [0.0, 600.5]
        # 第一次切 0→600.5，第二次切 600.5 起
        first_call = mr.call_args_list[0][0][0]
        assert "-ss" in first_call and first_call[first_call.index("-ss") + 1] == "0.000"
        assert "-t" in first_call and first_call[first_call.index("-t") + 1] == "600.500"

    def test_failure_returns_empty(self):
        with mock.patch.object(asr, "_ffmpeg_exe", return_value="ffmpeg"), \
             mock.patch.object(asr.subprocess, "run",
                               return_value=_ffmpeg_result(returncode=1)):
            assert asr._ffmpeg_segment_silence("x.wav", "segdir", [600.5], 1200.0) == []


class TestResolveConcurrencyAndWorkers:
    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("ASR_SPLIT_CONCURRENCY", "3")
        assert asr._resolve_split_concurrency("cuda") == 3
        monkeypatch.setenv("ASR_MODEL_NUM_WORKERS", "4")
        assert asr._resolve_model_num_workers("cuda") == 4

    def test_invalid_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("ASR_SPLIT_CONCURRENCY", "abc")
        monkeypatch.setenv("ASR_MODEL_NUM_WORKERS", "0")
        with mock.patch.object(asr, "_resolve_device", return_value=("cpu", "int8")):
            assert asr._resolve_split_concurrency("auto") == 1
        assert asr._resolve_model_num_workers("cpu") == 1

    def test_default_cuda2_cpu1(self, monkeypatch):
        monkeypatch.delenv("ASR_SPLIT_CONCURRENCY", raising=False)
        monkeypatch.delenv("ASR_MODEL_NUM_WORKERS", raising=False)
        with mock.patch.object(asr, "_resolve_device", return_value=("cuda", "float16")):
            assert asr._resolve_split_concurrency("auto") == 2
        assert asr._resolve_model_num_workers("cuda") == 2
        assert asr._resolve_model_num_workers("cpu") == 1

    def test_num_workers_in_cache_key(self, monkeypatch):
        """num_workers 必须参与模型缓存键（不同 worker 数不串用同一实例）。"""
        monkeypatch.setenv("ASR_MODEL_NUM_WORKERS", "2")
        stub = types.SimpleNamespace()
        stub.WhisperModel = mock.Mock(return_value=object())
        asr._MODEL_CACHE.clear()
        with mock.patch.dict(sys.modules, {"faster_whisper": stub}):
            with mock.patch.object(asr, "_resolve_device", return_value=("cpu", "int8")), \
                 mock.patch.object(asr, "_resolve_local_model_dir", return_value="/m/dir"), \
                 mock.patch.object(asr, "_apply_env_defaults"), \
                 mock.patch.object(asr, "_ensure_cuda_dlls"):
                asr._load_model("medium", "cpu")
        stub.WhisperModel.assert_called_once()
        assert stub.WhisperModel.call_args.kwargs.get("num_workers") == 2
        assert any(k[3] == 2 for k in asr._MODEL_CACHE), asr._MODEL_CACHE.keys()


def _seg(t0):
    return {"start": float(t0), "duration": 1.0, "text": "hello"}


class TestConcurrentChunks:
    def test_offsets_applied_and_sorted(self):
        chunks = [("a.wav", 0.0), ("b.wav", 600.5), ("c.wav", 1199.0)]
        fake = {0: [_seg(0.0), _seg(5.0)], 1: [_seg(1.0)], 2: [_seg(2.0)]}
        with mock.patch.object(asr, "transcribe_audio",
                               side_effect=lambda p, *a, **k: fake[
                                   [c[0] for c in chunks].index(p)]):
            got = asr._transcribe_chunks_concurrent(chunks, "medium", None, "cpu", 2,
                                                    types.SimpleNamespace(time=lambda: 0.0))
        assert [s["start"] for s in got] == [0.0, 5.0, 601.5, 1201.0]

    def test_any_chunk_failure_returns_none(self):
        chunks = [("a.wav", 0.0), ("b.wav", 600.5)]
        with mock.patch.object(asr, "transcribe_audio",
                               side_effect=[[_seg(0.0)], None]):
            got = asr._transcribe_chunks_concurrent(chunks, "medium", None, "cpu", 2,
                                                    types.SimpleNamespace(time=lambda: 0.0))
        assert got is None


class TestChunkedRouting:
    def _run_chunked(self, monkeypatch, chunks, seg_results, silence_split="1"):
        monkeypatch.setenv("ASR_BACKEND", "whisper")
        monkeypatch.setenv("ASR_SILENCE_SPLIT", silence_split)
        calls = {"seg": 0, "legacy": 0}
        monkeypatch.setattr(asr, "_wav_duration", lambda w: 2000.0)
        monkeypatch.setattr(asr, "_detect_silences", lambda w: [(599.2, 601.8)])
        monkeypatch.setattr(asr, "_pick_silence_boundaries",
                            lambda dur, sil, target_sec=600, **k: [600.5])
        monkeypatch.setattr(asr, "_ffmpeg_segment_silence",
                            lambda w, d, b, dur: chunks)
        monkeypatch.setattr(asr, "transcribe_audio",
                            mock.Mock(side_effect=seg_results))
        monkeypatch.setattr(asr, "_cleanup", lambda d: None)
        legacy = mock.Mock(return_value=[_seg(0.0)])
        monkeypatch.setattr(asr, "_ffmpeg_segment", legacy)
        return legacy

    def test_silence_path_preferred(self, monkeypatch):
        legacy = self._run_chunked(monkeypatch, [("a.wav", 0.0), ("b.wav", 600.5)],
                                   [[_seg(1.0)], [_seg(2.0)]])
        got = asr.transcribe_audio_chunked("x.wav")
        assert [s["start"] for s in got] == [1.0, 602.5]
        legacy.assert_not_called()

    def test_fallback_to_legacy_when_silence_cut_fails(self, monkeypatch):
        legacy = self._run_chunked(monkeypatch, [], [[_seg(1.0)]])
        got = asr.transcribe_audio_chunked("x.wav")
        # 旧路径：_ffmpeg_segment 返回 1 片 → 串行转写（mock 返回 start=1.0）→ 偏移 idx*600=0
        assert got == [_seg(1.0)]
        legacy.assert_called_once()

    def test_env_disable_goes_legacy(self, monkeypatch):
        legacy = self._run_chunked(monkeypatch, [("a.wav", 0.0)],
                                   [[_seg(1.0)]], silence_split="0")
        asr.transcribe_audio_chunked("x.wav")
        legacy.assert_called_once()

    def test_any_chunk_failure_returns_none(self, monkeypatch):
        self._run_chunked(monkeypatch, [("a.wav", 0.0), ("b.wav", 600.5)],
                          [[_seg(1.0)], None])
        assert asr.transcribe_audio_chunked("x.wav") is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
