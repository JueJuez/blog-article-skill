# -*- coding: utf-8 -*-
"""scripts/run_with_env.py — 加载项目根 .env 后再执行后续命令（CLI 入口）。

用法:
    python scripts/run_with_env.py -- <command> [args...]

为什么需要：`articles/main.py`、`scripts/fetch_up_range.py`、`_save_summary.py` 等落盘/抓取
脚本**自己不读 `.env`**，而 `.env` 里的 `OBSIDIAN_WRITE=1` / `DISABLE_FEISHU_SYNC=1` 决定落盘
目标、`BILI_COOKIE` 决定能否拿到 B站登录态。直接跑会静默丢这些开关。

实现已收敛到 `shared.env.run_with_env()`（单一真源，2026-09-20）；本文件只是薄壳。

⚠️ 前身是 `scripts/_run_with_env.py`。那个名字以 `_` 开头，被 `.gitignore:53` 的
`scripts/_*` 忽略 ⇒ 未入库，却在 `launch_backfill_series_detached.py` /
`launch_fetch_up_detached.py` 里被 subprocess 调用 ⇒ **克隆/换机后这些长任务入口全部
rc=1 秒退**。改名去掉下划线前缀后即正常入库。旧名保留为转发壳（已标弃用）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# ⚠️ 必须显式把 ROOT 放进 sys.path：本文件以 `python scripts/run_with_env.py` 方式启动时
# sys.path[0] 是 scripts/，`from shared.env import ...` 会 ModuleNotFoundError（2026-09-20 实修）。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.env import run_with_env  # noqa: E402


def main() -> int:
    if "--" in sys.argv:
        cmd = sys.argv[sys.argv.index("--") + 1:]
    else:
        cmd = sys.argv[1:]
    if not cmd:
        print("用法: python scripts/run_with_env.py -- <command> [args...]", file=sys.stderr)
        return 2
    return run_with_env(cmd)


if __name__ == "__main__":
    sys.exit(main())
