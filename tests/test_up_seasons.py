"""回归测试：up_seasons 合集（ugc_season）解析纯函数与前缀清洗。

网络函数不测（外部 API）；parse_* 与 strip_season_prefix 是机械层，
喂 fixture 即可钉死行为（2026-10-06 合集识别收编）。
"""
import sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts.up_seasons import (parse_seasons_payload, parse_archives_payload,
                                strip_season_prefix)


def test_strip_season_prefix():
    assert strip_season_prefix("合集·说话之道") == "说话之道"
    assert strip_season_prefix("说话之道") == "说话之道"
    assert strip_season_prefix("") == ""
    assert strip_season_prefix("合集·合集·X") == "合集·X"  # 只去一次


def test_parse_seasons_payload():
    data = {"items_lists": {
        "page": {"total": 2},
        "seasons_list": [
            {"meta": {"season_id": 7574314, "name": "合集·说话之道", "total": 8}},
            {"meta": {"season_id": 6103055, "name": "合集·职场", "total": 23}},
        ],
        "series_list": [
            {"meta": {"series_id": 99, "name": "合集·旧系列", "total": 3}},
        ],
    }}
    got = parse_seasons_payload(data)
    assert got == [(7574314, "合集·说话之道", 8), (6103055, "合集·职场", 23), (99, "合集·旧系列", 3)]


def test_parse_seasons_payload_empty():
    assert parse_seasons_payload({}) == []
    assert parse_seasons_payload({"items_lists": {}}) == []


def test_parse_archives_payload():
    data = {"archives": [{"bvid": "BV1a"}, {"bvid": "BV2"}, {}], "page": {"total": 3}}
    assert parse_archives_payload(data) == ["BV1a", "BV2"]
    assert parse_archives_payload({}) == []
