# 系列课救回 Runbook（股市系统教学 & 同类 B站 ugc_season 系列）

> 目的：**把"模型在哪、下一步做什么、为什么之前会误判"写成可复用的操作手册**，
> 让任何一次会话（或任何接手的人）都能无歧义地把一个缺失 series 补完，不再踩已踩过的坑。
>
> 配套代码：`videos/rescue_episode.py`（单一救回入口，含 `--check` / `--status`）、
> `shared/series_manifest.py`（状态机 + 缺口检测）、`monitors/apply_pending_series.py`（落盘）。
> 设计决策见 `docs/decisions/DECISION-20260813-series-resilience.md`。

---

## 0. 三句话结论（每次开始前先读）

1. **ASR 模型不在默认缓存** `~/.cache/huggingface`，而在 **`~/.cache/asr_whisper/asr_models/<Org>--faster-whisper-medium/model.bin`**（约 1.46 GB）。扫错目录会误判"模型缺失"。
2. **救回必须走 Bash 工具的 `run_in_background:true` 启动**，绝不能 `nohup ... &`——后者会被会话清理杀掉，表现成"加载模型时静默死亡"，极易误读成模型/cublas 故障。
3. **`ASR_WALL_TIMEOUT` 必须按视频时长定**：medium 在笔记本 GPU 上转写速度 ≤ 实时，1 小时视频至少留 3 小时超时。超时太短会"转写到一半被看门狗杀掉"。

---

## 1. 环境事实（一次性确认，写死在这里）

| 项 | 值 / 位置 | 备注 |
|---|---|---|
| ASR 模型 | `~/.cache/asr_whisper/asr_models/Systran--faster-whisper-medium/model.bin` | 1.46 GB；**不是** `~/.cache/huggingface` |
| ASR 模型解析逻辑 | `videos/asr.py:_resolve_local_model_dir()` | 返回本地目录，不触发 HF 下载 |
| Python 运行时 | `C:\Users\O1830\.workbuddy\binaries\python\envs\default\Scripts\python.exe` | managed venv，含 yt-dlp / faster-whisper / ctranslate2 |
| CUDA 运行库 | venv 内 `Lib\site-packages\nvidia/<pkg>/bin`（cublas / cuda_runtime / cudnn / cufft / cuda_nvrtc / nvjitlink） | **CUDA 12.4** 构建，匹配 ctranslate2 4.8.1 |
| cublas 自动注入 | `videos/asr.py:_ensure_cuda_dlls()` | 把上述 bin 目录加入 `PATH` + `os.add_dll_directory`（句柄持有防 GC） |
| 字幕兜底 cookie | `.env` 的 `BILI_COOKIE` | 影响高清/登录态字幕抓取；缺失不影响 ASR |

> ⚠️ **CUDA 版本铁律**：ctranslate2 是 CUDA 12.4 构建。若 `nvidia-*-cu12` wheel 是 12.9 会 **段错误**；
> 必须用 12.4.x（`cublas 12.4.5.8` / `cuda-runtime 12.4.127` / `cudnn 9.1.0.70` / `cufft 11.2.1.3` / `nvrtc 12.4.127`）。
> 且 cublas 依赖 `cudart64_12.dll`（cuda_runtime/bin），**必须加入所有 CUDA bin 目录**，不能只加 cublas 那一个。

---

## 2. 预检（每次救回前先跑，1 条命令自报全部状态）

```bash
PY="/c/Users/O1830/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
"$PY" -u videos/rescue_episode.py --series "股市系统教学" --page 5 --bvid BV1wtrKB9Ecj --lang zh --check
```

`--check` 会打印：
- ✅/⚠️ ASR 模型路径 + 大小（**直接告诉你模型在哪**）
- ✅/⚠️ CUDA 设备数（cublas 是否注入 PATH）
- ✅/⚠️ ASR 依赖（yt_dlp / faster_whisper / ctranslate2 / imageio_ffmpeg / huggingface_hub）
- ✅/⚠️ BILI_COOKIE
- ℹ️ 视频时长 + **推荐 `ASR_WALL_TIMEOUT`**（时长×3+600）
- 末尾直接给出**可复制的救回命令**

跑完若显示 `✅ 环境就绪，可直接救回`，再进入第 3 步。

---

## 3. 完整流程（救回单集 → 落飞书）

