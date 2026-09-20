#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""series_fetch_detached.py — 系列课补齐监督者（**已归档，勿用**）。

> ⛔ **2026-09-20 15:xx 归档**：本脚本的前提「faster-whisper CUDA/CPU 模型加载必崩、
> 只能强制 CPU + 反复重生工人」**已被推翻**。真实根因是 ctranslate2 **4.8.2** 在本机
> 构造模型即原生 access violation（CPU 与 CUDA 都崩），降回 **4.5.0** 即好；另有
> onnxruntime 1.29.0 坏包（DLL 初始化失败）与 MKL/ctranslate2 的 OpenMP 重复运行时
> 两个叠加故障。现行做法：环境修好后直接用 `scripts/launch_backfill_series_detached.py`，
> 依赖红线与排错顺序见 `references/asr-bilibili-sandbox.md`「本机运行环境版本要求」。
> 保留此文件仅为事故留档。

## 以下为归档时的原始说明（描述的是一个已修复的环境）

背景（2026-09-20 凌晨实踩）：本机 faster-whisper CUDA 模型加载必崩（access
violation，transcribe.py:689），CPU 路径也偶发同型原生崩溃——原生崩溃无法被
try/except 捕获，直接杀死整个抓取进程，无人值守下靠 30 分钟巡检重启太慢。

因此本脚本改成「监督者 + 工人」两层：
  - 监督者（本进程，WMI 拉起，不碰网络）：组一次触发清单 → 循环 spawn 工人
    （backfill_series 子进程，PYTHONFAULTHANDLER=1）；
  - 工人崩溃 → 记录日志 → 等 60s → 重生（登记表+队列自动去重，断点续抓）；
  - 工人「零进展退出」（反复死在同一集）→ 从日志抓最后一个「开始抓取」锚点，
    把该毒集 D8 计数直接抬到 3（转人工），继续推进后面的集；
  - 全部触发处理完（工人正常退出）→ 结束。

拉起方式（唯一规范，见 notes/_scraped/_series_redo_HANDOFF.md「重启抓取」）：
    powershell -NoProfile -Command "Invoke-CimMethod -ClassName Win32_Process \
      -MethodName Create -Arguments @{CommandLine='D:\\App\\anaconda3\\python.exe -u \
      \"D:\\Code\\Skills\\blog-article-skill\\scripts\\series_fetch_detached.py\"'}"
