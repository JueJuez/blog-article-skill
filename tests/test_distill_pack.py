# -*- coding: utf-8 -*-
"""S7 独立提炼契约回归测试（PLAN-20261026-low-cost-quality §9.3–§9.6，2026-10-27）。

契约要点（§9.3.2 / §9.4 S7 出口）：
1. 三类产物：主线地图（问题/推进/结论）、信息条目（观点/案例/步骤/数字/资源/条件/反例
   + 来源位置）、关系说明（支持/依赖/限制/矛盾）。
2. 同版绑定：source_sha256 与 provenance 同口径；失配不能当有效交接。
3. 机械校验是**报告项**：字段齐全 ≠ 语义完整；行号越界、悬空引用、原话未命中都进 issues。
4. 失败可续跑：同版重写幂等；pack 是 JSON sidecar，不进生成正文。
5. 试验期隔离：store_dir 可指定评估目录，不污染生产。
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from shared import distill_pack as DP
from prompts import distill as DT
from prompts.provenance import text_hash


_SOURCE = """> 原始文章内容（自动暂存）

---

# 一、为什么做代运营

作者说：「代运营的本质是把别人的坑变成自己的经验。」
（第 4 行附近，原话）

2019 年他接了第一个客户，报价 8000 元/月。
条件是客户必须自备货源，否则不接——这是他后来反复强调的前提。

# 二、踩过的坑