```bash
cd /d/Code/Skills/blog-article-skill
PY="/c/Users/O1830/.workbuddy/binaries/python/envs/default/Scripts/python.exe"

# (1) 预检（见第 2 步），确认环境 + 拿到推荐超时值
# (2) 救回：设定 ASR_WALL_TIMEOUT（按 --check 推荐值，长视频≥10800）
ASR_WALL_TIMEOUT=10800 "$PY" -u videos/rescue_episode.py \
  --series "股市系统教学" --page 5 --bvid BV1wtrKB9Ecj --lang zh --expected-total 38
#   → 产出 notes/股市系统教学/第05集_..._raw.md，并登记 pending_series.json
#   → 关键：--expected-total 38 让 manifest 知道总集数，自动算缺口
```

> ⚠️ **启动方式**：上面的命令要放进 Bash 工具的 `run_in_background:true` 参数里跑，
> **不要**写成 `nohup ... &`。长视频（1h+）转写要 1~3 小时，前台会超时、nohup 会被清。
> 单集各自独立一个后台任务，避免一个超时拖累后续（后台任务总时长超限会连坐杀掉后面的集）。

```bash
# (3) 总结：读 *_raw.md，按 prompts/templates.py 的 KEY_POINTS_PROMPT 写成 .body.md
#     （FORCE_AGENT_MODE=1 时无外部 AI，由执行模型/Agent 直接产出）
#     文件命名由 shared/series_naming.normalized_base 决定：第05集_<part>.body.md
#     写完后把 manifest 该集推进到 summarized：
"$PY" - <<'EOF'
from shared import series_manifest as sm
m = sm.load_or_init("股市系统教学", expected_total=38)
m.set_state(5, sm.SUMMARIZED); m.save()
EOF

# (4) 落盘飞书（分批，每批 5 集，避免一次大批量失败）：
"$PY" -u monitors/apply_pending_series.py --batch 5

# (5) 校验：飞书回读确认节点存在后，manifest 标 verified（reconcile_feishu 自动做）
"$PY" - <<'EOF'
from shared import series_manifest as sm
m = sm.load_or_init("股市系统教学", expected_total=38, reconcile=True)
print(m.summary_line())   # 看缺口是否为空
EOF
```

---

## 4. 状态查询（"漏了哪几集？下一步做什么？"一键回答）

```bash
PY="/c/Users/O1830/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
"$PY" -u videos/rescue_episode.py --series "股市系统教学" --status
```

`--status` 输出 manifest 概览 + **缺口集号列表** + 每集的救回命令（填 BV 即可）。
任何"是不是做完了"的疑问，先跑这条——缺口非空就说明没完，绝不再凭"字典里没 key"误判完成。

---

## 5. 已知坑（本系列实战踩过的，按发生频率）

1. **误判"模型缺失"**：只扫了默认 `~/.cache/huggingface`（空壳 2.5MB），没扫
   `~/.cache/asr_whisper`。→ 永远用 `--check` 看真实模型路径。
2. **`nohup &` 被会话清理杀掉**：表现成"加载模型时静默死亡 / segfault"。→ 用 `run_in_background:true`。
3. **`ASR_WALL_TIMEOUT` 太短**：1h 视频给 3600s → 转写超时。→ 按视频时长×3 设定（≥10800）。
4. **cublas "not found" 偶发**：只加了 cublas/bin，没加 cuda_runtime/bin（cublas 依赖 cudart64_12.dll）。
   → `_ensure_cuda_dlls` 现加入**所有** nvidia/*/bin 目录 + PATH；若仍报，先跑 `--check` 看 CUDA 设备数。
5. **ugc_season 单 P 集误拼 `?p=N`**：每集是独立 BV，拼 `?p=5` 会让 yt-dlp 报"No video formats found"。
   → `fetch.py` 已修：仅当视频确为多 P 且 page 有效才拼 `?p=N`（见 DECISION 文档）。
6. **后台任务总时长连坐**：在一个 bash 脚本里顺序跑 ep5(超时1h)+ep15+ep18，ep5 占满后 ep18 被总时长上限杀掉。
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
运行: /c/Users/O1830/.workbuddy/binaries/python/envs/default/Scripts/python.exe
预检: rescue_episode.py --check  (自报模型/CUDA/依赖/时长+推荐超时)
状态: rescue_episode.py --status  (自报缺口+命令)
救回: ASR_WALL_TIMEOUT=<时长×3> rescue_episode.py --series X --page N --bvid BV --expected-total 38
启动: 必须 Bash run_in_background:true，禁止 nohup &
总结: raw → KEY_POINTS 模板 → .body.md → manifest set_state SUMMARIZED
落盘: monitors/apply_pending_series.py --batch 5
```
