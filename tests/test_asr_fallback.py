"""回归测试：ASR 兜底链路的「环境坑自动处理 + 断点续跑缓存 + 防御性删除」逻辑。

背景：2026-08-06 实跑时，ASR 链路因 5 类本机环境坑（HF 镜像 / xet / 沙箱安全删除 /
CUDA dll / symlink）连踩 6 次失败，且曾因 `glob('notes/_raw_*.md')` 误删 50 个无关
暂存文件。这些本应写进代码、由测试守住，而不是靠每次手敲 export + 反复试错。

本测试**不下载模型、不联网**（仅验证逻辑与护栏）：
  - `_apply_env_defaults`：自动设 HF 镜像 / 关 xet / 关 hf_transfer / 设 HF_HOME；
    且不覆盖用户已显式设置的变量。
  - `_transcript_cache_path`：B站/YouTube/其他来源分别解析到 `transcripts/<key>.md`，
    命中缓存可跳过下载+转写（断点续跑）。
  - `safe_remove_one`：拒绝 glob 通配与目录，只删明确指定的单个文件（防误删护栏）。
  - `_resolve_repo_id` / `_resolve_local_model_dir`：模型 repo id 解析正确；
    且**强制 `local_dir_use_symlinks=False`**（绕开 Windows 沙箱 symlink 挂不上导致
    model.bin 找不到的坑）。
  - `_resolve_device`：cpu/cuda/auto 三种输入返回正确 (device, compute_type)。

运行：
    python tests/test_asr_fallback.py
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import videos.asr as asr


# ---------------------------------------------------------------------------
# 1) 环境坑自动处理
# ---------------------------------------------------------------------------

def test_env_defaults_fill_missing():
    """未设置时自动补齐 xet 关闭 / hf_transfer 关闭 / HF_HOME。"""
    saved = {k: os.environ.pop(k, None) for k in
             ("HF_HUB_DISABLE_XET", "HF_HUB_ENABLE_HF_TRANSFER", "HF_HOME")}
    try:
        asr._apply_env_defaults()
        assert os.environ["HF_HUB_DISABLE_XET"] == "1", "应关闭 xet"
        assert os.environ["HF_HUB_ENABLE_HF_TRANSFER"] == "0", "应关闭 hf_transfer"
        assert os.environ["HF_HOME"], "应设 HF_HOME（指向系统临时目录）"
        assert os.path.isabs(os.environ["HF_HOME"])
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_env_defaults_do_not_override_explicit():
    """用户已显式设置的变量不应被覆盖。"""
    saved = {k: os.environ.get(k) for k in
             ("HF_HUB_DISABLE_XET", "HF_HUB_ENABLE_HF_TRANSFER", "HF_HOME", "HF_ENDPOINT")}
    os.environ["HF_ENDPOINT"] = "https://example.com"
    try:
        asr._apply_env_defaults()
        assert os.environ["HF_ENDPOINT"] == "https://example.com", "不应覆盖已设的 HF_ENDPOINT"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_env_mirror_autoset_when_hf_unreachable():
    """huggingface.co 不可达时，自动把 HF_ENDPOINT 指向镜像。"""
    saved = os.environ.get("HF_ENDPOINT")
    os.environ.pop("HF_ENDPOINT", None)
    with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("timeout")):
        asr._apply_env_defaults()
    try:
        assert os.environ["HF_ENDPOINT"] == "https://hf-mirror.com", "不可达应自动走镜像"
    finally:
        if saved is None:
            os.environ.pop("HF_ENDPOINT", None)
        else:
            os.environ["HF_ENDPOINT"] = saved


def test_env_mirror_skipped_when_hf_reachable():
    """huggingface.co 可达时不强制改 HF_ENDPOINT（尊重用户/环境已有配置）。"""
    saved = os.environ.get("HF_ENDPOINT")
    os.environ.pop("HF_ENDPOINT", None)
    fake_resp = mock.MagicMock(status=200)
    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        asr._apply_env_defaults()
    try:
        assert "HF_ENDPOINT" not in os.environ or os.environ["HF_ENDPOINT"] != "https://hf-mirror.com", \
            "可达时不应强改 ENDPOINT"
    finally:
        if saved is None:
            os.environ.pop("HF_ENDPOINT", None)
        else:
            os.environ["HF_ENDPOINT"] = saved


# ---------------------------------------------------------------------------
# 2) 转写结果缓存（断点续跑）
# ---------------------------------------------------------------------------

def test_transcript_cache_path_bilibili():
    """B站链接 → transcripts/<BV号>.md。"""
    p = asr._transcript_cache_path("https://www.bilibili.com/video/BV1qfdaBtE95")
    assert p.endswith(os.path.join("transcripts", "BV1qfdaBtE95.md")), p


def test_transcript_cache_path_youtube():
    """YouTube 链接 → transcripts/<规范化key>.md（稳定、可命中）。"""
    url = "https://www.youtube.com/watch?v=abcd1234"
    p1 = asr._transcript_cache_path(url)
    p2 = asr._transcript_cache_path(url)
    assert p1 == p2, "同链接应解析到同一缓存路径（断点续跑前提）"
    assert "transcripts" in p1 and p1.endswith(".md")


def test_load_cached_transcript():
    """命中非空缓存返回文本；缺失返回 None。"""
    url = "https://www.bilibili.com/video/BVtestcache999"
    p = asr._transcript_cache_path(url)
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write("测试字幕内容")
        assert asr._load_cached_transcript(url) == "测试字幕内容"
    finally:
        asr.safe_remove_one(p)
    assert asr._load_cached_transcript(url) is None


# ---------------------------------------------------------------------------
# 3) 防御性删除护栏
# ---------------------------------------------------------------------------

def test_safe_remove_one_rejects_glob():
    """glob 通配必须被拒绝（防误删护栏）。"""
    assert asr.safe_remove_one("notes/_raw_*.md") is False
    assert asr.safe_remove_one("notes/*.md") is False
    assert asr.safe_remove_one("notes/[abc].md") is False


def test_safe_remove_one_rejects_directory():
    """目录必须被拒绝（只删单个文件）。"""
    assert asr.safe_remove_one("notes") is False


def test_safe_remove_one_deletes_explicit_file(tmp_path):
    """明确指定的单个文件应被删除。"""
    f = os.path.join(str(tmp_path), "explicit_raw.md")
    with open(f, "w", encoding="utf-8") as fh:
        fh.write("x")
    assert asr.safe_remove_one(f) is True
    assert not os.path.exists(f)


def test_safe_remove_one_missing_file_is_noop(tmp_path):
    """不存在的文件返回 False 且不抛错。"""
    assert asr.safe_remove_one(os.path.join(str(tmp_path), "nope.md")) is False


# ---------------------------------------------------------------------------
# 4) 模型解析 + symlink 护栏
# ---------------------------------------------------------------------------

def test_resolve_repo_id_known_size():
    assert asr._resolve_repo_id("medium") == "Systran/faster-whisper-medium"
    assert asr._resolve_repo_id("large-v3") == "Systran/faster-whisper-large-v3"


def test_resolve_repo_id_passthrough_full_id():
    assert asr._resolve_repo_id("user/custom-model") == "user/custom-model"


def test_resolve_local_model_dir_uses_real_files_not_symlinks():
    """强制 local_dir_use_symlinks=False（绕开 Windows 沙箱 symlink 坑）。"""
    calls = {}
    with mock.patch.object(asr, "_HAS_HF_HUB", True), \
         mock.patch.object(asr, "snapshot_download",
                           side_effect=lambda *a, **kw: calls.update(kw)) as sd, \
         mock.patch("os.path.exists", return_value=False), \
         mock.patch("os.makedirs"):
        asr._resolve_local_model_dir("medium")
    assert calls.get("local_dir_use_symlinks") is False, "必须落真实文件，禁用 symlink"
    assert sd.called


# ---------------------------------------------------------------------------
# 5) 设备解析
# ---------------------------------------------------------------------------

def test_resolve_device_explicit():
    assert asr._resolve_device("cpu") == ("cpu", "int8")
    assert asr._resolve_device("cuda") == ("cuda", "float16")


def test_resolve_device_auto_sees_cuda():
    # 直接验证内部逻辑：mock ctranslate2 返回 1 个 CUDA 设备
    with mock.patch("ctranslate2.get_cuda_device_count", return_value=1):
        dev, ct = asr._resolve_device("auto")
    assert (dev, ct) == ("cuda", "float16")


def test_resolve_device_auto_cpu_fallback():
    import videos.asr as a
    with mock.patch("ctranslate2.get_cuda_device_count", return_value=0):
        dev, ct = a._resolve_device("auto")
    assert (dev, ct) == ("cpu", "int8")


if __name__ == "__main__":
    test_env_defaults_fill_missing()
    print("[PASS] test_env_defaults_fill_missing")
    test_env_defaults_do_not_override_explicit()
    print("[PASS] test_env_defaults_do_not_override_explicit")
    test_env_mirror_autoset_when_hf_unreachable()
    print("[PASS] test_env_mirror_autoset_when_hf_unreachable")
    test_env_mirror_skipped_when_hf_reachable()
    print("[PASS] test_env_mirror_skipped_when_hf_reachable")
    test_transcript_cache_path_bilibili()
    print("[PASS] test_transcript_cache_path_bilibili")
    test_transcript_cache_path_youtube()
    print("[PASS] test_transcript_cache_path_youtube")
    test_load_cached_transcript()
    print("[PASS] test_load_cached_transcript")
    test_safe_remove_one_rejects_glob()
    print("[PASS] test_safe_remove_one_rejects_glob")
    test_safe_remove_one_rejects_directory()
    print("[PASS] test_safe_remove_one_rejects_directory")
    # 用临时目录手动替代 pytest 的 tmp_path fixture
    import tempfile
    _d = tempfile.mkdtemp()
    test_safe_remove_one_deletes_explicit_file(_d)
    print("[PASS] test_safe_remove_one_deletes_explicit_file")
    test_safe_remove_one_missing_file_is_noop(_d)
    print("[PASS] test_safe_remove_one_missing_file_is_noop")
    test_resolve_repo_id_known_size()
    print("[PASS] test_resolve_repo_id_known_size")
    test_resolve_repo_id_passthrough_full_id()
    print("[PASS] test_resolve_repo_id_passthrough_full_id")
    test_resolve_local_model_dir_uses_real_files_not_symlinks()
    print("[PASS] test_resolve_local_model_dir_uses_real_files_not_symlinks")
    test_resolve_device_explicit()
    print("[PASS] test_resolve_device_explicit")
    test_resolve_device_auto_sees_cuda()
    print("[PASS] test_resolve_device_auto_sees_cuda")
    test_resolve_device_auto_cpu_fallback()
    print("[PASS] test_resolve_device_auto_cpu_fallback")
    print("\n✅ ASR 兜底链路回归测试全部通过")