第一个月他没有签竞业条款，客户直接绕过他找上游，损失约 3 万。
""" + "正文。\n" * 5

_SEG_LINES = None


def _pack(source=_SOURCE, **kw):
    """构造一个最小合法 pack。"""
    base = {
        "schema_version": DP.SCHEMA_VERSION,
        "contract_version": DT.CONTRACT_VERSION,
        "kind": "distill_pack",
        "recorded_at": "2026-10-27T13:00:00",
        "url": "https://example.com/a",
        "url_key": DP.url_key_of("https://example.com/a", source),
        "title": "代运营复盘",
        "source_sha256": text_hash(source),
        "distill_prompt_sha256": text_hash(DT.render_distill_prompt("代运营复盘", source)),
        "material_key": "",
        "mainline": {
            "question": "代运营怎么做才不亏",
            "progression": ["接单条件", "踩坑", "补救"],
            "conclusions": ["必须签竞业条款"],
        },
        "items": [
            {"id": "i1", "type": "viewpoint", "stance": "quote",
             "content": "代运营的本质是把别人的坑变成自己的经验。",
             "source_lines": [7, 7]},
            {"id": "i2", "type": "number", "stance": "paraphrase",
             "content": "首单报价 8000 元/月",
             "source_lines": [10, 10]},
            {"id": "i3", "type": "condition", "stance": "paraphrase",
             "content": "客户必须自备货源，否则不接",
             "source_lines": [11, 11]},
        ],
        "relations": [
            {"from": "i3", "to": "i2", "kind": "limits", "note": "报价前提"},
            {"from": "i1", "to": "i3", "kind": "supports", "note": "经验支撑条件"},
        ],
        "deferred": [],
        "coverage": {
            "source_lines_total": len(source.split("\n")),
            "processed_ranges": [[1, len(source.split("\n"))]],
            "complete": True,
            "chunk_count": 1,
            "item_count": 3,
            "relation_count": 2,
        },
        "notes": "",
    }
    base.update(kw)
    base["items"] = DP.build_pack(title=base["title"], url=base["url"], source_text=source,
                                  distill_prompt_sha256=base["distill_prompt_sha256"],
                                  parsed=base)["items"]
    return base


# ---------------------------------------------------------------- schema 与绑定

def test_schema_minimal_fields():
    v = DP.validate_pack(_pack(), _SOURCE)
    assert v["ok"] is True, v["issues"]


def test_source_hash_mismatch_rejected():
    """换源/改稿后旧提炼不能当有效交接（§9.6）。"""
    v = DP.validate_pack(_pack(), _SOURCE + "\n追加")
    assert v["ok"] is False
    assert any("source_sha256" in i for i in v["issues"])


def test_prompt_hash_recorded():
    """提炼 prompt 版本随 pack 记录（改契约文案 = 换版本）。"""
    p = DT.render_distill_prompt("t", _SOURCE)
    p2 = DT.render_distill_prompt("t", _SOURCE)
    assert text_hash(p) == text_hash(p2)
    assert "主线" in p and "items" in p and "relations" in p


# ---------------------------------------------------------------- 机械校验（报告项）

def test_source_lines_out_of_range():
    bad = _pack()
    bad["items"][0]["source_lines"] = [999, 1001]
    v = DP.validate_pack(bad, _SOURCE)
    assert v["ok"] is False
    assert any("越界" in i for i in v["issues"])


def test_source_lines_empty_target():
    """行号指向空行 → sanity issue（定位可能漂移）。"""
    bad = _pack()
    bad["items"][1]["source_lines"] = [4, 4]  # 第 4 行为空
    v = DP.validate_pack(bad, _SOURCE)
    assert any("空行" in i or "定位" in i for i in v["issues"])


def test_dangling_relation_reference():
    bad = _pack()
    bad["relations"].append({"from": "i9", "to": "i1", "kind": "supports", "note": ""})
    v = DP.validate_pack(bad, _SOURCE)
    assert v["ok"] is False
    assert any("悬空" in i or "i9" in i for i in v["issues"])


def test_bad_enum_rejected():
    bad = _pack()
    bad["items"][0]["type"] = "magic"
    v = DP.validate_pack(bad, _SOURCE)
    assert any("type" in i for i in v["issues"])
    bad2 = _pack()
    bad2["relations"][0]["kind"] = "causes_magic"
    v2 = DP.validate_pack(bad2, _SOURCE)
    assert any("kind" in i for i in v2["issues"])


def test_quote_must_hit_source():
    """stance=quote 的内容须能在源里命中（去空白口径，同 extract_checklist）。"""
    bad = _pack()
    bad["items"][0]["content"] = "这句原话根本不在源里出现。"
    v = DP.validate_pack(bad, _SOURCE)
    assert any("原话" in i for i in v["issues"])
    # 摘意不做命中要求
    ok = _pack()
    ok["items"][1]["content"] = "换个说法：首月收费八千。"
    v2 = DP.validate_pack(ok, _SOURCE)
    assert any("i2" in i and "原话" in i for i in v2["issues"])


def test_missing_mainline_reported():
    """缺主线地图 → issue（字段齐全不冒充语义完整，缺类即报）。"""
    bad = _pack()
    bad["mainline"] = {"question": "", "progression": [], "conclusions": []}
    v = DP.validate_pack(bad, _SOURCE)
    assert any("主线" in i for i in v["issues"])


def test_empty_items_reported():
    bad = _pack()
    bad["items"] = []
    v = DP.validate_pack(bad, _SOURCE)
    assert any("条目" in i for i in v["issues"])


# ---------------------------------------------------------------- 存储与续跑

def test_save_load_roundtrip_and_idempotent_rerun(tmp_path):
    """同版重写幂等（失败可续跑）；pack 为 JSON sidecar。"""
    pack = _pack()
    p1 = DP.save_pack(pack, store_dir=str(tmp_path))
    first = json.load(open(p1, encoding="utf-8"))
    p2 = DP.save_pack(pack, store_dir=str(tmp_path))  # 续跑重写
    assert p1 == p2
    assert json.load(open(p2, encoding="utf-8")) == first
    loaded = DP.load_pack("https://example.com/a", store_dir=str(tmp_path))
    assert loaded["source_sha256"] == pack["source_sha256"]
    assert DP.validate_pack(loaded, _SOURCE)["ok"] is True


def test_load_missing_returns_none(tmp_path):
    assert DP.load_pack("https://example.com/none", store_dir=str(tmp_path)) is None


def test_pack_key_matches_material_layer():
    """与 S6 材料索引/图片台账同款 url_key，可互查。"""
    from shared.image_inventory import url_key as inv_key
    assert DP.url_key_of("https://example.com/a") == inv_key("https://example.com/a")


def test_material_key_honest_when_absent():
    """S6 索引未建时 material_available 如实 False，不伪造结构可回查。"""
    pack = _pack(material_key="", material_available=False)
    v = DP.validate_pack(pack, _SOURCE)
    assert v["ok"] is True  # 缺材料索引不是错误，但必须如实
    assert pack["material_available"] is False


# ---------------------------------------------------------------- prompt 契约

def test_distill_prompt_is_self_contained():
    """自包含任务：完整源文本 + 三类产物定义 + 边界条款。"""
    p = DT.render_distill_prompt("代运营复盘", _SOURCE)
    # 契约 v2 起源文本逐行带 `NNN| ` 全局行号前缀下发（分块提炼拼回同一坐标的前提）
    assert DT.numbered_source(_SOURCE) in p
    assert "1| " in p and f"{len(_SOURCE.splitlines())}| " in p  # 首尾行号对得上
    assert "主线" in p and "items" in p and "relations" in p
    assert "通读本任务的全部源文本" in p
    assert "JSON" in p
    assert "不输出素材正文" in p and "由程序" in p
    assert "行号" in p


def test_contract_number_rules_present():
    """契约（N1 粒度改造后）：不混口径 / 不确定读数不补精度 / 先定位再写行号。

    v2 的「数字逐行成条」在 v4 退场：它按数字个数制造细条目，是长源条目爆炸的主因。
    但**不混口径**的约束没有放松，只是换成「同单元内用 readings 分列口径」。
    """
    p = DT.render_distill_prompt("t", _SOURCE)
    assert "数字逐行成条" not in p
    assert "不混口径" in p
    assert "readings" in p
    assert "不确定读数不许补精度" in p
    assert "notes" in p  # 存疑说明独立于原文正文
    assert "不换算" in p           # 不许把「4.7 一 三」拼成 4.713%
    assert "source_spans" in p and "照抄源行号" in p


def test_contract_v4_granularity():
    """契约 v4（N1）：段级论证单元、跨段 deferred、局部不替全篇下结论。"""
    p = DT.render_distill_prompt("t", _SOURCE)
    assert "带注解的内容不得标 quote" in p
    assert "完整论证单元" in p and "不输出素材正文" in p
    assert "deferred" in p
    assert DT.CONTRACT_VERSION == 5
    assert DT.example_issues() == []


def test_contract_is_explicitly_versioned():
    """契约文案必须是「改了就换版本的」：MARKER 与 CONTRACT_VERSION 同步，hash 稳定可复算。"""
    assert str(DT.CONTRACT_VERSION) in DT.DISTILL_MARKER
    assert DT.prompt_hash("t", _SOURCE) == DT.prompt_hash("t", _SOURCE)
    assert DT.prompt_hash("t", _SOURCE) != DT.prompt_hash("别的标题", _SOURCE)


def test_distill_prompt_does_not_leak_into_note_templates():
    """提炼契约与九类写作模板互不拼接（中间产物不误入正文的结构前提）。"""
    from prompts.templates import NOTE_TEMPLATES
    dp = DT.render_distill_prompt("t", _SOURCE)
    for t, v in NOTE_TEMPLATES.items():
        assert DT.DISTILL_MARKER not in v["prompt"], t
    assert DT.DISTILL_MARKER in dp


def test_load_pack_accepts_pseudo_scheme_url(tmp_path):
    """eval:// 等伪协议 URL 须能读回自己存的 pack（S8 首轮实抓的 bug）。"""
    pack = _pack(url="eval://s8/D1")
    pack["url_key"] = DP.url_key_of("eval://s8/D1", _SOURCE)  # 夹具同步 key
    p = DP.save_pack(pack, store_dir=str(tmp_path))
    loaded = DP.load_pack("eval://s8/D1", store_dir=str(tmp_path))
    assert loaded is not None
    assert loaded["source_sha256"] == pack["source_sha256"]
    assert DP.validate_pack(loaded, _SOURCE)["ok"] is True
