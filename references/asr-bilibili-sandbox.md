# ASR 转写：B站/远程视频沙箱踩坑与固化

> 维护位置：`videos/asr.py`（代码固化）+ 本文件（知识固化）
> 最近更新：2026-09-15（新增「字幕可用性判定 & 硬字幕识别」一节；新增「坑 4：ASR 同进程连续转写必崩」；2026-09-13 的三坑已固化）

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

### 坑 4：同一 Python 进程连续跑 ASR 转写必崩（GPU/Whisper 状态污染）

- **现象**：`backfill_series.py` 批量补抓时，同进程内逐集调 `fetch_transcript` → `transcribe_video`。
  第一集 ASR 成功，第二集起进程**无 Python traceback 直接 exit 1 崩溃**（连 `try/except` 都接不到），
  导致后续无字幕集（第43、152…）抓到音频/转写却没稳定入队。单集单独重试必成功。
- **根因**：faster-whisper（GPU/ctranslate2）在同进程内反复 `load_model` + 推理后，CUDA/cuBLAS
  上下文状态污染（疑似显存句柄未彻底释放、`_ensure_cuda_dlls` 重复 `add_dll_directory` 副作用），
  连续第二次推理触发原生层崩溃。与坑 2（整段长音频）不同——这次是"连续多次调用"触发。
- **规避（当前实操）**：
  - **每个无字幕集用独立 Python 进程**跑 `transcribe_video` / `_backfill_one`——
    进程退出即释放全部 CUDA 状态，下一集全新进程必成功。
  - 批量 run（`BILI_BATCH_NO_ASR=0` 默认 ASR 兜底）若漏集，**不要重跑整批**，
    对漏集逐个独立进程补抓（命中 `transcripts/<bvid>.md` 缓存的集秒级完成）。
  - 已验证：203 集系列课里 5 个真无字幕集，逐进程补抓全部成功（各 1160~1780 字）。
- **代码层根治待做**：`videos/asr.py` 的 `transcribe_video` 应包装子进程隔离或
  `importlib.reload` 重置 CUDA 上下文，避免调用方手动拆进程。

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

## 字幕可用性判定 & 硬字幕识别（2026-09-15）

> 背景：抓「长视频 + 两系列课」时，先用错接口（`x/player/wbi/v2`）把"全有字幕"判成"全无字幕"，
> 又一度要给 4h 长视频跑 ASR，最后发现它是**烤进画面的硬字幕**。本节约掉这类误判。

### 1. 项目实际走哪条字幕链路

- `videos/fetch.py` 的字幕探测用 **`dm/view`**（`x/v2/dm/view?aid=&oid=&type=1`），
  读 `data.subtitle.subtitles` 列表（含 `ai-zh` / `ai-en` / `zh-CN` 等 `lan`）。
  这条对"确实存在的软字幕"判定可靠（系列1 9 集、系列2 多数集都靠它正确识别 ai-zh）。
- **`x/player/wbi/v2` 不可用于字幕判定**。实测它对"明明有 ai-zh 字幕"的视频也返回空，
  会系统性误判成"无字幕"，2026-09-15 那次"三个都无字幕"的错判就源于此。别用它。

### 2. 「API 返回空」≠「视频无字幕」—— 硬字幕陷阱

视频在播放器里**肉眼可见中文字幕**，但 `dm/view` 与字幕专用接口都返回空，这种情况是
**硬字幕（bened-in / 烤进画面）**：字幕是视频帧的一部分，服务器根本没有独立的字幕资源，
任何 API 都抓不到，ASR 也只转得出旁白语音、与屏幕上的字幕文字对不上。

**硬字幕的判定（2026-09-15 对 4h 长视频 `BV1rnh36jEkr` 三重印证）：**

1. `dm/view` → `subtitle.subtitles` 为空
2. 字幕专用接口返回空资产：
   ```
   GET https://api.bilibili.com/x/v2/subtitle/web/view
       ?oid={cid}&pid={aid}&type=1
       &context_ext=%7b%22video_type%22%3a1%7d
       &preferred_language=ai-zh&cur_production_type=0&playlist_switch=0
       &web_location=1315873
   Header: Referer: https://www.bilibili.com/  +  Cookie
   ```
   → HTTP 200 但 body 仅 `{}`（2 字节），即服务端**根本没字幕数据**。
3. CDP 打开页面：`.bpx-player-subtitle-text` DOM 为 null、无 `.bpx-player-subtitle-btn`、
   `video.textTracks == 0`、无字幕菜单。

三条任一满足即高度疑似硬字幕；第 2 条（`subtitle/web/view` 返回 `{}`）+ 第 3 条最铁。

### 3. 决策规则：遇到"无字幕"视频，先别急着 ASR

| 情形 | 现象 | 动作 |
|---|---|---|
| 软字幕存在 | `dm/view` 列得出 `ai-zh` 等 | 直接下字幕，不跑 ASR |
| 真无字幕（非硬字幕） | API 空 + 播放器也确实没字 | 走 ASR 兜底（短集成本低） |
| **硬字幕** | API 空 + **肉眼可见字幕** + `subtitle/web/view` 返回 `{}` | **放弃**，不浪费 ASR（长视频尤甚） |

实操：对"API 空但疑似有字幕"的视频，先用 `subtitle/web/view` 验一次；
返回 `{}` 且页面 DOM 无字幕节点 → 判硬字幕，放弃。只有确认"真无字幕且非硬字幕"才投 ASR。

> **批量补抓的额外陷阱（2026-09-15）**：系列课批量跑 `backfill_series.py` 时，`dm/view`
> 在限流窗口会**偶发返回空**（即便该集真有 `ai-zh` 字幕），被当成"无字幕"跳过 → 漏集。
> 实测 203 集系列课里第99、102集本有 `ai-zh` 字幕却被漏，误判成"需 ASR"。
> **解法**：批量跑完必须对缺口集用 `fetch_subtitle_only(url, lang="zh")` 逐一复核
> （它内部重试更稳，且直接读 `dm/view` 字幕列表），有字幕的直接补抓，真无的才走 ASR。
> 不要轻信"批量报告无字幕"就给 ASR——先复核，避免浪费转写资源。
