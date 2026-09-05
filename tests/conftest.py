"""tests 公共路径引导：保证 pytest 在任意 cwd 下可导入项目包与脚本目录模块。

monitors/run.py 以 `python monitors/run.py` 运行时靠脚本目录（sys.path[0]）导入
status_store 等同目录模块；pytest 以包形式导入 monitors.run 时需在此显式补齐，
否则 test_scys_daily / test_pending_prompt_precompute 等直接收集失败。
"""
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
for _p in (BASE_DIR, BASE_DIR / "monitors", BASE_DIR / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


@pytest.fixture(autouse=True)
def _isolate_dedup(monkeypatch, tmp_path):
    """守卫（2026-09-05）：任何测试不得写真实 .cache/dedup.json。

    背景：test_a4_frontmatter_tokens 漏挂 tmp_dedup，把内容哈希条目写进真实索引。
    autouse 重定向让「测试污染真实去重状态」结构上不可能，而不是靠每个测试记得挂 fixture。
    """
    from articles import dedup
    monkeypatch.setattr(dedup, "_INDEX_FILE", str(tmp_path / "dedup.json"))
    monkeypatch.setattr(dedup, "_CACHE_DIR", str(tmp_path))
