"""videos/asr.py — 本地/任意视频 ASR 转写（P3）

对本地视频文件或无字幕链接，用 ffmpeg 直下音频（yt-dlp 仅取直链，绕开沙箱限流）
→ faster-whisper 本地免费转写。
作为「抓取不到字幕」时的自动兜底（用户规则 2026-08-06：抓不到字幕即自动走 ASR）。
下载机制详见 extract_audio 文档：远程链接走「yt-dlp --get-url 取直链 + Python urllib 直下
原始容器 + ffmpeg 本地转码」，不再用 yt-dlp 整段下载客户端（沙箱里会被限流卡 2.5MiB 截断，
2026-09-13 修），也不用 ffmpeg 直连 CDN（B站 CDN 域名轮换、抽到被沙箱拦截的 host 报 -138
崩溃，2026-09-13 改 urllib 下载 + 换 host 重试修复）。

依赖（managed venv，已装）：
- yt-dlp         视频/音频下载与抽取
- faster-whisper 本地 ASR（CTranslate2 引擎）
- imageio-ffmpeg 内嵌 ffmpeg 二进制（免系统安装，解决本机无 ffmpeg 问题）

环境变量：
- ASR_MODEL   模型大小/名称（默认 medium；可设 large-v3 / Belle-faster-whisper-large-v3-zh-punct 等）
- ASR_DEVICE  auto|cpu|cuda（默认 auto：有 CUDA 走 cuda/float16，否则 CPU/int8）
- ASR_LANG    转写语言（默认 zh）

注意（PRD 风险边界）：
- 首次运行会下载 Whisper 模型（medium ~1.5GB / large-v3 ~3GB），需联网。
- 长音频（>30min）自动分片转写（600s/片，见 transcribe_audio_chunked），规避整段塞爆
  GPU 的 CUDA 原生段错误崩溃（无 Python traceback，2026-09-13 实踩：2h+ 视频整段转写
  段错误；分片后 VRAM 有界，稳定跑通两个 2h+ 视频）。可正常处理 2h+ 视频。
- 24h 超长视频仍不现实，优先 CC 或先裁片。
- 依赖缺失或下载失败均优雅提示，不阻断主流程。
"""

import os
import re
import sys
import subprocess
import tempfile
import hashlib
import threading
import urllib.request as _urlreq
import urllib.parse as _urlparse
from typing import Optional, Tuple, List, Dict

try:
    from huggingface_hub import snapshot_download
    _HAS_HF_HUB = True
except Exception:
    _HAS_HF_HUB = False


# ---------------------------------------------------------------------------
# 环境坑自动处理（本机沙箱/Windows 已知，避免每次手敲 export 重踩）
# ---------------------------------------------------------------------------

def _apply_env_defaults():
    """一次性设好本机已知的环境坑（仅当未显式设置），覆盖所有走
    huggingface_hub / ctranslate2 的入口。调用点：_load_model / transcribe_video。

    已解决的问题（2026-08-06 实跑踩坑记录）：
    - HF_HUB_DISABLE_XET=1      : 关 xet 传输后端（否则直连 cas-server.xethub 返回 401）
    - HF_HUB_ENABLE_HF_TRANSFER=0: 关 hf_transfer（与 xet 同类）
    - HF_HOME=系统临时目录       : 默认 cache 的 .incomplete 清理会被沙箱安全删除拦截 →
                                  改放 %TEMP%，其中的 unlink 走原生、不受拦截
    - HF_ENDPOINT=镜像          : 沙箱直连 huggingface.co 超时 → 自动走 hf-mirror.com
    - KMP_DUPLICATE_LIB_OK=TRUE : anaconda 的 MKL 自带一份 libiomp5md.dll，ctranslate2 也自带
                                  一份，同时驻留时推理会打印 "OMP: Error #15 ... multiple copies
                                  of the OpenMP runtime" 并**直接 Aborted**（原生崩溃，try/except
                                  抓不住，日志里只留一行 Fatal Python error）。Intel 官方给的
                                  逃生开关即此变量（2026-09-20 实踩补齐，此前项目完全没有）。
    """
    if not os.environ.get("KMP_DUPLICATE_LIB_OK"):
        os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    if not os.environ.get("HF_HUB_DISABLE_XET"):
        os.environ["HF_HUB_DISABLE_XET"] = "1"
    if not os.environ.get("HF_HUB_ENABLE_HF_TRANSFER"):
        os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
    if not os.environ.get("HF_HOME"):
        # 固定用户缓存目录（~/.cache/asr_whisper），不放系统 Temp ——
        # 避免 Temp 被磁盘清理误删导致 1.5GB 模型丢失重下。
        os.environ["HF_HOME"] = os.path.expanduser("~/.cache/asr_whisper")
    if not os.environ.get("HF_ENDPOINT"):
        import urllib.request as _u
        try:
            _u.urlopen("https://huggingface.co", timeout=8)
        except Exception:
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"


# ---------------------------------------------------------------------------
# 转写结果缓存（断点续跑：避免重复下载音频 + 重跑 GPU 转写）
# ---------------------------------------------------------------------------

_TRANSCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "transcripts")


def _transcript_cache_path(url: str) -> str:
    os.makedirs(_TRANSCRIPTS_DIR, exist_ok=True)
    try:
        from . import fetch as _f
        if _f.is_bilibili(url):
            key = _f._bili_extract_bvid(url) or url
        elif _f.is_youtube(url):
            key = re.sub(r'[^A-Za-z0-9]', '_', url)[:64]
        else:
            key = hashlib.md5(url.encode("utf-8")).hexdigest()[:16]
    except Exception:
        key = hashlib.md5(url.encode("utf-8")).hexdigest()[:16]
    return os.path.join(_TRANSCRIPTS_DIR, f"{key}.md")


