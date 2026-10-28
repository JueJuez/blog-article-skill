"""原文素材的结构性保护：模型只定位，正文由同版原文产生。"""
import copy
import json

import pytest

from prompts import distill as DT
from prompts import synthesis as SY
from shared import distill_dispatch as DD
from shared import distill_pack as DP
from conftest import v6ize


SOURCE = ("很多人大学本科毕业\n从此一辈子不再学习了\n他只完成了4年的大学教育\n\n"
          "全职年薪80万到100万\n兼职顾问少要一点，但干的活值80万到100万\n"
          "通常需要当地合伙人或员工\n定期（如每月一次）联系客户\n完成单笔2万美金订单")


def parsed(spans=None, **extra):
    item = {"id": "i1", "type": "argument", "label": "学历与持续学习",
            "source_spans": [[1, 3]] if spans is None else spans,
            "notes": "保留时间归属"}
    item.update(extra)
    return v6ize({"mainline": {"question": "作者如何论证", "progression": [], "conclusions": []},
                  "items": [item], "relations": [], "deferred": []})


def pack(data=None):
    return DP.build_pack(title="T", url="eval://excerpts/T", source_text=SOURCE,
                         distill_prompt_sha256=DT.prompt_hash("T", SOURCE),
                         parsed=parsed() if data is None else data)


def test_model_rewrite_cannot_replace_original():
    # 重现旧错误；即使模型多交一份错写，正文仍由源定位生成。
    p = pack(parsed(content="本科毕业后4年不再学习", readings=[{"value": "4年后"}]))
    assert p["items"][0]["content"] == "\n".join(SOURCE.split("\n")[:3])
    assert not p["items"][0].get("readings")
    assert DP.validate_pack(p, SOURCE)["ok"]


def test_multiple_spans_preserve_separate_context():
    p = pack(parsed([[1, 3], [5, 6]]))
    it = p["items"][0]
    assert it["source_spans"] == [[1, 3], [5, 6]]
    assert "非连续原文" in it["content"]
    assert "兼职顾问少要一点" in it["content"]
    assert DP.validate_pack(p, SOURCE)["ok"]


@pytest.mark.parametrize("spans", [[], [[0, 2]], [[1, 100]], [[3, 1]],
                                      [[1, 3], [2, 5]], [[5, 6], [1, 3]], [[True, 2]]])
def test_invalid_or_overlapping_spans_refused(spans):
    # 定位有误不能靠 Python 负索引、截断或自动排序掩盖。
    result = DP.validate_pack(pack(parsed(spans)), SOURCE)
    assert not result["ok"], result


def test_modified_material_body_refused():
    p = pack()
    p["items"][0]["content"] = "本科毕业后4年不再学习"
    result = DP.validate_pack(p, SOURCE)
    assert not result["ok"] and any("原文" in x for x in result["issues"])


def test_material_body_must_match_its_location():
    # 内容在源的其他地方出现也不够，必须命中所声明的位置。
    p = pack()
    p["items"][0]["content"] = SOURCE.split("\n")[6]
    assert not DP.validate_pack(p, SOURCE)["ok"]


def test_all_serializations_preserve_selection_and_annotations():
    p = pack(parsed([[1, 3], [5, 6]]))
    assert DP.lines_roundtrip_issues(p) == []
    got = DP.parse_pack_lines(DP.serialize_pack(p, "lines"))["items"][0]
    for field in ("content", "source_spans", "label", "notes"):
        assert got[field] == p["items"][0][field]


def test_merge_does_not_drop_equal_text_at_different_locations():
    # 相同表述出现在不同位置时不能自动当重复，背景可能不同。
    src = "同一句原文\n同一句原文"
    parts = [{"chunk": {"index": n, "total": 2, "start_line": n, "end_line": n},
              "parsed": parsed([[n, n]])} for n in (1, 2)]
    p = DP.merge_chunk_packs(title="T", url="eval://excerpts/merge", source_text=src,
                             distill_prompt_sha256="h", parts=parts)
    assert len(p["items"]) == 2
    assert DP.validate_pack(p, src)["ok"]


def test_ingest_checks_every_span_against_chunk_core():
    chunk = {"core_start_line": 5, "core_end_line": 9}
    # 不允许伪造包围区间后，偷偷引用核心区间之外的原文。
    assert DD.chunk_scope_issues(chunk, parsed([[1, 3], [5, 6]], source_lines=[5, 6]))


