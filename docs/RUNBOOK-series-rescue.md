# 系列课救回操作手册（股市系统教学 & 同类 B站 ugc_season）

> 本文件是**操作手册，不是功能清单**。每步都写"跑什么命令 / 调哪个模块的哪个方法"，
> 不让执行模型去猜、去找。配套设计决策见 `docs/decisions/DECISION-20260813-series-resilience.md`。
>
> 设计原则（踩坑后沉淀）：**方法本身是线性的，必须把线性主干写进代码 + 关键事实常量化/自报
> + 文档写"怎么做"而非"有什么功能"**，否则每次执行都要重新推理+寻找，找错一处就误判。

---

## 0. 怎么做（一句话）

**跑 `videos/rescue_episode.py` 一条命令即可**。它内部已写好线性主干，自动按
`A 探测时长算超时 → B 抓字幕 → C(无字幕)ASR → D 写raw+manifest → E 总结` 顺序执行。
你不需要手动拼超时、不需要手动调 fetch/asr——那些都在 rescue 内部**可见的步骤函数**里。

```bash
# 救回第 5 集（ugc_season 每集独立 BV，传 --bvid）
"$PY" -u videos/rescue_episode.py --series "股市系统教学" --page 5 --bvid BV1wtrKB9Ecj --lang zh --expected-total 38
```

---

## 1. 环境事实（写死在这里，别再去找）