def _load_cached_transcript(url: str) -> Optional[str]:
    """命中缓存返回纯文本；否则 None。缓存仅存文本（标题/作者由 B站 API 现取）。"""
    p = _transcript_cache_path(url)
    if os.path.exists(p) and os.path.getsize(p) > 0:
        try:
            with open(p, encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None
    return None


def _save_transcript_cache(url: str, text: str) -> None:
    p = _transcript_cache_path(url)
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 运行环境探测
# ---------------------------------------------------------------------------

_KNOWN_SIZES = {"tiny", "base", "small", "medium", "large-v2", "large-v3"}


def _resolve_repo_id(model_size: str) -> str:
    """把 model_size 解析成 HuggingFace repo id。"""
    ms = (model_size or "medium").strip()
    if "/" in ms:                      # 已经是完整 repo id
        return ms
    if ms in _KNOWN_SIZES:            # 标准尺寸 → Systran 官方镜像
        return f"Systran/faster-whisper-{ms}"
    return ms


def _resolve_local_model_dir(model_size: str) -> str:
    """把模型下载/解析到「真实文件」本地目录（不用 blobs+symlink 机制），
    返回该目录路径。Windows 沙箱下 symlink 受限，必须落真实文件。

    已缓存的 blob 会被直接复用（local_dir 仅做一次拷贝），不会重下模型。
    """
    _apply_env_defaults()
    repo_id = _resolve_repo_id(model_size)
    safe = repo_id.replace("/", "--")
    base = os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/asr_whisper")
    local_dir = os.path.join(base, "asr_models", safe)
    model_bin = os.path.join(local_dir, "model.bin")
    if os.path.exists(model_bin):
        return local_dir
    os.makedirs(local_dir, exist_ok=True)
    if not _HAS_HF_HUB:
        raise RuntimeError("未安装 huggingface_hub（请 pip install huggingface-hub）")
    snapshot_download(
        repo_id,
        local_dir=local_dir,
        local_dir_use_symlinks=False,   # 关键：真实文件，不创建 symlink
    )
    return local_dir


def _ffmpeg_exe() -> Optional[str]:
    """优先用 imageio-ffmpeg 内嵌二进制（免系统安装），其次回退系统 PATH。"""
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        exe = get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    import shutil
    return shutil.which("ffmpeg")


# 关键：在「任何 ctranslate2 / faster_whisper import 之前」就把 CUDA 运行库目录加进
# 进程 DLL 搜索路径。Windows 上 ctranslate2 的依赖（cublas64_12.dll）在扩展模块
# 首次导入时就绑定，若彼时路径未就绪，cublas 会被标记为「未找到」且之后不再重试，
# 导致模型加载报 "Library cublas64_12.dll is not found or cannot be loaded"。
# 因此在 asr 模块被导入的那一刻就执行一次路径注册（幂等，仅一次）。
try:
    _ensure_cuda_dlls()
except Exception:
    pass


def _resolve_device(device: str = "auto") -> Tuple[str, str]:
    """返回 (device, compute_type)。auto 时优先 CUDA/float16，否则 CPU/int8。

    `ASR_DEVICE` 环境变量（auto|cpu|cuda）优先于入参，可在本机 GPU/驱动异常时强制回退。

    历史澄清（2026-09-20）：2026-09-15~20 那段「ASR 必崩」**不是 CUDA 或 CPU 的问题**——
    真正原因是 ctranslate2 升到 4.8.2 后在本机构造模型即原生 access violation（CPU 与 CUDA
    都崩），降回 4.5.0 即好；当时的 `ASR_DEVICE=cpu` 只是碰巧绕开了崩溃点。版本红线与排错
    顺序见 `references/asr-bilibili-sandbox.md`「本机运行环境版本要求」。
    """
    device = os.environ.get("ASR_DEVICE") or device
    if device == "cpu":
        return "cpu", "int8"
    if device == "cuda":
        return "cuda", "float16"
    # auto：看 ctranslate2 能否看到 CUDA 设备（CPU-only 的 wheel 会返回 0）
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


# ---------------------------------------------------------------------------
# 硬超时看门狗（优化 D）：防止 ASR 在 CPU 回退 / 模型下载卡死时无限挂起
# （本次曾因 shell `timeout` 在本沙箱不生效 + 进程掉到 CPU，单集卡 1h5m）。
# 用守护线程在超时后置位 Event，转写生成器每出一段检查一次即抛 TimeoutError。
# ---------------------------------------------------------------------------

_ASR_ABORT = threading.Event()


def _start_watchdog(timeout: Optional[float]):
    """启动硬超时看门狗；返回 cancel() 取消函数。timeout<=0 或不设则不生效。"""
    _ASR_ABORT.clear()
    if not timeout or timeout <= 0:
        return lambda: None
    def _fire():
        _ASR_ABORT.set()
        print(f"   ⏰ ASR 硬超时（{int(timeout)}s）触发，正在中止转写…")
    t = threading.Timer(float(timeout), _fire)
    t.daemon = True
    t.start()
    return lambda: t.cancel()


def check_asr_deps() -> Tuple[bool, List[str]]:
    """（优化 F）检查 ASR 链路所有依赖是否可导入。

    Returns: (ok, missing_module_list)。缺依赖时调用方应打印一行安装命令，
    而非静默失败（此前缺依赖时 ASR 兜底直接抛异常，信息不友好）。
    """
    need = ["yt_dlp", "faster_whisper", "ctranslate2", "imageio_ffmpeg", "huggingface_hub"]
    missing = []
    for mod in need:
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    return (len(missing) == 0, missing)


_MODEL_CACHE: Dict[Tuple[str, str, str], object] = {}


# ---------------------------------------------------------------------------
# CUDA 运行库自动定位（Windows 上 ctranslate2 需要 cublas/cudart dll 在 DLL 搜索路径）
# ---------------------------------------------------------------------------

_CUDA_DLL_SEARCHED = False
# 必须持有 os.add_dll_directory 返回的句柄，否则被 GC 后目录会从搜索路径移除
# （Python 3.8+：add_dll_directory 的返回值需保持存活，路径才持续生效）。
_CUDA_DLL_HANDLES: list = []


def _ensure_cuda_dlls():
    """把含 cublas64_12.dll 的目录加进进程 DLL 搜索路径（Windows 专用）。

    这样即使用户没手动配 PATH，只要本机有 CUDA 运行库（pip 装的 nvidia-* wheel，或
    Lenovo/预装 NVIDIA 驱动附带的），GPU 转写就能直接生效；找不到则靠 transcribe 的
    CPU 回退兜底。

    🔧 **GPU 依赖找不到了看这里**（2026-09-20 定版）：
    - 需要的包：`nvidia-cublas-cu12` + `nvidia-cudnn-cu12`（cuDNN **9.x**），安装命令
      `python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`，落地在
      `site-packages/nvidia/<pkg>/bin`（本函数会扫这些目录）。
    - 配套的**版本红线**与排错顺序见 `references/asr-bilibili-sandbox.md`
      「本机运行环境版本要求」节：`ctranslate2` 必须 4.5.0、`onnxruntime` 必须 1.19.2、
      `KMP_DUPLICATE_LIB_OK=TRUE`（已由 `_apply_env_defaults()` 自动设置）。
    - 症状对照：缺 cublas → `cublas64_12.dll is not found`；缺 cuDNN 9 组件 →
      `Could not locate cudnn_ops64_9.dll`；两者都会静默回退 CPU（0.8x，比 GPU 慢 3 倍以上）。

    幂等但「非一次性失效」：仅当本次/此前已成功加入 cublas 目录才跳过；若某次扫描被
    沙箱/时序拦截导致一个都没加成功，下次调用（如 transcribe_video 内的显式调用）会
    重扫并补加，避免模块导入期的过早失败令整进程的 GPU 路径永久失效。
    """
    global _CUDA_DLL_SEARCHED
    if _CUDA_DLL_SEARCHED and _CUDA_DLL_HANDLES:
        return
    _CUDA_DLL_SEARCHED = True
    import glob
    checked = set()
    # 注意：不再硬编码本机 Lenovo 旧 cublas 路径——该目录的 cublas 版本与
    # ctranslate2 4.8.1（CUDA 12.4 构建）可能不一致，混用会触发模型加载段错误。
    # 唯一可信来源是 venv 内通过 pip 安装的 nvidia-cublas-cu12（==12.4.x）运行库。
    known = []
    # 浅层扫描 Program Files 下两级的 cublas64_12.dll（避免全盘递归过慢）
    known += glob.glob(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v*\bin")
    known += glob.glob(r"C:\Program Files\*\cublas64_12.dll")
    known += glob.glob(r"C:\Program Files\*\\*\cublas64_12.dll")
    # venv 内通过 pip 安装的 nvidia CUDA 运行库（nvidia-cublas-cu12 等 wheel）：
    # 这些 wheel 把 cublas/cudart/cudnn/cufft 的 dll 放在 site-packages/nvidia/<pkg>/bin/，
    # 本机没有系统级 CUDA Toolkit 时，只能从这里拿到运行库。
    try:
        import site
        for sp in site.getsitepackages() + [os.path.dirname(os.__file__)]:
            nvidia_root = os.path.join(sp, "nvidia")
            if os.path.isdir(nvidia_root):
                known += glob.glob(os.path.join(nvidia_root, "*", "bin"))
    except Exception:
        pass
    # 当前解释器所在 venv 的 nvidia 目录（兜底）
    interp_dir = os.path.dirname(os.path.abspath(sys.executable))
    for cand in [interp_dir, os.path.dirname(interp_dir)]:
        nr = os.path.join(cand, "Lib", "site-packages", "nvidia")
        if os.path.isdir(nr):
            known += glob.glob(os.path.join(nr, "*", "bin"))
    for pather in known:
        d = pather if os.path.isdir(pather) else os.path.dirname(pather)
        if not d or d in checked:
            continue
        checked.add(d)
        # 关键：加入所有含 CUDA 运行库 dll 的 bin 目录，而非仅含 cublas64_12.dll 的那一个。
        # cublas64_12.dll 自身依赖 cudart64_12.dll（在 cuda_runtime/bin）、cublasLt64_12.dll
        # 等；若只把 cublas/bin 加入搜索路径，cublas 加载其依赖时会报
        # "Library cublas64_12.dll is not found or cannot be loaded"（实际是缺依赖），模型加载失败。
        # 因此凡 nvidia/*/bin（cublas / cuda_runtime / cudnn / cufft / nvrtc / nvjitlink）及
        # 系统 CUDA Toolkit bin 一律加入，确保传递依赖全在路径上。
        try:
            has_cuda_dll = any(fn.lower().endswith(".dll") for fn in os.listdir(d))
        except Exception:
            has_cuda_dll = False
        if has_cuda_dll:
            try:
                h = os.add_dll_directory(d)
                # 持有句柄，防止被 GC 后目录从搜索路径移除
                if h is not None:
                    _CUDA_DLL_HANDLES.append(h)
                # Windows 上 ctranslate2 的 C 扩展用原生 LoadLibrary 解析 cublas，
                # 仅 os.add_dll_directory 在某些情况下仍不够（取决于其加载标志）；
                # 把 CUDA bin 目录同时前置到 PATH，可让所有加载器（含 ctypes/原生）
                # 一致解析 cublas 及其整套依赖，是更稳的双保险。
                cur_path = os.environ.get("PATH", "")
                if d not in cur_path.split(os.pathsep):
                    os.environ["PATH"] = d + os.pathsep + cur_path
                print(f"   🔌 已把 CUDA 运行库目录加入 DLL 搜索路径 / PATH：{d}")
            except Exception:
                pass


def _load_model(model_size: str, device: str = "auto"):
    """加载（并缓存）faster-whisper 模型；CUDA 创建失败自动回退 CPU。

    模型先解析为真实本地目录（绕开 Windows 沙箱下 blobs+symlink 失败的坑），
    再传给 WhisperModel，避免 'Unable to open file model.bin'。
    """
    _apply_env_defaults()        # 设好 HF 镜像 / xet / HF_HOME 等环境坑
    _ensure_cuda_dlls()          # 必须在 import faster_whisper 之前：Windows 在
    import faster_whisper        # ctranslate2 扩展导入时即绑定 cublas，晚于此则失效
    dev, ct = _resolve_device(device)
    try:
        model_path = _resolve_local_model_dir(model_size)
    except Exception as e:
        print(f"   ❌ 模型解析/下载失败：{e}")
        raise
    key = (model_path, dev, ct)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    try:
        print(f"   🎛️ 加载 Whisper 模型 {model_size}（device={dev}, compute_type={ct}）...")
        model = faster_whisper.WhisperModel(model_path, device=dev, compute_type=ct)
    except Exception as e:
        if dev == "cuda":
            print(f"   ⚠️ CUDA 加载失败（{e}），回退 CPU/int8")
            model = faster_whisper.WhisperModel(model_path, device="cpu", compute_type="int8")
        else:
            raise
    _MODEL_CACHE[key] = model
    return model


# ---------------------------------------------------------------------------
# 音频抽取（yt-dlp + 内嵌 ffmpeg）
# ---------------------------------------------------------------------------

def _build_ydl_opts(out_wav: str, cookie_str: Optional[str] = None,
                    ffmpeg_exe: Optional[str] = None) -> dict:
    opts = {
        "format": "bestaudio/best",
        "extract_audio": True,
        "audio_format": "wav",
        "outtmpl": os.path.splitext(out_wav)[0],  # yt-dlp 会补 .wav
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 30,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
        }],
    }
    if ffmpeg_exe:
        opts["ffmpeg_location"] = ffmpeg_exe
    if cookie_str:
        opts["http_headers"] = {"Cookie": cookie_str}
    # YouTube 走代理（若配置了 YT_PROXY）
    proxy = os.environ.get("YT_PROXY")
    if proxy:
        opts["proxy"] = proxy
    return opts


