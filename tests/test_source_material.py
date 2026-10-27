# -*- coding: utf-8 -*-
"""S6 材料层回归测试（PLAN-20261026-low-cost-quality §9.3–§9.6，2026-10-27）。

契约要点：
1. 同版绑定：材料索引以 provenance.text_hash 同口径绑定 source_sha256，
   版本失配不能当有效交接（verify 必须拒绝）。
2. 结构索引：标题层级 / 表格块 / 代码块 / 段落范围均可按 1-based 行号回查。
3. 不伪造：只有文本时不伪造表格结构、时间位置或画面证据；
   时间戳与图片状态如实声明 present/absent 与台账 key。
4. 复用：图片状态引用 shared.image_inventory（url_key / load_for_url），不建第二套台账。
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from shared import source_material as SM
from prompts.provenance import text_hash


_ARTICLE = """> 原始文章内容（自动暂存）
> 标题：测试源

---

# 一、总述

这是第一段，
共两行。

## 1.1 细节

| 项目 | 数值 |
|---|---|
| A | 10 |
| B | 20 |

正文继续。

```python
print("hello")
```

结尾段。
"""

_SEGMENTS = [
    {"start": 0.0, "duration": 2.0, "text": "大家好"},
    {"start": 2.0, "duration": 3.5, "text": "今天讲定价"},
]


# ---------------------------------------------------------------- 同版绑定

def test_hash_matches_provenance_scope():
    """source_sha256 与 provenance.text_hash 同口径（单一真源，不另写 hash）。"""
    idx = SM.build_index(_ARTICLE, kind="article")
    assert idx["source_sha256"] == text_hash(_ARTICLE)


def test_verify_accepts_same_text(tmp_path):
    idx = SM.build_index(_ARTICLE, kind="article")
    assert SM.verify(idx, _ARTICLE) is True


def test_verify_rejects_mutated_text(tmp_path):
    """版本失配不能当有效交接（§9.6）。"""
    idx = SM.build_index(_ARTICLE, kind="article")
    assert SM.verify(idx, _ARTICLE + "\n追加行") is False


# ---------------------------------------------------------------- 结构索引

def test_headings_with_line_numbers():
    idx = SM.build_index(_ARTICLE, kind="article")
    hs = idx["headings"]
    assert [h["text"] for h in hs] == ["一、总述", "1.1 细节"]
    assert hs[0]["level"] == 1 and hs[1]["level"] == 2
    # 1-based 行号且真实指向标题行
    lines = _ARTICLE.split("\n")
    for h in hs:
        assert lines[h["line"] - 1].lstrip("# ").strip() == h["text"]


def test_tables_detected_with_bounds():
    idx = SM.build_index(_ARTICLE, kind="article")
    ts = idx["tables"]
    assert len(ts) == 1
    t = ts[0]
    assert t["rows"] == 4  # 行数=管道行数（表头+分隔行+2 数据行），如实计数
    lines = _ARTICLE.split("\n")
    block = lines[t["line_start"] - 1:t["line_end"]]
    assert all(ln.lstrip().startswith("|") for ln in block)


def test_code_block_bounds():
    idx = SM.build_index(_ARTICLE, kind="article")
    cb = idx["code_blocks"]
    assert len(cb) == 1 and cb[0]["lang"] == "python"
    lines = _ARTICLE.split("\n")
    assert lines[cb[0]["line_start"] - 1].startswith("```python")
    assert lines[cb[0]["line_end"] - 1].strip() == "```"


def test_paragraph_bounds():
    idx = SM.build_index(_ARTICLE, kind="article")
    pb = idx["paragraphs"]
    assert pb["count"] >= 3
    assert pb["bounds"], "段落行号范围必须可回查"


def test_plain_text_no_fake_structure():
    """纯文本（无 #/|/```）如实返回空结构，不编造标题或表格。"""
    plain = "第一行\n第二行\n\n第三行"
    idx = SM.build_index(plain, kind="video_asr")
    assert idx["headings"] == []
    assert idx["tables"] == []
    assert idx["code_blocks"] == []


