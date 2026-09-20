"""系列排除名单（series_exclude）回归测试（2026-09-19）：

保护需求：
1. series_title_excluded：配置名与 B站季名互为子串即命中（B站季名可能带
   「合集·」等装饰前缀），空白项/空名单不误命中。
2. load_series_exclude：从 subscriptions.json 读 账号名 → series_exclude 列表，
   未配置的账号不出现在结果里。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from shared.routing import series_title_excluded


def test_exact_match():
    assert series_title_excluded("小猪仔与生活", ["小猪仔与生活"])


def test_bidirectional_substring():
    # B站季名带装饰前缀，配置名是季名的子串
    assert series_title_excluded("合集·动画片：小猪仔学投资", ["动画片：小猪仔学投资"])
    # 反向：配置名比季名长（季名是其子串）也算命中
    assert series_title_excluded("小猪仔与生活", ["小猪仔与生活（含番外）"])


def test_no_false_hit():
    assert not series_title_excluded("小猪仔拆公司", ["小猪仔与生活"])
    assert not series_title_excluded("", ["小猪仔与生活"])
    assert not series_title_excluded("小猪仔与生活", [])
    assert not series_title_excluded("小猪仔与生活", ["", "  "])


def test_load_series_exclude_has_pig():
    from shared.routing import load_series_exclude
    exc = load_series_exclude()
    assert exc.get("价投小猪仔") == ["小猪仔与生活", "动画片：小猪仔学投资"]
    # 未配置的账号不得出现
    assert all("series_exclude" not in v for v in exc.values())
