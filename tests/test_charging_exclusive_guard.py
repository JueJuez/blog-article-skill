"""回归测试：充电专属 / 源完整性 的抓取策略（2026-09-20）。

**策略（用户 2026-09-20 定策，微调后）**：
  「充电专属」**不再一刀切跳过**。机理是 upower 限制的是**媒体流**（音频只给试看片段），
  **字幕流不一定要限**——实证两集走向完全相反：
    · `BV1eL4k6jEii` 音频只给 1/41 分钟，字幕却给了全片（源 11352 字 / 4.57 字/秒）→ 笔记合法；
    · `BV1fr8P6REBW` 音频只给 32/104 分钟 → 源真残。
  所以改成：
    ① **有字幕 → 直接用字幕过**（充电与否都一样）；
    ② **拿不到字幕 → 走 ASR，由「源完整性校验」裁决**：本地音频 < 视频时长×90% 即拒绝转写。

  两条已按用户决定**不做**的事（守住别悄悄长回来）：
    · 字幕侧不加「源字/秒」密度校验（用户：没遇到过字幕不完整，别加机制）；
    · 不设「已知充电+无字幕就直接记不可达」的快速路径（会误杀「短视频+试看完整」的真能收录集）。

本测试全部离线（不联网、不下载、不加载模型）。

运行：
    python tests/test_charging_exclusive_guard.py     # 或 pytest
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

def test_predicate_recognizes_preview_only():
    assert vf.bili_is_charging_exclusive(PREVIEW_INFO) is True


def test_predicate_not_triggered_when_account_can_play():
    """账号已充电（is_upower_preview=False）→ 不是「仅试看」。"""
    assert vf.bili_is_charging_exclusive(PAID_BUT_PLAYABLE_INFO) is False


def test_predicate_normal_video_not_matched():
    assert vf.bili_is_charging_exclusive(NORMAL_INFO) is False


def test_predicate_charging_arc_fallback():
    """老入口只给投稿列表的 is_charging_arc 时用它兜底。"""
    assert vf.bili_is_charging_exclusive({"is_charging_arc": 1}) is True
    assert vf.bili_is_charging_exclusive({"is_charging_arc": 0}) is False


def test_predicate_empty_is_false():
    assert vf.bili_is_charging_exclusive(None) is False
    assert vf.bili_is_charging_exclusive({}) is False


# ---------------------------------------------------------------------------
# ② 字幕层：充电专属**不再阻断**，仍照常尝试字幕
# ---------------------------------------------------------------------------

def test_subtitle_layer_does_not_block_charging():
    """充电专属也要照常查字幕——实证有集的字幕是完整的，一刀切会误杀。"""
    _reset_global()
    os.environ["BILI_BATCH_NO_ASR"] = "1"          # 免 yt-dlp 联网兜底
    try:
        with mock.patch.object(vf, "_bili_get_video_info", return_value=PREVIEW_INFO), \
                mock.patch.object(vf, "_bili_fetch_page_subtitle",
                                  return_value=None) as _sub:
            res = vf.fetch_subtitle_only(URL)
    finally:
        os.environ.pop("BILI_BATCH_NO_ASR", None)
    assert _sub.called, "不应因为「充电专属」就跳过字幕查询（这正是误杀的原因）"
    assert res is None, "此处确实没有字幕，返回 None 是对的"
    assert vf.LAST_CHARGING_EXCLUSIVE is True, "应置位标记供上层打印原因"


def test_subtitle_layer_returns_subtitles_for_charging_video():
    """充电专属 + 字幕可用 → **照常返回**（模拟 BV1eL4k6jEii 那种字幕完整的集）。"""
    _reset_global()
    segs = [{"start": 0.0, "duration": 1.0, "text": "完整字幕内容"}]
    with mock.patch.object(vf, "_bili_get_video_info", return_value=PREVIEW_INFO), \
            mock.patch.object(vf, "_bili_fetch_page_subtitle", return_value=segs):
        res = vf.fetch_subtitle_only(URL)
    assert res is not None, "充电专属但字幕完整时必须放行"
    assert res[1] == segs


def test_source_density_warning_removed():
    """字幕侧不做密度校验（用户 2026-09-20 决定：没遇到过字幕不完整，别加机制）。

    守住「不要悄悄长回来」——ASR 层的覆盖率闸门才是唯一硬闸门。
    """
    assert not hasattr(vf, "_source_density_warning"), \
        "字幕侧密度告警已按用户决定撤除，不应再出现（如需恢复请先与用户确认）"


# ---------------------------------------------------------------------------
# ③ 路由层：无字幕的充电集仍应尝试 ASR（由完整性校验裁决，而不是凭标记拒绝）
# ---------------------------------------------------------------------------

def test_transcript_router_tries_asr_for_charging_without_subtitle():
    _reset_global()
    called = {"asr": 0}

    def _fake_asr(*a, **k):
        called["asr"] += 1
        return None

    with mock.patch.object(vf, "fetch_subtitle_only",
                           side_effect=lambda *a, **k: (setattr(vf, "LAST_CHARGING_EXCLUSIVE", True), None)[1]), \
            mock.patch.object(asr, "transcribe_video", side_effect=_fake_asr), \
            mock.patch.object(asr, "check_asr_deps", return_value=(True, [])):
        res = vf.fetch_bilibili_transcript(URL)
    assert called["asr"] == 1, "充电专属且无字幕时应交给 ASR+完整性校验裁决，而不是直接拒绝"
    assert res is None


def test_transcript_router_still_uses_asr_for_normal_video():
    _reset_global()
    called = {"asr": 0}

    def _fake_asr(*a, **k):
        called["asr"] += 1
        return ("标题", [{"start": 0.0, "duration": 0.0, "text": "正文"}], "某UP")

    with mock.patch.object(vf, "fetch_subtitle_only", return_value=None), \
            mock.patch.object(asr, "transcribe_video", side_effect=_fake_asr), \
            mock.patch.object(asr, "check_asr_deps", return_value=(True, [])):
        res = vf.fetch_bilibili_transcript(URL)
    assert called["asr"] == 1
    assert res is not None and res[2] == "某UP"


# ---------------------------------------------------------------------------
# ④ ASR 层：唯一闸门 = 音频覆盖率 ≥90%
# ---------------------------------------------------------------------------

def _run_transcribe(info, audio_dur, chunked_result=None):
    """跑一次 transcribe_video，返回 (结果, 是否调用了转写, 是否下载了音频)。"""
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    seen = {"chunked": 0, "download": 0}

    def _fake_extract(*a, **k):
        seen["download"] += 1
        return True

    def _fake_chunked(*a, **k):
        seen["chunked"] += 1
        return chunked_result if chunked_result is not None else [
            {"start": 0.0, "duration": 0.0, "text": "正文"}]

    with mock.patch.object(vf, "is_bilibili", return_value=True), \
            mock.patch.object(vf, "_bili_extract_bvid", return_value=BVID), \
            mock.patch.object(vf, "_bili_get_video_info", return_value=info), \
            mock.patch.object(vf, "_bili_build_cookies_from_env", return_value="ck"), \
            mock.patch.object(asr, "extract_audio", side_effect=_fake_extract), \
            mock.patch.object(asr, "_wav_duration", return_value=audio_dur), \
            mock.patch.object(asr, "_save_transcript_cache"), \
            mock.patch.object(asr, "transcribe_audio_chunked", side_effect=_fake_chunked):
        res = asr.transcribe_video(URL, force=True)
    return res, seen


def test_charging_with_incomplete_audio_is_rejected():
    """充电专属 + 只拿到试看片段（<90%）→ 拒绝转写（这就是新策略的闸门）。"""
    res, seen = _run_transcribe(PREVIEW_INFO, 1919.0)   # 32/104 分钟
    assert res is None, "覆盖率 31% 必须拒绝"
    assert seen["download"] == 1, "应按新策略先实测音频长度，而不是凭充电标记直接拒"
    assert seen["chunked"] == 0, "不得转写残缺音频"


def test_charging_with_complete_audio_is_accepted():
    """充电专属 + 音频完整（≥90%）→ 放行（不因充电标记误杀）。"""
    res, seen = _run_transcribe(PREVIEW_INFO, 5312.0)
    assert seen["chunked"] == 1, "音频完整时应正常转写"
    assert res is not None


def test_normal_video_with_incomplete_audio_is_rejected():
    """非充电集同样受完整性校验保护（下载断流 / CDN 只给一段 也拦得住）。"""
    res, seen = _run_transcribe(NORMAL_INFO, 100.0)     # 100/637 秒
    assert res is None
    assert seen["chunked"] == 0


def test_normal_video_with_complete_audio_is_accepted():
    res, seen = _run_transcribe(NORMAL_INFO, 637.0)
    assert seen["chunked"] == 1
    assert res is not None


def test_transcribe_video_respects_asr_device_cpu():
    """`ASR_DEVICE=cpu` 必须被尊重（此前「见到 GPU 就锁 cuda」会无条件覆盖它）。"""
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
    with mock.patch.object(ctranslate2, "get_cuda_device_count", return_value=1):
        auto_dev = _run(None)
        cpu_dev = _run("cpu")

    assert auto_dev == "cuda", f"默认应锁 cuda，实际 {auto_dev}"
    assert cpu_dev != "cuda", f"ASR_DEVICE=cpu 不得被强行改回 cuda，实际 {cpu_dev}"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"[PASS] {name}")
    print("\n✅ 充电专属 / 源完整性策略回归测试全部通过")
