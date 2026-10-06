#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Setup FunASR (Paraformer-zh + fsmn-vad + ct-punc) 作为本地中文特化 ASR 后端。

设计原则（重要）：
- **隔离**：FunASR 依赖 torch，而 torch 会拉 nvidia-cublas-cu12 等 CUDA 运行库，
  pip 一旦升级这些包会砸掉生产环境 faster-whisper 的 ctranslate2 4.5.0 红线。
  因此本脚本把 FunASR 装进**独立 venv**，绝不污染 anaconda3 的生产 whisper 环境。
- **模型同根管理**：三个模型下到 ~/.cache/asr_whisper/funasr_models/，与 whisper 模型
  目录（asr_whisper/asr_models/）同级，方便统一备份/清理。

用法：
  python scripts/setup_funasr.py                  # 建 venv + 装包 + 下模型（全量）
  python scripts/setup_funasr.py --models-only    # 只（重新）下载模型
  python scripts/setup_funasr.py --check          # 校验安装是否可用
  python scripts/setup_funasr.py --no-download    # 只建 venv + 装包，不下模型

启用后端：在运行 ASR 时设 ASR_BACKEND=funasr（可选 FUNASR_VENV_PY=<venv>/Scripts/python.exe）。
"""
import argparse
import os
import subprocess
import sys

ASR_WHISPER_ROOT = os.path.expanduser("~/.cache/asr_whisper")
FUNASR_MODELS_ROOT = os.path.join(ASR_WHISPER_ROOT, "funasr_models")
DEFAULT_VENV = os.path.expanduser("~/.venvs/funasr")

# (子目录名, ModelScope repo id, revision)
MODELS = [
    ("paraformer-zh", "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch", "master"),
    ("fsmn-vad", "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch", "master"),
    ("ct-punc", "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch", "master"),
]


def _run(cmd, **kw):
    print("+", " ".join(cmd))
    return subprocess.run(cmd, **kw)


def _find_base_python():
    env = os.environ.get("ASR_BASE_PY")
    if env and os.path.exists(env):
        return env
    for cand in (r"D:\App\anaconda3\python.exe",
                 r"C:\Users\O1830\anaconda3\python.exe"):
        if os.path.exists(cand):
            return cand
    return sys.executable


def venv_python(venv):
    return os.path.join(venv, "Scripts", "python.exe")


def create_venv(venv):
    if os.path.exists(venv_python(venv)):
        print(f"[setup] venv 已存在，跳过创建: {venv}")
        return
    base_py = _find_base_python()
    print(f"[setup] 用 {base_py} 创建独立 venv: {venv}")
    os.makedirs(os.path.dirname(venv), exist_ok=True)
    _run([base_py, "-m", "venv", venv], check=True)


def install(venv):
    py = venv_python(venv)
    _run([py, "-m", "pip", "install", "--upgrade", "pip"], check=True)
    # 版本红线（踩坑实测，勿随意升级）：
    # - torch 必须锁 2.8.0+cu126：更新版（2.9+/2.14）在 Win11 上 c10.dll DllMain 崩（WinError 1114），
    #   社区同因，降到 2.8.0 即好；且需从 pytorch 官方 index 拉 CUDA 轮子（pypi 默认是 CPU 版）。
    # - sentencepiece 必须锁 0.1.99：0.2.2 的预编译 wheel 在本机加载即 segfault（access violation），
    #   降级到 funasr 1.4.x 时代标配 0.1.99 即好。
    # - onnxruntime 装 CPU 版作安全网（pytorch 模型推理不强制，但 FunASR 内部某些路径会用到）。
    # - kaldi-native-fbank：FunASR 做 fbank 特征提取的后端（不装 torchaudio 就必须装它，
    #   否则转写时 ImportError: neither torchaudio nor kaldi-native-fbank）。它纯 C++、不依赖 torch 版本。
    _run([py, "-m", "pip", "install", "torch==2.8.0",
          "--index-url", "https://download.pytorch.org/whl/cu126"], check=True)
    _run([py, "-m", "pip", "install", "sentencepiece==0.1.99",
          "funasr", "modelscope", "onnxruntime", "kaldi-native-fbank"], check=True)


def download_models(venv):
    py = venv_python(venv)
    models = MODELS
    root = FUNASR_MODELS_ROOT
    # 注意：modelscope 1.40+ 已移除 local_dir_use_symlinks 参数，只传 local_dir 即可。
    # skip 逻辑改为「目录已存在且非空」才跳过；若目录存在却是半创建的空/残留，则先删再下。
    code = (
        "import os, shutil\n"
        "from modelscope import snapshot_download\n"
        "models = " + repr(models) + "\n"
        "root = " + repr(root) + "\n"
        "for folder, repo, rev in models:\n"
        "    d = os.path.join(root, folder)\n"
        "    if os.path.isdir(d) and any(os.scandir(d)):\n"
        "        print('skip (已存在):', d); continue\n"
        "    if os.path.isdir(d):\n"
        "        shutil.rmtree(d)\n"
        "    os.makedirs(d, exist_ok=True)\n"
        "    print('downloading', repo, '->', d)\n"
        "    snapshot_download(repo, revision=rev, local_dir=d)\n"
        "print('MODELS_DONE')\n"
    )
    _run([py, "-c", code], check=True)


def check(venv):
    py = venv_python(venv)
    proc = subprocess.run(
        [py, "-c",
         "import funasr, torch; "
         "print('funasr ok | torch', torch.__version__, '| cuda', torch.cuda.is_available())"],
        capture_output=True, text=True)
    print(proc.stdout.strip())
    if proc.stderr.strip():
        print("[stderr]", proc.stderr.strip()[:500])
    # 模型目录检查
    print("模型目录:", FUNASR_MODELS_ROOT)
    for folder, _repo, _rev in MODELS:
        d = os.path.join(FUNASR_MODELS_ROOT, folder)
        ok = os.path.isdir(d) and any(os.scandir(d))
        print(f"  {'✅' if ok else '❌'} {folder}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--venv", default=DEFAULT_VENV,
                    help="funasr venv 路径（默认 %(default)s）")
    ap.add_argument("--models-only", action="store_true",
                    help="只下载/校验模型，不建 venv、不装包")
    ap.add_argument("--no-download", action="store_true",
                    help="只建 venv + 装包，不下模型")
    ap.add_argument("--check", action="store_true", help="校验安装")
    args = ap.parse_args()

    if args.check:
        check(args.venv)
        return
    if not args.models_only:
        create_venv(args.venv)
        install(args.venv)
    if not args.no_download:
        download_models(args.venv)

    print("\n[setup] 完成。")
    print(f"[setup] FunASR 模型目录: {FUNASR_MODELS_ROOT}")
    print("[setup] 启用：运行 ASR 前设 ASR_BACKEND=funasr"
          "（若 venv 不在默认位置，另设 FUNASR_VENV_PY）。")


if __name__ == "__main__":
    main()
