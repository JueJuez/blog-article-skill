"""tests 公共路径引导：保证 pytest 在任意 cwd 下可导入项目包与脚本目录模块。

monitors/run.py 以 `python monitors/run.py` 运行时靠脚本目录（sys.path[0]）导入
status_store 等同目录模块；pytest 以包形式导入 monitors.run 时需在此显式补齐，
否则 test_scys_daily / test_pending_prompt_precompute 等直接收集失败。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
for _p in (BASE_DIR, BASE_DIR / "monitors", BASE_DIR / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