| 事实 | 位置 / 值 | 代码里怎么得到 |
|---|---|---|
| **ASR 模型文件** | `~/.cache/asr_whisper/asr_models/Systran--faster-whisper-medium/model.bin`（1.46 GB） | `videos/asr.py:_resolve_local_model_dir("medium")` 返回目录，拼 `model.bin` |
| ⚠️ 不是默认缓存 | **别去 `~/.cache/huggingface` 找**（那是空壳，会误判"模型缺失"） | — |
| Python 运行时 | `C:\Users\O1830\.workbuddy\binaries\python\envs\default\Scripts\python.exe` | managed venv（含 yt-dlp / faster-whisper / ctranslate2） |
| CUDA 运行库 | venv `Lib\site-packages\nvidia/<pkg>/bin`（**CUDA 12.4** 构建，匹配 ctranslate2 4.8.1） | `videos/asr.py:_ensure_cuda_dlls()` 自动把全部 nvidia/*/bin 注入 `PATH` + 句柄持有 |
| 字幕 cookie | `.env:BILI_COOKIE` | 影响高清/登录态字幕；缺失不影响 ASR |
| **飞书写入机制** | 底层 `articles/feishu.py:FeishuOutput` → **`lark-cli`（subprocess, shell=False）** | `from articles.feishu import FeishuOutput; FeishuOutput().explain_mechanism()` 自报全部事实 |
| 面板无关 | 飞书落盘走 `lark-cli`，与面板 `feishu` MCP 连接器是两套独立机制，连接器状态不是落盘判据 | 可用性判据=`is_available()`=`FEISHU_WIKI_SPACE` 已配 + `lark-cli --version` 可执行 |
| 一行自报 | `monitors/apply_pending_series.py --check` | 打印机制 + `is_available()`，落盘前先跑这条确认 |

> ⚠️ **CUDA 12.4 铁律**：ctranslate2 是 CUDA 12.4 构建。`nvidia-*-cu12` wheel 若是 12.9 会**段错误**；
> 必须用 12.4.x（`cublas 12.4.5.8` / `cuda-runtime 12.4.127` / `cudnn 9.1.0.70` / `cufft 11.2.1.3` / `nvrtc 12.4.127`）。
> cublas 依赖 `cudart64_12.dll`（cuda_runtime/bin），**必须加入所有 CUDA bin 目录**，不能只加 cublas 那一个。

---

## 2. 预检（救回前 1 条命令，自报全部状态）

```bash
PY="/c/Users/O1830/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
"$PY" -u videos/rescue_episode.py --series "股市系统教学" --page 5 --bvid BV1wtrKB9Ecj --lang zh --check
```

`--check` 内部依次调用（全部自报，不用你找）：
- `asr._resolve_local_model_dir("medium")` → 打印**模型真实路径 + 大小**
- `asr._ensure_cuda_dlls()` + `ctranslate2.get_cuda_device_count()` → 打印 CUDA 设备数
- `asr.check_asr_deps()` → 打印依赖（yt_dlp / faster_whisper / ctranslate2 / imageio_ffmpeg / huggingface_hub）
- `_video_duration(ep_url)` → 打印视频时长 + **推荐超时**

显示 `✅ 环境就绪，可直接救回` 再继续第 3 步。

---

## 3. 完整流程（操作手册）

### 3.1 救回单集（只需跑这一个命令）

```bash
cd /d/Code/Skills/blog-article-skill
PY="/c/Users/O1830/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
"$PY" -u videos/rescue_episode.py --series "股市系统教学" --page 5 --bvid BV1wtrKB9Ecj --lang zh --expected-total 38
```

> ⚠️ **启动方式**：放进 Bash 工具的 `run_in_background:true` 参数，**禁止 `nohup ... &`**（后者被会话清理杀掉，
> 表现成"加载模型时静默死亡"，极易误读成模型/cublas 故障）。每集各自独立一个后台任务，避免总时长连坐。
> **超时不用手设**：rescue 内部 `_probe_timeout` 会先探测视频时长、按 `时长×3+600` 自动算（如 82min→15389s）。
> 想强制覆盖才设 `ASR_WALL_TIMEOUT=<秒>`。

命令内部实际发生的（你不用管，但要知道走哪步、调什么）：
- **步骤A** `_probe_timeout(ep_url)` → 探测时长 → 算 `ASR_WALL_TIMEOUT`（**前置，在抓取之前**）
- **步骤B** `_step_fetch_subtitle` → 调 `videos/fetch.py:fetch_subtitle_only()`（原生 API → yt-dlp 自动字幕，**不含 ASR**）
- **步骤C** 仅当 B 返回字幕缺失 → `_step_asr` → 调 `videos/asr.py:transcribe_video(url, lang, wall_timeout=算好的值)`，模型取自 `_resolve_local_model_dir`
- **步骤D** 写 `notes/股市系统教学/第05集_..._raw.md` + `shared/series_manifest.py` 状态 `raw_ready` + 登记 `monitors/pending_series.json`
- **步骤E** `_step_summarize` → 返回确切的"下一步"指令（见 3.2）

### 3.2 总结（读 raw → 写 body → 落飞书）

rescue 的"步骤E"返回的指令，照做即可（不再需要猜调什么）：
1. 读 `notes/股市系统教学/第05集_..._raw.md`
2. 调 `articles/main.py:summarize_content(text, note_type="key_points")`，或按 `prompts/templates.py:KEY_POINTS_PROMPT` 由前端模型总结
3. 写入 body 文件，路径由 `shared/series_naming.body_path(raw_abs)` 决定（命名 `第05集_<part>.body.md`，标题 `# 第5集 <part>【股市系统教学（5）】`）
4. 把 manifest 该集推进 `summarized`：
   ```bash
   "$PY" - <<'EOF'
   from shared import series_manifest as sm
   m = sm.load_or_init("股市系统教学", expected_total=38)
   m.set_state(5, sm.SUMMARIZED); m.save()
   EOF
   ```
5. 落飞书（分批，每批 5 集）：
   ```bash
   "$PY" -u monitors/apply_pending_series.py --batch 5
   ```
> **落盘走 lark-cli，与面板 feishu MCP 连接器是两套独立机制**：面板连接器状态不是落盘判据。
> 落盘前若想确认，跑 `"$PY" -u monitors/apply_pending_series.py --check`，看到 `is_available : True` 即可放心落盘；
> 飞书读写永远先测 `lark-cli` 命令行（用户 token 长期有效），无需关注面板连接器连接状态。

---

## 4. 状态查询（"漏了哪几集？下一步做什么？"一键答）

```bash
PY="/c/Users/O1830/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
"$PY" -u videos/rescue_episode.py --series "股市系统教学" --status
```

`--status` 调 `shared/series_manifest.py:gap_pages()` 打印**缺口集号列表** + 每集救回命令（填 BV 即可）。
任何"是不是做完了"的疑问，先跑这条——缺口非空就说明没完，绝不再凭"字典里没 key"误判完成。

---

## 5. 已知坑（本系列实战踩过的，按发生频率）

1. **误判"模型缺失"**：只扫了默认 `~/.cache/huggingface`（空壳），没扫 `~/.cache/asr_whisper`。
   → 永远用 `--check` 看真实模型路径（`_resolve_local_model_dir` 自报）。
2. **`nohup &` 被会话清理杀掉**：表现成"加载模型时静默死亡 / segfault"。→ 用 `run_in_background:true`。
3. **`ASR_WALL_TIMEOUT` 太短**：1h 视频给 3600s → 转写超时。→ 现已**前置自动算**（时长×3+600），无需手设；长视频仍可用环境变量覆盖。
4. **cublas "not found" 偶发**：只加了 cublas/bin，没加 cuda_runtime/bin（cublas 依赖 cudart64_12.dll）。
5. **落盘判据只看 lark-cli**：飞书落盘走 `lark-cli`（`articles/feishu.FeishuOutput`），与面板 feishu MCP 连接器是两套独立机制；判据只看 `is_available()`（`FEISHU_WIKI_SPACE` 已配 + `lark-cli --version` 可执行）。落盘前跑 `apply_pending_series.py --check` 一行自报，看到 `is_available : True` 就落，无需关注面板连接器状态。
   → `_ensure_cuda_dlls` 现加入**所有** nvidia/*/bin 目录 + PATH；若仍报，先跑 `--check` 看 CUDA 设备数。