"""
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "notes", "_scraped", "_series_backfill_20260920.log")
FAILS = os.path.join(ROOT, "notes", "_scraped", "series_backfill_failures.json")
EXCLUDED = {"合集·小猪仔与生活", "合集·动画片：小猪仔学投资"}  # 用户 2026-09-19 去掉，永不补
WORKER_GAP = os.environ.get("BILI_GAP", "30")
MAX_ROUNDS = 300          # 防失控上限（每轮至少处理若干集）
RESPAWN_PAUSE = 60        # 工人崩溃后等待秒数

# 1) 日志重定向（WMI 拉起的进程无控制台句柄：dup2 会踩「句柄无效」，改 Python 层整体替换）
logf = open(LOG, "a", encoding="utf-8", buffering=1)
lograw = open(LOG, "a", encoding="utf-8", buffering=1)  # 原始句柄：显式传给工人子进程
try:
    os.dup2(logf.fileno(), 1)
    os.dup2(logf.fileno(), 2)
except OSError:
    pass  # WMI 环境下标准句柄无效，dup2 可能失败；工人靠显式传句柄兜底
sys.stdout = logf
sys.stderr = logf
os.chdir(ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

print("########## series_fetch_detached 监督者启动 %s pid=%s ##########"
      % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid()), flush=True)

# 2) 加载 .env（BILI_COOKIE 等抓取凭据，工人子进程继承）
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(ROOT, ".env"))

# 2.5) ASR 强制 CPU：CUDA 模型加载必崩（见文件头）；ASR_DEVICE 环境变量代码未读，
# 只能 monkeypatch。CPU 也偶发同型崩溃 → 由监督者重生循环 + 毒集转人工兜底。
import videos.asr as _asr  # noqa: E402
_asr._resolve_device = lambda device="auto": ("cpu", "int8")


def _log(s):
    print(s, flush=True)


def _queue_count():
    try:
        return len(json.load(open(os.path.join(ROOT, "monitors/pending_summaries.json"),
                                  encoding="utf-8")))
    except Exception:
        return 0


def _build_triggers():
    """每个有缺口的系列取第一个未登记、未在队列的集。"""
    import backfill_series  # noqa: E402
    audit = json.load(open(os.path.join(ROOT, "notes/_scraped/_series_audit_20260919.json"),
                           encoding="utf-8"))
    reg = json.load(open(os.path.join(ROOT, "notes/_meta/summary_registry.json"),
                         encoding="utf-8"))
    reg_bv = set()
    for v in reg.values():
        if not v.get("summarized_at"):
            continue
        m = re.search(r"BV[0-9A-Za-z]{10}", (v.get("source_url") or ""))
        if m:
            reg_bv.add(m.group(0))
    q = json.load(open(os.path.join(ROOT, "monitors/pending_summaries.json"), encoding="utf-8"))
    q_bv = {re.search(r"BV[0-9A-Za-z]{10}", e["url"]).group(0) for e in q}
    triggers, total = [], 0
    for up, seasons in audit.items():
        for sname, eps in seasons.items():
            if sname in EXCLUDED:
                continue
            miss = [e["bvid"] for e in eps if e["bvid"] not in reg_bv and e["bvid"] not in q_bv]
            if miss:
                triggers.append("https://www.bilibili.com/video/" + miss[0])
                total += len(miss)
    return triggers, total


def _poison_last_anchor():
    """从日志抓最后一个「开始抓取」锚点，把该毒集 D8 计数抬到 3（转人工）。"""
    try:
        lines = open(LOG, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return None
    anchor = None
    for l in reversed(lines):
        m = re.match(r"\[backfill\] 开始抓取: (\S+)", l.strip())
        if m:
            anchor = m.group(1)
            break
    if not anchor:
        return None
    try:
        data = json.load(open(FAILS, encoding="utf-8"))
    except Exception:
        data = {}
    data[anchor] = {"fail_count": 3,
                    "last_error": "监督者判定毒集：反复原生崩溃（转人工，白天排查 ASR 后可清账本重试）",
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    json.dump(data, open(FAILS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return anchor


# 毒集判定（2026-09-20 修正）：模型加载崩溃是随机的（每次工人重生第一次 ASR 加载约
# 五成概率崩，谁排前谁背锅），单轮零进展不构成毒集证据。需连续 POISON_STREAK 轮
# 零进展且锚点同一集才判毒集转人工。
POISON_STREAK = 3


# 3) 组触发清单（一次；工人每次重生会按登记表+队列自动跳过已完成集）
triggers, total = _build_triggers()
_log("[supervisor] 触发系列 %d，预计补抓 %d 集" % (len(triggers), total))
if not triggers:
    _log("[supervisor] 无需补抓，退出")
    sys.exit(0)

# 4) 监督循环
zero_streak, last_zero_anchor = 0, None
cmd = [sys.executable, "-u", "-X", "faulthandler",
       os.path.join(ROOT, "scripts", "backfill_series.py")]
for u in triggers:
    cmd += ["--url", u]
cmd += ["--gap", WORKER_GAP]
env = dict(os.environ)
env["PYTHONFAULTHANDLER"] = "1"
# WMI 环境下子进程 stdout 按 locale（GBK）包装，打印 ℹ️/emoji 直接 UnicodeEncodeError，
# 导致 ASR 全军覆没（「字幕抓取失败（含 ASR 兜底无果）」的真正根因之一）——强制 UTF-8。
env["PYTHONUTF8"] = "1"
env["PYTHONIOENCODING"] = "utf-8"
# 强制工人 ASR 走 CPU（asr.py 现已真正读取 ASR_DEVICE；本机 CUDA 模型加载必崩）
env["ASR_DEVICE"] = "cpu"

for rnd in range(1, MAX_ROUNDS + 1):
    before = _queue_count()
    _log("[supervisor] 第 %d 轮工人启动 %s" % (rnd, time.strftime("%H:%M:%S")))
    try:
        # 显式传日志句柄：WMI 环境下工人继承的 fd1/2 无效，直接写会 0xC0000005 崩溃
        r = subprocess.run(cmd, env=env, stdout=lograw, stderr=subprocess.STDOUT)
        rc = r.returncode
    except Exception as exc:  # noqa: BLE001
        _log("[supervisor] 工人启动异常: %r" % exc)
        rc = -1
    after = _queue_count()
    if rc == 0:
        _log("[supervisor] 工人正常完成（rc=0），全部触发处理完毕")
        break
    _log("[supervisor] 工人异常退出 rc=%s，队列 %s→%s（+%d）"
         % (rc, before, after, after - before))
    if after <= before:
        # 零进展：锚点同一集连续 POISON_STREAK 轮才判毒集（崩溃是随机的，别冤枉集）
        anchor = None
        try:
            lines = open(LOG, encoding="utf-8", errors="replace").read().splitlines()
            for l in reversed(lines):
                m = re.match(r"\[backfill\] 开始抓取: (\S+)", l.strip())
                if m:
                    anchor = m.group(1)
                    break
        except OSError:
            pass
        if anchor and anchor == last_zero_anchor:
            zero_streak += 1
        else:
            zero_streak, last_zero_anchor = 1, anchor
        _log("[supervisor] 零进展锚点 %s（连续 %d/%d 轮）"
             % (anchor, zero_streak, POISON_STREAK))
        if zero_streak >= POISON_STREAK and anchor:
            _poison_last_anchor()
            _log("[supervisor] 连续 %d 轮同一锚点，毒集转人工: %s" % (zero_streak, anchor))
            zero_streak, last_zero_anchor = 0, None
    else:
        zero_streak, last_zero_anchor = 0, None
    _log("[supervisor] %ds 后重生" % RESPAWN_PAUSE)
    time.sleep(RESPAWN_PAUSE)
else:
    _log("[supervisor] 达到最大轮次 %d，收工" % MAX_ROUNDS)

_log("[supervisor] ALL DONE %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
