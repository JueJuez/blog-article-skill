"""回归测试：B站「充电专属·仅试看」视频的抓取拦截（2026-09-20）。

背景（实踩）：2 集 B站「充电专属」视频（88 分钟 / 28 分钟）在补齐流程里被当成正常视频下载，
B站只返回试看片段（实测仅覆盖 5.6% / 10.7%），管线**一路无告警地把残缺音频转写并落盘**，
笔记静默丢掉 90%+ 内容。事后定位到：监控发现路径早有充电过滤（读投稿列表的
`is_charging_arc`），但**补源/补齐路径完全没有**。

本测试守两件事（不联网、不下载）：
  ① 判据正确：只有「专属 且 仅试看」才拦；账号已充电（可看全片）时**不能误杀**。
  ② 拦截点齐备：字幕层（fetch_subtitle_only / fetch_bilibili_transcript）与
     ASR 层（transcribe_video）三处都拦得住，且不浪费一次音频下载。

运行：
    python tests/test_charging_exclusive_guard.py
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import videos.asr as asr
import videos.fetch as vf

URL = "https://www.bilibili.com/video/BV1TESTCHG001"
BVID = "BV1TESTCHG001"

PREVIEW_INFO = {
    "aid": 1, "cid": 2, "pubdate": 0, "title": "充电专属样例",
    "author": "某UP", "pages": [{"cid": 2, "page": 1, "part": ""}],
    "duration": 5312,
    "is_upower_exclusive": True, "is_upower_preview": True,
}
PAID_BUT_PLAYABLE_INFO = dict(PREVIEW_INFO, is_upower_preview=False,
                              is_upower_exclusive=True, title="已充电可看全片")
NORMAL_INFO = dict(PREVIEW_INFO, is_upower_exclusive=False, is_upower_preview=False,
                   title="普通视频", duration=637)


def _reset_global():
    vf.LAST_CHARGING_EXCLUSIVE = False


# ---------------------------------------------------------------------------
# ① 判据
# ---------------------------------------------------------------------------

def test_predicate_blocks_only_preview():
    assert vf.bili_is_charging_exclusive(PREVIEW_INFO) is True


def test_predicate_not_block_when_account_can_play():
    """账号已充电（is_upower_preview=False）→ 不拦，否则会误杀能看全片的集。"""
    assert vf.bili_is_charging_exclusive(PAID_BUT_PLAYABLE_INFO) is False


def test_predicate_normal_video_not_blocked():
    assert vf.bili_is_charging_exclusive(NORMAL_INFO) is False


def test_predicate_charging_arc_fallback():
    """老入口只给投稿列表的 is_charging_arc 时用它兜底。"""
    assert vf.bili_is_charging_exclusive({"is_charging_arc": 1}) is True
    assert vf.bili_is_charging_exclusive({"is_charging_arc": 0}) is False


def test_predicate_empty_is_false():
    assert vf.bili_is_charging_exclusive(None) is False
    assert vf.bili_is_charging_exclusive({}) is False


# ---------------------------------------------------------------------------
# ② 字幕层拦截
# ---------------------------------------------------------------------------

def test_fetch_subtitle_only_blocks_charging():
    _reset_global()
    with mock.patch.object(vf, "_bili_get_video_info", return_value=PREVIEW_INFO):
        res = vf.fetch_subtitle_only(URL)
    assert res is None, "充电专属应返回 None（不抓字幕）"
    assert vf.LAST_CHARGING_EXCLUSIVE is True, "应置位模块级标记供上层判断"


def test_fetch_bilibili_transcript_skips_asr_when_charging():
    """字幕层判定为充电专属后，不得再走 ASR 兜底（避免下载试看片段并落盘残缺笔记）。"""
    _reset_global()
    called = {"asr": 0}

    def _fake_asr(*a, **k):
        called["asr"] += 1
        return None

    with mock.patch.object(vf, "_bili_get_video_info", return_value=PREVIEW_INFO), \
            mock.patch.object(asr, "transcribe_video", side_effect=_fake_asr), \
            mock.patch.object(asr, "check_asr_deps", return_value=(True, [])):
        res = vf.fetch_bilibili_transcript(URL)
    assert res is None
    assert called["asr"] == 0, "充电专属不得进入 ASR 兜底"


def test_fetch_bilibili_transcript_still_uses_asr_for_normal_video():
    """反向用例：普通无字幕视频必须照旧走 ASR 兜底（护栏不能把正常路径也堵死）。

    这里直接桩掉字幕层（返回 None 且不置位充电标记），以保持测试完全离线。
    """
    _reset_global()
    called = {"asr": 0}

    def _fake_asr(*a, **k):
        called["asr"] += 1
        return ("标题", [{"start": 0.0, "duration": 0.0, "text": "正文"}], "某UP")

    with mock.patch.object(vf, "fetch_subtitle_only", return_value=None), \
            mock.patch.object(asr, "transcribe_video", side_effect=_fake_asr), \
            mock.patch.object(asr, "check_asr_deps", return_value=(True, [])):
        res = vf.fetch_bilibili_transcript(URL)
    assert called["asr"] == 1, "普通视频仍应尝试 ASR"
    assert res is not None and res[2] == "某UP"


# ---------------------------------------------------------------------------
# ③ ASR 层拦截（纵深防御：其他调用方直接调 transcribe_video 也拦得住）
# ---------------------------------------------------------------------------

def test_transcribe_video_blocks_charging_without_download():
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")  # 避免联网探测
    downloaded = {"n": 0}

    def _fake_extract(*a, **k):
        downloaded["n"] += 1
        return True

    with mock.patch.object(vf, "is_bilibili", return_value=True), \
            mock.patch.object(vf, "_bili_extract_bvid", return_value=BVID), \
            mock.patch.object(vf, "_bili_get_video_info", return_value=PREVIEW_INFO), \
            mock.patch.object(vf, "_bili_build_cookies_from_env", return_value="ck"), \
            mock.patch.object(asr, "extract_audio", side_effect=_fake_extract):
        res = asr.transcribe_video(URL, force=True)
    assert res is None, "充电专属应被 ASR 层拦下"
    assert downloaded["n"] == 0, "拦在下载之前，不应浪费一次音频下载"


def test_transcribe_video_rejects_incomplete_source():
    """源完整性校验：本地音频明显短于视频时长时必须拒绝转写，而不是产出残缺笔记。"""
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    with mock.patch.object(vf, "is_bilibili", return_value=True), \
            mock.patch.object(vf, "_bili_extract_bvid", return_value=BVID), \
            mock.patch.object(vf, "_bili_get_video_info", return_value=NORMAL_INFO), \
            mock.patch.object(vf, "_bili_build_cookies_from_env", return_value="ck"), \
            mock.patch.object(asr, "extract_audio", return_value=True), \
            mock.patch.object(asr, "_wav_duration", return_value=100.0), \
            mock.patch.object(asr, "transcribe_audio_chunked") as _chunked:
        res = asr.transcribe_video(URL, force=True)
    assert res is None, "只覆盖 100/637 秒应拒绝"
    _chunked.assert_not_called()


def test_transcribe_video_respects_asr_device_cpu():
    """`ASR_DEVICE=cpu` 必须被尊重。

    2026-09-20 修：`transcribe_video` 里「见到 GPU 就锁 cuda」的代码此前会**无条件覆盖**
    ASR_DEVICE，使 `_resolve_device` 文档承诺的开关在这条路径上实际失效（GPU/驱动异常时
    无法按文档强制回退 CPU）。此用例守住「显式 cpu 优先于自动探测」。

    注：`transcribe_video` 只做「是否锁 cuda」这一步，真正的 cpu/int8 解析在
    `_resolve_device` 里；这里拦住的是**被强行改成 cuda** 的行为。
    """
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    seen = {}

    def _fake_chunked(wav, model_size, lang, device, wall_timeout=None, **kw):
        seen["device"] = device
        return [{"start": 0.0, "duration": 0.0, "text": "正文"}]

    def _run(env_val):
        saved = os.environ.get("ASR_DEVICE")
        seen.clear()
        if env_val is None:
            os.environ.pop("ASR_DEVICE", None)
        else:
            os.environ["ASR_DEVICE"] = env_val
        try:
            with mock.patch.object(vf, "is_bilibili", return_value=True), \
                    mock.patch.object(vf, "_bili_extract_bvid", return_value=BVID), \
                    mock.patch.object(vf, "_bili_get_video_info", return_value=NORMAL_INFO), \
                    mock.patch.object(vf, "_bili_build_cookies_from_env", return_value="ck"), \
                    mock.patch.object(asr, "extract_audio", return_value=True), \
                    mock.patch.object(asr, "_wav_duration", return_value=637.0), \
                    mock.patch.object(asr, "_save_transcript_cache"), \
                    mock.patch.object(asr, "transcribe_audio_chunked", side_effect=_fake_chunked):
                asr.transcribe_video(URL, force=True)
        finally:
            if saved is None:
                os.environ.pop("ASR_DEVICE", None)
            else:
                os.environ["ASR_DEVICE"] = saved
        return seen.get("device")

    import ctranslate2
    # 假装本机看得到 GPU，两种 env 都要能测到
    with mock.patch.object(ctranslate2, "get_cuda_device_count", return_value=1):
        auto_dev = _run(None)
        cpu_dev = _run("cpu")

    assert auto_dev == "cuda", f"默认应锁 cuda，实际 {auto_dev}"
    assert cpu_dev != "cuda", f"ASR_DEVICE=cpu 不得被强行改回 cuda，实际 {cpu_dev}"


if __name__ == "__main__":
    test_predicate_blocks_only_preview()
    print("[PASS] test_predicate_blocks_only_preview")
    test_predicate_not_block_when_account_can_play()
    print("[PASS] test_predicate_not_block_when_account_can_play")
    test_predicate_normal_video_not_blocked()
    print("[PASS] test_predicate_normal_video_not_blocked")
    test_predicate_charging_arc_fallback()
    print("[PASS] test_predicate_charging_arc_fallback")
    test_predicate_empty_is_false()
    print("[PASS] test_predicate_empty_is_false")
    test_fetch_subtitle_only_blocks_charging()
    print("[PASS] test_fetch_subtitle_only_blocks_charging")
    test_fetch_bilibili_transcript_skips_asr_when_charging()
    print("[PASS] test_fetch_bilibili_transcript_skips_asr_when_charging")
    test_fetch_bilibili_transcript_still_uses_asr_for_normal_video()
    print("[PASS] test_fetch_bilibili_transcript_still_uses_asr_for_normal_video")
    test_transcribe_video_blocks_charging_without_download()
    print("[PASS] test_transcribe_video_blocks_charging_without_download")
    test_transcribe_video_rejects_incomplete_source()
    print("[PASS] test_transcribe_video_rejects_incomplete_source")
    test_transcribe_video_respects_asr_device_cpu()
    print("[PASS] test_transcribe_video_respects_asr_device_cpu")
    print("\n✅ 充电专属 / 源完整性护栏回归测试全部通过")