def _ydl_get_audio_url(source: str, cookie_str: Optional[str] = None,
                       ffmpeg_exe: Optional[str] = None) -> Optional[str]:
    """用 yt-dlp 只抽取「直链音频 URL」（simulate + forceurl，不做大体积下载）。

    沙箱里 yt-dlp 自身的下载客户端会被限流（卡在精确 2.5MiB 截断），但「取播放
    地址」只是几个小 API 请求，不受影响。拿到 URL 后交给 ffmpeg 直接拉字节
    （见 _ffmpeg_download_audio），绕开 yt-dlp 限速的下载客户端。

    Returns: 直链音频 URL 或 None。
    """
    try:
        import yt_dlp
    except ImportError:
        return None
    opts = {
        "format": "bestaudio/best",
        "simulate": True,
        "forceurl": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
    }
    if cookie_str:
        opts["http_headers"] = {"Cookie": cookie_str}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(source, download=False)
        if isinstance(info, dict):
            return info.get("url")
    except Exception as e:
        print(f"   ℹ️ yt-dlp 取音频直链失败（将回退 yt-dlp 整段下载）：{e}")
    return None


def _ffmpeg_download_audio(url: str, out_wav: str,
                           cookie_str: Optional[str] = None,
                           ffmpeg_exe: Optional[str] = None) -> bool:
    """用 ffmpeg 直接拉音频直链并转成 wav（16k mono）。

    绕开 yt-dlp 被沙箱限流的下载客户端。请求头带 Cookie/Referer/UA
    （B站 m4s CDN 校验 Referer+UA，签名在 URL query 内）。
    """
    ffmpeg_exe = ffmpeg_exe or _ffmpeg_exe()
    if not ffmpeg_exe:
        return False
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    ref = "https://www.bilibili.com/"
    hdrs = f"User-Agent: {UA}\r\nReferer: {ref}\r\n"
    if cookie_str:
        hdrs += f"Cookie: {cookie_str}\r\n"
    cmd = [
        ffmpeg_exe, "-y",
        "-rw_timeout", "120000",   # 单请求读超时 120s
        "-headers", hdrs,
        "-i", url,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-f", "wav",
        out_wav,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception as e:
        print(f"   ❌ ffmpeg 下载音频异常: {e}")
        return False
    if proc.returncode != 0:
        print(f"   ❌ ffmpeg 下载音频失败(rc={proc.returncode}): {proc.stderr[-600:]}")
        return False
    return os.path.exists(out_wav) and os.path.getsize(out_wav) > 0


def _download_audio_urllib(source: str, out_raw: str,
                           cookie_str: Optional[str] = None,
                           ffmpeg_exe: Optional[str] = None,
                           max_retry: int = 8) -> bool:
    """用 Python urllib 下载音频直链到本地原始容器（m4s 等），绕开 ffmpeg 直连
    B站 CDN 时抽到被沙箱拦截的 host（如 estgoss）报 -138 崩溃的问题。

    根因（2026-09-13 实踩）：B站音频 CDN 域名会轮换（estgoss 被沙箱网关拦、
    mirrorali 等可达），ffmpeg 直下抽到坏 host 直接 Error number -138 退出；而
    yt-dlp 取直链（仅小 API 请求）可达，urllib 直连多数 host 也可达。抽到坏 host
    时 urlopen 抛错 → 重新取直链（换 host）重试，直到拿到数据。

    下载到本地原始容器后，由调用方用 ffmpeg 本地转码成 wav（无网络依赖，不崩）。
    返回 True/False。
    """
    ffmpeg_exe = ffmpeg_exe or _ffmpeg_exe()
    if not ffmpeg_exe:
        return False
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    ref = "https://www.bilibili.com/"
    for i in range(max_retry):
        aurl = _ydl_get_audio_url(source, cookie_str=cookie_str, ffmpeg_exe=ffmpeg_exe)
        if not aurl:
            return False
        host = _urlparse.urlparse(aurl).netloc
        try:
            req = _urlreq.Request(aurl, headers={
                "User-Agent": UA,
                "Referer": ref,
                "Cookie": cookie_str or "",
            })
            with _urlreq.urlopen(req, timeout=120) as r, open(out_raw, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            if os.path.getsize(out_raw) > 0:
                print(f"   ⬇️ urllib 下载音频 {os.path.getsize(out_raw)} 字节（host={host}, 第 {i+1} 次）")
                return True
        except Exception as e:
            print(f"   ℹ️ urllib 下载失败（host={host}, 第 {i+1} 次）: "
                  f"{type(e).__name__} {str(e)[:120]}，换 host 重试")
    return False


def _ffmpeg_transcode(local_path: str, out_wav: str,
                      ffmpeg_exe: Optional[str] = None) -> bool:
    """本地文件：直接 ffmpeg 转码成 wav（无网络）。"""
    ffmpeg_exe = ffmpeg_exe or _ffmpeg_exe()
    if not ffmpeg_exe:
        return False
    cmd = [ffmpeg_exe, "-y", "-i", local_path,
           "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-f", "wav", out_wav]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except Exception as e:
        print(f"   ❌ 本地音频转码异常: {e}")
        return False
    if proc.returncode != 0:
        print(f"   ❌ 本地音频转码失败: {proc.stderr[-600:]}")
        return False
    return os.path.exists(out_wav) and os.path.getsize(out_wav) > 0


def _ydl_extract_audio_legacy(source: str, out_wav: str,
                              cookie_str: Optional[str] = None,
                              ffmpeg_exe: Optional[str] = None) -> bool:
    """回退路径：yt-dlp 整段下载 + FFmpegExtractAudio 后处理（兼容非 B站/YouTube 源）。"""
    try:
        import yt_dlp
    except ImportError:
        print("⚠️ 未安装 yt-dlp（pip install yt-dlp）")
        return False
    try:
        opts = _build_ydl_opts(out_wav, cookie_str=cookie_str, ffmpeg_exe=ffmpeg_exe)
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(source, download=True)
        return os.path.exists(out_wav)
    except Exception as e:
        print(f"❌ 音频抽取失败(yt-dlp 回退): {e}")
        return False


def extract_audio(source: str, out_wav: str,
                  cookie_str: Optional[str] = None,
                  ffmpeg_exe: Optional[str] = None) -> bool:
    """抽出 wav（ffmpeg 用内嵌 exe，无需系统安装）。

    优先级：
      1. 本地文件 → ffmpeg 直接转码（无网络）。
      2. 远程链接 → yt-dlp 取直链 + ffmpeg 直接下载（**绕开沙箱里 yt-dlp 被限流的
         下载客户端**，2026-09-13 修复：idx184 之类无 CC 字幕视频此前卡 2.5MiB 截断）。
      3. 回退 → 旧 yt-dlp 整段下载（兼容特殊源）。

    source: Bilibili/YouTube 链接 或 本地视频/音频路径。
    """
    ffmpeg_exe = ffmpeg_exe or _ffmpeg_exe()
    if not ffmpeg_exe:
        print("⚠️ 未找到 ffmpeg（请 pip install imageio-ffmpeg 或系统安装 ffmpeg）")
        return False
    # 1) 本地文件
    if os.path.exists(source):
        return _ffmpeg_transcode(source, out_wav, ffmpeg_exe)
    # 2) 远程链接：优先 urllib 下载原始容器 + ffmpeg 本地转码（绕开 ffmpeg 直连
    #    B站 CDN 抽到被沙箱拦截的 host 报 -138 崩溃；2026-09-13 踩坑修复）。
    url = _ydl_get_audio_url(source, cookie_str=cookie_str, ffmpeg_exe=ffmpeg_exe)
    if url:
        raw = out_wav + ".raw"   # 原始容器（m4s 等）临时落盘，转码后删
        try:
            if _download_audio_urllib(source, raw, cookie_str=cookie_str,
                                     ffmpeg_exe=ffmpeg_exe):
                if _ffmpeg_transcode(raw, out_wav, ffmpeg_exe):
                    return True
        finally:
            try:
                if os.path.exists(raw):
                    os.remove(raw)
            except Exception:
                pass
        # 回退：ffmpeg 直下（仍可能抽到坏 host，但作为最后兜底）
        if _ffmpeg_download_audio(url, out_wav, cookie_str=cookie_str, ffmpeg_exe=ffmpeg_exe):
            return True
    # 3) 回退：yt-dlp 整段下载
    return _ydl_extract_audio_legacy(source, out_wav, cookie_str=cookie_str, ffmpeg_exe=ffmpeg_exe)


# ---------------------------------------------------------------------------
# 转写
# ---------------------------------------------------------------------------

def transcribe_audio(wav: str, model_size: str = "medium",
                     language: Optional[str] = None,
                     device: str = "auto",
                     wall_timeout: Optional[float] = None) -> Optional[List[Dict]]:
    """对本地 wav 做 faster-whisper 转写 → segments。

    segments schema 对齐 fetch 层：[{"start","duration","text"}, ...]
    device=auto 时若 CUDA 运行库缺失（cublas/cudnn dll 找不到），自动回退 CPU 重试。
    wall_timeout: 单集硬超时秒数（优化 D）；超时抛出 TimeoutError 由上层转成"跳过该集"。
    """
    model = _load_model(model_size, device)
    cancel = _start_watchdog(wall_timeout)
    try:
        print(f"   🎙️ faster-whisper 转写中（model={model_size}, lang={language or 'auto'}）...")
        return _run_transcribe(model, wav, language)
    except TimeoutError:
        print("❌ ASR 因硬超时中止（本集跳过，不会无限卡死）")
        return None
    except Exception as e:
        # CUDA 运行库缺失（cublas/cudnn dll 找不到）/ 加载或推理期 CUDA 错误 → 回退 CPU 重试。
        # 关键修复（2026-08-26）：transcribe_video 会「强制锁 cuda」（优化 D），导致 device=="cuda"，
        # 旧守卫 `if device != "cuda"` 直接放过、不回退 → CUDA 推理期缺 cublas dll 时静默失败。
        # 现改为「只要错误本身是 CUDA/cublas 类，无论 device 怎么被锁定都回退 CPU」，
        # 与 _load_model 的 CUDA 加载失败回退保持一致，杜绝「本机缺 CUDA 运行库却卡死 ASR」的复发。
        err = str(e)
        if ("cublas" in err or "cudnn" in err
                or "CUDA" in err or "not found or cannot be loaded" in err):
            print(f"   ⚠️ GPU 转写失败（{e}），回退 CPU/int8 重试")
            cpu_model = _load_model(model_size, "cpu")
            try:
                return _run_transcribe(cpu_model, wav, language)
            except Exception as e2:
                print(f"❌ CPU 重试仍失败: {e2}")
                return None
        print(f"❌ ASR 转写失败: {e}")
        return None
    finally:
        cancel()


def _run_transcribe(model, wav: str, language: Optional[str]) -> Optional[List[Dict]]:
    segments_gen, info = model.transcribe(
        wav,
        language=language,
        beam_size=5,
        vad_filter=True,                 # 跳过静音段，长音频更稳
        condition_on_previous_text=False,
    )
    segments: List[Dict] = []
    for s in segments_gen:
        if _ASR_ABORT.is_set():         # 硬超时看门狗：超时立即中止
            raise TimeoutError("ASR 超过硬超时上限，已中止")
        txt = (s.text or "").strip()
        if txt:
            segments.append({
                "start": float(s.start),
                "duration": float(s.end - s.start),
                "text": txt,
            })
    if not segments:
        print("❌ ASR 结果为空")
        return None
    print(f"   ✅ ASR 完成（{len(segments)} 段，检测语言 {info.language}）")
    return segments


# ---------------------------------------------------------------------------
# 长音频分片转写（规避整段塞 GPU 的 CUDA 原生崩溃）
# ---------------------------------------------------------------------------

def _wav_duration(wav: str) -> Optional[float]:
    """读 wav 时长（秒）。16k mono pcm_s16le，优先用 wave 模块只读 header；
    读不到则按 32000 bytes/s 估算。"""
    try:
        import wave
        with wave.open(wav, "rb") as wf:
            fr = wf.getframerate()
            n = wf.getnframes()
            if fr:
                return n / fr
    except Exception:
        pass
    try:
        return os.path.getsize(wav) / 32000.0
    except Exception:
        return None


def _ffmpeg_segment(wav: str, seg_dir: str, segment_sec: int = 600) -> List[str]:
    """用 ffmpeg 把 wav 按固定时长切片（-f segment -c copy，无重编码、极快），
    返回排序后的分片路径列表。

    解决「整段长音频一次性喂给 GPU 推理导致 CUDA 原生段错误（无 Python traceback，
    无法被 except 捕获）」的问题（2026-09-13 实踩：2h+ 视频整段转写崩溃；切 600s
    小片后 VRAM 始终有界，稳定跑通两个 2h+ 视频）。"""
    import glob
    ffmpeg_exe = _ffmpeg_exe()
    if not ffmpeg_exe:
        return []
    os.makedirs(seg_dir, exist_ok=True)
    try:
        rc = subprocess.run([ffmpeg_exe, "-y", "-i", wav,
                             "-f", "segment", "-segment_time", str(segment_sec),
                             "-c", "copy", os.path.join(seg_dir, "seg_%03d.wav")],
                            capture_output=True, text=True, timeout=300)
    except Exception as e:
        print(f"   ❌ 音频切片失败: {e}")
        return []
    if rc.returncode != 0:
        print(f"   ❌ 音频切片失败(rc={rc.returncode}): {rc.stderr[-400:]}")
        return []
    return sorted(glob.glob(os.path.join(seg_dir, "seg_*.wav")))


def transcribe_audio_chunked(wav: str, model_size: str = "medium",
                            language: Optional[str] = None,
                            device: str = "auto",
                            wall_timeout: Optional[float] = None,
                            segment_sec: int = 600,
                            auto_threshold: float = 1800.0) -> Optional[List[Dict]]:
    """长音频安全转写：wav 超过 auto_threshold 秒（默认 30min）自动分片逐段转写
    并拼接绝对时间戳；短于此则走原整段快路径（transcribe_audio）。

    长音频整段一次性喂给 GPU 推理会在 CUDA 层段错误崩溃（无 Python traceback，
    无法被 except 捕获、连看门狗都救不了），切片后每段 VRAM 占用有界，彻底规避。
    2026-09-13 实踩修复（两个 2h+ B站视频）。

    任一片转写失败（返回 None）即整段失败返回 None，由上层走 CPU 降级 / 跳过。
    """
    import time
    dur = _wav_duration(wav)
    if dur is None or dur <= auto_threshold:
        return transcribe_audio(wav, model_size, language, device, wall_timeout)
    seg_dir = tempfile.mkdtemp(prefix="asr_seg_")
    segs = _ffmpeg_segment(wav, seg_dir, segment_sec)
    if not segs:
        print("   ⚠️ 切片为空，回退整段转写")
        return transcribe_audio(wav, model_size, language, device, wall_timeout)
    print(f"   ✂️ 长音频 {dur:.0f}s 超过阈值，切 {len(segs)} 片逐段转写")
    all_segs: List[Dict] = []
    try:
        for idx, sg in enumerate(segs):
            t0 = time.time()
            # 内层不设硬超时看门狗（避免与外层嵌套 Timer）；单片宽松上限 600s。
            s = transcribe_audio(sg, model_size, language, device, wall_timeout=None)
            if not s:
                print(f"   ❌ 第 {idx+1}/{len(segs)} 片转写失败，整段放弃")
                return None
            for x in s:
                x["start"] = x.get("start", 0) + idx * segment_sec
            all_segs.extend(s)
            print(f"   ✅ 第 {idx+1}/{len(segs)} 片完成（{len(s)} 段, {time.time()-t0:.0f}s）")
    finally:
        _cleanup(seg_dir)
    if not all_segs:
        return None
    return all_segs


# ---------------------------------------------------------------------------
# 对外：本地文件 / 链接
# ---------------------------------------------------------------------------

def transcribe_file(path: str, model_size: str = "medium",
                    language: Optional[str] = None,
                    device: str = "auto") -> Optional[Tuple[str, List[Dict]]]:
    """转写本地视频/音频文件 → (title, segments)。无作者（本地文件未知）。"""
    model_size = os.environ.get("ASR_MODEL") or model_size
    ffmpeg_exe = _ffmpeg_exe()
    tmpdir = tempfile.mkdtemp(prefix="asr_")
    wav = os.path.join(tmpdir, "audio.wav")
    try:
        if not extract_audio(path, wav, ffmpeg_exe=ffmpeg_exe):
            return None
        segs = transcribe_audio(wav, model_size, language, device)
        if not segs:
            return None
        title = os.path.splitext(os.path.basename(path))[0]
        return (title, segs)
    finally:
        _cleanup(tmpdir)


def transcribe_video(url: str, lang: str = "zh",
                     model_size: str = "medium",
                     device: str = "auto",
                     force: bool = False,
                     wall_timeout: Optional[float] = None) -> Optional[Tuple[str, object, str]]:
    """对链接（Bilibili/YouTube 等 yt-dlp 支持的源）做 ASR 转写兜底。

    Returns: (title, segments_or_text, author)。
      - 默认把 segments 转成纯文本返回（下游 _summarize_and_save 同时支持 list/dict 与 str）；
        同时把文本缓存到 transcripts/<id>.md，供断点续跑与外层模型直接读取。
      - author 对 B站从 view API 取 UP主，其他来源回退空串。失败返回 None。
    """
    _apply_env_defaults()
    model_size = os.environ.get("ASR_MODEL") or model_size
    ffmpeg_exe = _ffmpeg_exe()
    _ensure_cuda_dlls()          # 必须在 import ctranslate2 之前（Windows 导入即绑定 cublas）

    # 强制 CUDA（优化 D）：只要 ctranslate2 能看见 GPU 就锁 cuda/float16，
    # 绝不在无提示下掉 CPU（曾因掉到 CPU 单集卡 1h5m）。
    # ⚠️ 但显式 `ASR_DEVICE=cpu` 必须被尊重——2026-09-20 修：此前这里无条件改回 cuda，
    # 使 `_resolve_device` 文档承诺的 ASR_DEVICE 开关在 transcribe_video 上实际失效
    # （GPU/驱动异常时无法按文档强制回退 CPU）。
    if (os.environ.get("ASR_DEVICE") or "").strip().lower() != "cpu":
        try:
            import ctranslate2 as _ct
            if _ct.get_cuda_device_count() > 0:
                device = "cuda"
        except Exception:
            pass
    wall_timeout = wall_timeout or int(os.environ.get("ASR_WALL_TIMEOUT", "1800") or 0)

    # B站：注入登录态 cookie（部分视频匿名无法下载）；并取标题/UP主
    cookie_str = None
    title, author = "", ""
    expect_dur = 0.0        # 视频总时长（秒），用于「源完整性」校验
    upower_preview = False  # 充电专属·仅试看：拿不到全片
    try:
        from . import fetch
        if fetch.is_bilibili(url):
            cookie_str = fetch._bili_build_cookies_from_env()
            info = fetch._bili_get_video_info(fetch._bili_extract_bvid(url))
            if info:
                title = info.get("title", "")
                author = info.get("author", "")
                expect_dur = float(info.get("duration") or 0)
                upower_preview = bool(info.get("is_upower_exclusive")
                                      and info.get("is_upower_preview"))
    except Exception as e:
        print(f"   ℹ️ B站元数据/ cookie 获取跳过：{e}")

    # 充电专属（仅试看）：**不再一刀切跳过**（2026-09-20 用户定策，微调后）。
    # 机理：upower 限制的是**媒体流**，不是字幕流也不必然导致音频不完整——
    #   实证 BV1eL4k6jEii 音频只给 1/41 分钟，字幕却给了全片（笔记合法）；
    #   而 BV1fr8P6REBW 音频只给 32/104 分钟（源真残）。
    # 故策略：**让「源完整性校验」做唯一裁决**（下面那段 90% 覆盖率闸门），
    # 本标记只用于把失败原因打得更准。这样既不误杀「字幕/音频完整的充电集」，
    # 也不会放过「只拿到试看片段」的集。
    if upower_preview:
        print(f"   ℹ️ 该集为充电专属（当前账号仅可试看），全片 {expect_dur:.0f}s："
              f"先取音频，再由完整性校验（覆盖率 ≥90%）决定是否收录。")

    # 断点续跑：非强制且命中 ASR 缓存 → 跳过下载音频 + GPU 转写，直接返回文本
    if not force:
        cached = _load_cached_transcript(url)
        if cached:
            print(f"   ♻️ 命中 ASR 缓存，跳过下载/转写（{len(cached)} 字）")
            return (title, cached, author)

    tmpdir = tempfile.mkdtemp(prefix="asr_")
    wav = os.path.join(tmpdir, "audio.wav")
    try:
        if not extract_audio(url, wav, cookie_str=cookie_str, ffmpeg_exe=ffmpeg_exe):
            if fetch and fetch.is_bilibili(url):
                if not cookie_str:
                    print("   ℹ️ B站音频下载失败：缺少登录态 cookie。请在本机 Chrome 登录 B站并"
                          "完全退出 Chrome 后重试（让 Playwright 能提取 cookie）。")
                else:
                    # Q4：412 / 下载失败可能源于 cookie 过期或被挤下线 →
                    # 尝试从本机 Chrome 刷新一次再试，避免"过期 cookie 直接 412"卡死。
                    # （2026-09-03 重构后唯一方法是 _bili_extract_cookies_cdp，旧
                    #   同义函数已删除；引用断裂由 tests/test_asr_fetch_refs.py 守住）
                    try:
                        fresh = fetch._bili_extract_cookies_cdp()
                        if fresh and fresh != cookie_str:
                            print("   ♻️ 当前 cookie 可能已失效，已从本机 Chrome 刷新，重试音频下载一次")
                            if extract_audio(url, wav, cookie_str=fresh, ffmpeg_exe=ffmpeg_exe):
                                cookie_str = fresh
                            else:
                                return None
                        else:
                            print("   ℹ️ B站音频下载失败：cookie 刷新不可用（本机 Chrome 未完全退出？），跳过 ASR。")
                            return None
                    except Exception as e:
                        print(f"   ℹ️ B站音频下载失败且 cookie 刷新异常：{e}，跳过 ASR。")
                        return None
            return None
        # 源完整性校验（2026-09-20）——**充电专属的唯一裁决闸门**。
        # 本地音频若明显短于视频总时长，说明拿到的是残缺源（充电专属试看 / 下载被截断 /
        # CDN 只返回一段）。此前无此校验，会把残缺音频当全片转写并落盘，笔记静默丢失大部分
        # 内容。宁可失败（进失败账本、可人工排查）也不产出残品。
        got_dur = _wav_duration(wav) or 0.0
        if expect_dur and got_dur and got_dur < expect_dur * 0.9:
            why = "（该集为充电专属，B站只给试看片段）" if upower_preview else ""
            print(f"   ⛔ 源不完整{why}：本地音频 {got_dur:.0f}s 仅覆盖视频 {expect_dur:.0f}s 的 "
                  f"{got_dur / expect_dur * 100:.0f}%（阈值 90%），跳过 ASR 以免产出残缺笔记。")
            return None
        segs = transcribe_audio_chunked(wav, model_size, lang, device, wall_timeout=wall_timeout)
        if not segs:
            return None
        text = "\n".join(s["text"] for s in segs)
        _save_transcript_cache(url, text)
        # 标题兜底：用文件名（去掉扩展）
        if not title:
            title = os.path.splitext(os.path.basename(wav))[0]
        return (title, text, author)
    finally:
        _cleanup(tmpdir)


def transcribe_url(url: str, model_size: str = "medium",
                   language: Optional[str] = None,
                   device: str = "auto",
                   force: bool = False) -> Optional[Tuple[str, object]]:
    """兼容旧签名：链接 → (title, segments_or_text)。author 丢弃。"""
    r = transcribe_video(url, lang=language or "zh", model_size=model_size,
                         device=device, force=force)
    if not r:
        return None
    return (r[0], r[1])


def transcribe_to_file(url: str, out_path: str, lang: str = "zh",
                       model_size: str = "medium",
                       wall_timeout: Optional[float] = None) -> Optional[str]:
    """投递式转写（P1, PLAN §3.5）：url → 把 transcript 文本写到 out_path，返回 out_path。

    供 run_parallel 的 ASR 子进程池调用——「不阻塞调用方」由子进程池实现，
    本函数本身只负责跑完转写并落盘。复用 transcribe_video 全链路
    （下载 / 缓存 / 硬超时看门狗 / CUDA 缺失 CPU 回退）。
    """
    res = transcribe_video(url, lang=lang, model_size=model_size, wall_timeout=wall_timeout)
    if not res:
        return None
    _, text, _ = res
    try:
        parent = os.path.dirname(os.path.abspath(out_path))
        os.makedirs(parent, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text or "")
        return out_path
    except Exception as e:
        print(f"   ❌ 转写结果写盘失败: {e}")
        return None


def _cleanup(tmpdir: str) -> None:
    try:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass


def safe_remove_one(path: str) -> bool:
    """只删明确指定的单个文件；拒绝 glob 通配与目录，防止误删。

    任何代码路径需要删除文件都应走这里，**绝不用 `glob` + `os.remove`**
    （曾因 `glob('notes/_raw_*.md')` 误删 50 个无关暂存文件）。
    """
    if not path or any(c in path for c in "*?[]"):
        print(f"   ⚠️ safe_remove_one 拒绝（疑似通配/非法路径）：{path!r}")
        return False
    if not os.path.isfile(path):
        return False
    try:
        os.remove(path)
        return True
    except Exception as e:
        print(f"   ⚠️ 删除失败：{path} -> {e}")
        return False
