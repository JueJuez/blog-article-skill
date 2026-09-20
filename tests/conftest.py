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
    2026-09-07（P0-2）：登记表迁往 notes/_meta/sync_ledger.json 后，旧档
    _LEGACY_INDEX_FILE 也必须一并隔离——否则测试内首次读索引会把真实旧档
    搬进 tmp 并删除真档（数据丢失）。
    2026-09-08（PLAN-20260908 1.1）：真源升格为 notes/_meta/summary_registry.json，
    搬家链三代 .cache/dedup.json → sync_ledger.json → summary_registry.json，
    全部重定向；_LEGACY_CACHE_FILE 用 raising=False 兼容 RED 阶段常量未定义。
    """
    from articles import dedup
    monkeypatch.setattr(dedup, "_INDEX_FILE", str(tmp_path / "summary_registry.json"))
    monkeypatch.setattr(dedup, "_LEGACY_INDEX_FILE",
                        str(tmp_path / "notes_meta" / "sync_ledger.json"))
    monkeypatch.setattr(dedup, "_LEGACY_CACHE_FILE",
                        str(tmp_path / "cache" / "dedup.json"), raising=False)
    monkeypatch.setattr(dedup, "_CACHE_DIR", str(tmp_path))


@pytest.fixture(autouse=True)
def _isolate_gate_blockers(monkeypatch, tmp_path):
    """守卫（2026-09-07）：任何测试不得写真实 monitors/gate_blockers.*.jsonl。

    背景：门禁接线测试（test_note_mechanical_gate / test_series_note_gate）走真实
    log_gate_block，把 7 条假拦截记录（g.com / example.com）写进了真实台账。
    autouse 重定向让「测试污染真实拦截台账」结构上不可能，而不是靠每个测试记得挂 fixture。
    """
    from shared import gate_blockers
    monkeypatch.setattr(gate_blockers, "GATE_BLOCKERS_BASE",
                        str(tmp_path / "gate_blockers.jsonl"))


@pytest.fixture(autouse=True)
def _strip_oversized_env():
    """守卫（2026-09-20）：清掉 WorkBuddy 桌面端注入的超长环境变量。

    背景：桌面端按 `cli/product.json` 的 `productConfigEnv` 向子进程注入
    `ACC_PRODUCT_CONFIG` / `_V2` / `_V3`，值为**整个 product.json**（约 322 KB / 330412 字符，
    源 `D:\\AI\\WorkBuddy\\resources\\app.asar.unpacked\\cli\\product.json`），
    远超 Windows 单变量 32767 上限。后果：任何 `os.environ[k] = v` 都会抛
    `ValueError: the environment variable is longer than 32767 characters`，
    典型受害者是 `mock.patch.dict(os.environ, ...)` 在**还原阶段**炸掉
    （test_patch_trio 假失败，与业务代码无关）。同一原因也让这些变量**时有时无**
    （沙箱 CLI 启动子进程时会主动 delete 它们）。

    ⚠️ 为什么在测试侧清而不是去关掉注入：应用侧关**不掉**——`app.asar` 里硬编码了
    `DEFAULT_PRODUCT_CONFIG_ENV_KEYS = ["..._V3","..._V2","ACC_PRODUCT_CONFIG"]`，
    删 `cli/product.json` 的声明会被这份默认值兜底；且这些变量只服务 WorkBuddy CLI 自身
    （它另有 `ACC_PRODUCT_CONFIG_PATH` 走 spill 文件，是官方推荐的 path 模式），
    与本项目无关。故在测试进程内清掉即可 —— 与 `_isolate_dedup` 同一思路：
    让「超长变量污染测试」结构上不可能，而不是靠每次手动前置环境变量或靠每个测试记得处理。
    """
    import os
    for _k in ("ACC_PRODUCT_CONFIG", "ACC_PRODUCT_CONFIG_V2", "ACC_PRODUCT_CONFIG_V3"):
        os.environ.pop(_k, None)
