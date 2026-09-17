# -*- coding: utf-8 -*-
"""统一的环境变量加载（.env → os.environ）。

2026-09-18 抽出：此前 `scripts/` 下有 5 份各自实现的 `load_env`（`_run_with_env.py`、
`_save_summary_from_file.py`、`resum_save_batch.py`、`fetch_bili_by_bvids.py`、
`land_scys_by_key.py`），行为略有差异（有的额外强制 Obsidian 开关）。口径散落会导致
「同一个脚本换个入口跑行为不一样」，故收敛为一份 + 显式开关。

⚠️ 本模块**只依赖标准库**：部分调用方要求在 `import articles` **之前**完成注入
（`articles/feishu.py` 在 import 期读环境变量），因此这里绝不能引入项目内任何模块。
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(env_path: str = "", force_obsidian: bool = False) -> bool:
    """把 `.env` 注入 `os.environ`（已存在的键不覆盖）。

    Args:
        env_path: 显式指定 .env 路径；留空则用项目根目录下的 `.env`。
        force_obsidian: True 时额外强制 `OBSIDIAN_WRITE=1` + `DISABLE_FEISHU_SYNC=1`
            （落盘类脚本的常态：默认只写本地 Obsidian，见 RULES.md 红线）。
            False（默认）时不动这两个开关，由调用方/子命令自行决定——
            `scripts/_run_with_env.py` 转发子命令时必须保持 False。

    Returns:
        是否真的读到了 .env 文件。
    """
    p = Path(env_path) if env_path else ROOT / ".env"
    if not p.exists():
        if force_obsidian:
            os.environ["OBSIDIAN_WRITE"] = "1"
            os.environ["DISABLE_FEISHU_SYNC"] = "1"
        return False
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if force_obsidian:
        os.environ["OBSIDIAN_WRITE"] = "1"
        os.environ["DISABLE_FEISHU_SYNC"] = "1"
    return True