def test_contract_and_handoff_do_not_force_rewriting():
    assert DT.CONTRACT_VERSION >= 5
    rules = DT.rules_text()
    assert "source_spans" in rules and "由程序" in rules
    assert "不输出素材正文" in rules
    text = SY.render_section(pack())
    assert "原文" in text and "按需" in text
    assert "只" in text and "素材" in text


def test_three_known_failures_are_kept_in_original():
    # 完整选中这些位置后，数字两口径、时间与限定词无需模型再生成。
    it = pack(parsed([[1, 9]]))["items"][0]
    for expected in ("4年的大学教育", "兼职顾问少要一点", "通常", "或员工", "如每月一次"):
        assert expected in it["content"]
    assert "最大" not in it["content"]


def test_uncovered_ranges_complement_selection():
    p = pack(parsed([[1, 3], [5, 6]]))
    assert DP.uncovered_ranges(p, SOURCE) == [[4, 4], [7, 9]]
    excerpt = DP.uncovered_excerpt(p, SOURCE)
    assert "全职年薪80万到100万" not in excerpt          # 已选中，不重复
    for kept in ("通常需要当地合伙人或员工", "定期（如每月一次）联系客户", "完成单笔2万美金订单"):
        assert kept in excerpt


def test_full_coverage_has_no_appendix():
    p = pack(parsed([[1, 9]]))
    assert DP.uncovered_ranges(p, SOURCE) == []
    assert DP.uncovered_excerpt(p, SOURCE) == ""


def test_pack_only_prompt_is_complete_without_source_read():
    p = pack(parsed([[1, 3], [5, 6]]))
    text = DD.assemble_pack_only_prompt("BASE", p, SOURCE)
    assert text.startswith("BASE")
    assert "不需要另行读取源文件" in text
    assert DD.UNCOVERED_BEGIN in text and DD.UNCOVERED_END in text
    appendix = text.split(DD.UNCOVERED_BEGIN)[1]
    for kept in ("通常需要当地合伙人或员工", "定期（如每月一次）联系客户", "完成单笔2万美金订单"):
        assert kept in appendix
    assert "全职年薪80万到100万" not in appendix
    base, block = DD.split_write_prompt(text)
    assert base == "BASE" and block and DD.UNCOVERED_BEGIN in block


def test_pack_only_prompt_does_not_duplicate_source_lines():
    # 已选区间进素材、未选区间进附录，二者恰好拼成全文且逐行不重复——
    # 「不再追加全文」是机械可查的：任何源行都不该在写作输入里出现两次。
    p = pack(parsed([[1, 3], [5, 6]]))
    text = DD.assemble_pack_only_prompt("BASE", p, SOURCE)
    for ln in (l for l in SOURCE.split("\n") if l.strip()):
        assert text.count(ln) == 1, f"源行重复投喂：{ln!r}"


def test_pack_only_block_keeps_version_binding():
    p = pack()
    block = DD.render_pack_only_block(p, SOURCE)
    assert DD.block_is_current(block, SOURCE, title="T") == (True, "ok")


# ------------------------------------------------------- prepare_entry 接线（F2）

def _long_plain_source(chars: int = 12000) -> str:
    body = "这一段是低密度论述文字用来把总字数推过路由阈值本身不含数字也不含表格。\n"
    out, n = [], 0
    while n < chars:
        out.append(body)
        n += len("".join(body.split()))
    return "\n".join(out)


def _long_parsed(src: str) -> dict:
    n = len(src.split("\n"))
    return v6ize({"mainline": {"question": "源在论证什么", "progression": [], "conclusions": []},
                  "items": [{"id": "i1", "type": "argument", "label": "主体论证",
                             "source_spans": [[1, n - 5]], "notes": "尾段未选，应有附录"}],
                  "relations": [], "deferred": []})