5. **ugc_season 单 P 集误拼 `?p=N`**：每集是独立 BV，拼 `?p=5` 会让 yt-dlp 报"No video formats found"。
   → `fetch.py` 已修：仅当视频确为多 P 且 page 有效才拼 `?p=N`。
6. **后台任务总时长连坐**：一个 bash 脚本里顺序跑多集，前一集占满后后续被总时长上限杀掉。
   → 每集各自独立 `run_in_background` 任务。
7. **误报"完成"**：reconcile 时缺失集只是"不在字典里"，无任何占位 → 以为全做完了。
   → manifest 记 `expected_total`，`gap_pages()` 显式标缺口；汇报前必跑 `--status`。

---

## 6. 飞书节点核对（最终确认）

若对"飞书里到底在不在"有疑问，用 `articles/feishu.py` 列节点（见 DECISION 文档末的临时脚本思路），
按标题核对 `第NN集` 是否存在。这是比 manifest 更权威的落地证据。

---

## 7. 速查卡（贴墙版）

```
模型: ~/.cache/asr_whisper/asr_models/Systran--faster-whisper-medium/model.bin (1.46G)
      （代码常量：videos/asr.py:_resolve_local_model_dir）
运行: /c/Users/O1830/.workbuddy/binaries/python/envs/default/Scripts/python.exe
预检: rescue_episode.py --check   （自报 模型路径/CUDA/依赖/时长+推荐超时）
状态: rescue_episode.py --status   （自报 缺口集号+命令）
救回: rescue_episode.py --series X --page N --bvid BV --expected-total 38
      （超时自动按 时长×3+600 算，无需手设；覆盖才设 ASR_WALL_TIMEOUT=）
启动: 必须 Bash run_in_background:true，禁止 nohup &
主干: rescue() 内 A探时长→B抓字幕(fetch_subtitle_only)→C无字幕ASR(asr.transcribe_video)→D写raw+manifest→E总结
总结: raw → articles.summarize_content(note_type='key_points') 或 KEY_POINTS_PROMPT → body_path → set_state SUMMARIZED
落盘: monitors/apply_pending_series.py --batch 5
```