# ---------------------------------------------------------------- 时间位置与图片

def test_timestamps_present():
    idx = SM.build_index(_ARTICLE, kind="video_asr", timestamps=_SEGMENTS)
    cov = idx["coverage"]["timestamps"]
    assert cov == "present"
    assert idx["timestamps"]["segments"] == _SEGMENTS
    assert idx["timestamps"]["span_seconds"] == pytest.approx(5.5)


def test_timestamps_absent_declared():
    """没有 segments 时如实声明 absent，不伪造时间位置。"""
    idx = SM.build_index(_ARTICLE, kind="video_asr")
    assert idx["coverage"]["timestamps"] == "absent"
    assert idx["timestamps"] is None


def test_images_links_to_inventory():
    idx = SM.build_index(_ARTICLE, kind="scys", image_key="abcd1234ef567890")
    assert idx["coverage"]["images"] == "abcd1234ef567890"


def test_images_no_inventory_declared():
    idx = SM.build_index(_ARTICLE, kind="article")
    assert idx["coverage"]["images"] == "no_inventory"


def test_text_complete_declared_by_caller():
    """读取范围由调用方如实声明；partial 必须落进索引。"""
    idx = SM.build_index(_ARTICLE[:50], kind="article", text_complete=False)
    assert idx["coverage"]["text"] == "partial"


# ---------------------------------------------------------------- 落盘与加载

def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(SM, "MATERIALS_DIR", str(tmp_path))
    idx = SM.build_index(_ARTICLE, kind="article", url="https://example.com/post/1")
    path = SM.save_index(idx)
    assert os.path.exists(path)
    loaded = SM.load_index("https://example.com/post/1")
    assert loaded == idx
    # 加载后仍可对账
    assert SM.verify(loaded, _ARTICLE) is True


def test_load_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(SM, "MATERIALS_DIR", str(tmp_path))
    assert SM.load_index("https://example.com/none") is None


def test_filename_uses_inventory_url_key():
    """与 image_inventory 同款 url_key 命名，同一 URL 可互查图片台账。"""
    from shared.image_inventory import url_key as inv_key
    assert SM.index_filename("https://example.com/post/1") == f"{inv_key('https://example.com/post/1')}.json"


# ---------------------------------------------------------------- 视频侧接入

def test_record_video_material_writes_sidecar(tmp_path, monkeypatch):
    """ASR/CC 转写成功后经 record_video_material 写材料索引（失败不阻塞主流程）。"""
    monkeypatch.setattr(SM, "MATERIALS_DIR", str(tmp_path))
    url = "https://www.bilibili.com/video/BV1testMaterial"
    SM.record_video_material(url=url, title="测试视频", text="大家好\n今天讲定价",
                             segments=_SEGMENTS)
    loaded = SM.load_index(url)
    assert loaded is not None
    assert loaded["coverage"]["timestamps"] == "present"
    assert SM.verify(loaded, "大家好\n今天讲定价") is True


def test_record_video_material_tolerates_bad_segments(tmp_path, monkeypatch):
    """segments 异常时记 absent 落盘（或抛错被吞），不得让调用方崩。"""
    monkeypatch.setattr(SM, "MATERIALS_DIR", str(tmp_path))
    SM.record_video_material(url="https://example.com/v2", title="t",
                             text="x", segments=[{"start": "bad", "text": None}])
    loaded = SM.load_index("https://example.com/v2")
    assert loaded is not None
    assert loaded["coverage"]["timestamps"] in ("absent", "present")


def test_load_index_accepts_pseudo_scheme_url(tmp_path, monkeypatch):
    """eval:// 伪协议同 hash key 读取（与 distill_pack 同步修复）。"""
    monkeypatch.setattr(SM, "MATERIALS_DIR", str(tmp_path))
    idx = SM.build_index(_ARTICLE, kind="article", url="eval://s8/D1")
    SM.save_index(idx)
    loaded = SM.load_index("eval://s8/D1")
    assert loaded is not None and loaded["url_key"] == idx["url_key"]