class TestPrepareEntryPackOnly:
    def _setup(self, tmp_path, url="eval://excerpts/T", with_pack=True):
        src = _long_plain_source()
        sf = tmp_path / "src.md"
        sf.write_text(src, encoding="utf-8")
        if with_pack:
            p = DP.build_pack(title="T", url=url, source_text=src,
                              distill_prompt_sha256=DT.prompt_hash("T", src),
                              parsed=_long_parsed(src))
            DP.save_pack(p, str(tmp_path))
        entry = {"url": url, "title": "T", "note_type": "general",
                 "prompt": "BASE PROMPT\n", "raw_file": str(sf)}
        return entry, src, (p if with_pack else None)

    def test_prepare_assembles_pack_only_prompt(self, tmp_path):
        entry, src, _ = self._setup(tmp_path)
        rep = DD.prepare_entry(entry, store_dir=str(tmp_path), source_text=src)
        assert rep["status"] == "prepared", rep.get("status")
        base, block = DD.split_write_prompt(entry["prompt"])
        assert base == "BASE PROMPT"
        assert DD.UNCOVERED_BEGIN in block and DD.UNCOVERED_END in block
        assert "不需要另行读取源文件" in block
        assert entry["generation_context"]["distill_input_mode"] == "pack_only"
        assert DD.block_is_current(block, src, title="T") == (True, "ok")

    def test_prepare_is_idempotent(self, tmp_path):
        entry, src, _ = self._setup(tmp_path)
        DD.prepare_entry(entry, store_dir=str(tmp_path), source_text=src)
        once = entry["prompt"]
        rep = DD.prepare_entry(entry, store_dir=str(tmp_path), source_text=src)
        assert rep["status"] == "already_prepared"
        assert entry["prompt"] == once
        assert entry["prompt"].count(DD.UNCOVERED_BEGIN) == 1

    def test_old_input_mode_block_is_reassembled(self, tmp_path):
        """源与契约同版的旧输入模式块（无未选区间附录）不能被幂等判定误认为新模式。"""
        entry, src, p = self._setup(tmp_path)
        entry["prompt"] = DD.assemble_write_prompt("BASE PROMPT\n", p)
        assert DD.UNCOVERED_BEGIN not in entry["prompt"]
        rep = DD.prepare_entry(entry, store_dir=str(tmp_path), source_text=src)
        assert rep["status"] == "prepared"
        _, block = DD.split_write_prompt(entry["prompt"])
        assert DD.UNCOVERED_BEGIN in block
        assert entry["prompt"].count(DD.MATERIAL_PACK_BEGIN) == 1


# ------------------------------------------------------- 批次交接（F3）

class TestBatchHandoff:
    def _write_queue(self, tmp_path, entries):
        qp = tmp_path / "queue.json"
        qp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(qp)

    def test_batch_scopes_to_given_urls_only(self, tmp_path):
        a, src_a, _ = TestPrepareEntryPackOnly()._setup(tmp_path, url="eval://excerpts/A")
        b, src_b, _ = TestPrepareEntryPackOnly()._setup(tmp_path, url="eval://excerpts/B")
        qp = self._write_queue(tmp_path, [a, b])
        res = DD.prepare_batch_entries(qp, [a["url"]], store_dir=str(tmp_path))
        assert res["prepared"] == [a["url"]]
        entries = json.load(open(qp, encoding="utf-8"))
        assert DD.UNCOVERED_BEGIN in entries[0]["prompt"]
        assert entries[1]["prompt"] == b["prompt"]          # 批次外条目一字不动

    def test_batch_failure_does_not_advance_or_block_others(self, tmp_path):
        a, _, _ = TestPrepareEntryPackOnly()._setup(tmp_path, url="eval://excerpts/A",
                                                    with_pack=False)   # 无有效 pack
        b, _, _ = TestPrepareEntryPackOnly()._setup(tmp_path, url="eval://excerpts/B")
        qp = self._write_queue(tmp_path, [a, b])
        res = DD.prepare_batch_entries(qp, [a["url"], b["url"]], store_dir=str(tmp_path))
        assert res["fallback"] == [{"url": a["url"], "reason": "no_pack",
                                    "stripped": False}]
        assert res["prepared"] == [b["url"]]
        entries = json.load(open(qp, encoding="utf-8"))
        assert entries[0]["prompt"] == a["prompt"]          # 失败篇不推进
        assert DD.UNCOVERED_BEGIN in entries[1]["prompt"]

    def test_batch_idempotent_and_missing_url_reported(self, tmp_path):
        a, _, _ = TestPrepareEntryPackOnly()._setup(tmp_path)
        qp = self._write_queue(tmp_path, [a])
        DD.prepare_batch_entries(qp, [a["url"]], store_dir=str(tmp_path))
        res = DD.prepare_batch_entries(qp, [a["url"], "eval://excerpts/NOPE"],
                                       store_dir=str(tmp_path))
        assert res["already"] == [a["url"]]
        assert res["not_in_queue"] == ["eval://excerpts/NOPE"]
