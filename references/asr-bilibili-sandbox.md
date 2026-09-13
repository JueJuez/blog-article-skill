# ASR 转写：B站/远程视频沙箱踩坑与固化

> 维护位置：`videos/asr.py`（代码固化）+ 本文件（知识固化）
> 最近更新：2026-09-13（两个 UP 主合集抓取，5 篇无字幕视频走 ASR 兜底时连踩三坑，已全部固化）

## 背景

2026-09-13 抓取冯新思想【组织权力论】、博士阿辉混社会【手段方法论】【社会人修行指南】
等合集，47 篇里有 5 篇无 CC/AI 字幕，需走 ASR 兜底转写。在沙箱环境下连踩三个坑，
修复已全部固化进 `videos/asr.py`，本文件记录根因与验证，避免未来重踩。

## 踩坑清单

### 坑 1：B站音频 CDN 域名轮换 → ffmpeg 直下报 -138 崩溃

- **现象**：ffmpeg 直连 B站音频 CDN 抽取 `.m4s` 时，报 `Error number -138`
  （连接/TLS 打不开输入），进程直接退出，无 Python traceback。
- **根因**：B站音频 CDN 域名会轮换（`estgoss` 被沙箱网关拦截、`mirrorali` 等可达）。
  ffmpeg 直下抽到坏 host 就崩；而 yt-dlp 取直链（仅几个小 API 请求）可达，
  urllib 直连多数 host 也可达。不是"全盘不可达"，是 ffmpeg 网络栈抽到坏 host 才崩。
- **修复**（`videos/asr.py`）：
  - 新增 `_download_audio_urllib()`：用 Python `urllib` 下载音频直链到本地**原始容器**
    （m4s），抽到坏 host（`urlopen` 抛错）时自动重新取直链（换 host）重试，最多 8 次。
  - `extract_audio()` 远程分支优先走「urllib 下载 + ffmpeg 本地转码」；ffmpeg 直下
    保留为最后兜底。
  - 关键点：下载到本地容器后用 ffmpeg **本地**转码成 wav（无网络依赖，不可能因 CDN 崩）。

### 坑 2：长音频（2h+）整段塞 GPU → CUDA 原生段错误

- **现象**：2 小时+ 视频把整段 wav 一次性喂给 faster-whisper GPU 推理，进程**无 Python
  traceback 直接崩溃**（连硬超时看门狗都救不了，因为它发生在原生层）。
- **根因**：整段长音频推理时 VRAM 占用无界，触发 CUDA 原生层段错误。短视频（<30min）
  不受影响（小模型整段也吃得下）。
- **修复**（`videos/asr.py`）：
  - 新增 `_wav_duration()`（读 wav header 时长）/ `_ffmpeg_segment()`（ffmpeg `-f segment
    -c copy` 无重编码切片，极快）/ `transcribe_audio_chunked()`。
  - `transcribe_audio_chunked()`：wav 超过 `auto_threshold`（默认 30min）自动按
    `segment_sec`（默认 600s）切片，逐段转写并拼接绝对时间戳；短音频走原整段快路径。
  - `transcribe_video()` 改调分片版。切片后每段 VRAM 占用有界，彻底规避崩溃。

### 坑 3：nvidia CUDA 运行库（cublas/cudnn dll）路径

- **现象**：本机有 GPU（ctranslate2 能看到 CUDA 设备），但加载模型报
  `Library cublas64_12.dll is not found or cannot be loaded`，随后回退 CPU（极慢）。
- **根因**：管理版 Python 解释器没把 pip 安装的 nvidia-* 运行库（nvidia-cublas-cu12 等
  wheel，放在 `site-packages/nvidia/<pkg>/bin`）纳入 DLL 搜索路径。
- **修复**（`videos/asr.py`）：导入期由 `_ensure_cuda_dlls()` 自动扫描并
  `os.add_dll_directory()` + 前置 PATH（幂等、持有句柄防 GC 移除）。**无需手动 export，
  之前那次手动 `export PATH` 已是多余动作**。

## 验证记录

- 短/中视频（组织权力论 2 篇、手段方法论 1 篇）无 CC 字幕 → ASR（GPU medium）转写成功。
- 两个 2h+ 超大视频（`BV17bxKeBEpo` 2h20m、`BV1MMxQeSEi1` 2h12m）→ **分片转写成功**，
  各约 5 万字，GPU 全程稳定无崩溃（各切 ~14 片）。
- 临时调试脚本 `_tmp/asr_one.py` 的 urllib 下载 + 分片逻辑已合并进 `videos/asr.py`，
  该临时脚本已删除。

## 用法

- 单视频 ASR 兜底：`from videos.asr import transcribe_video; transcribe_video(url)`
  （自动 CC → ASR）。
- 批量无字幕视频：保持 `BILI_BATCH_NO_ASR=1` 跳过无字幕视频；仅对确需文本的无字幕视频
  单独 `transcribe_video(url)` 或 `transcribe_to_file(url, out_path)`。
- 长音频自动分片无需干预；如需调阈值/片长：
  `transcribe_audio_chunked(..., auto_threshold=1800, segment_sec=600)`。
- 断点续跑：转写文本缓存到 `transcripts/<bvid>.md`，非强制模式下命中缓存即跳过下载/转写。
