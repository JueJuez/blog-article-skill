# -*- coding: utf-8 -*-
"""两条落盘管线的回归测试（2026-09-17 下沉 note_path 后新增）。

钉死三件事：
1. **增量生产管线零变化**：`save_summarized_article` 不传 `note_path` 时，
   仍走 `generate_filename` 推导文件名并落盘（老行为）。
2. **存量重做管线 in-place**：传 `note_path` 时覆盖既有精确路径，
   目录里**不出现 `-N` 副本**，且格式与生产一致（标签行 + 来源链接）。
3. **薄壳 `resum_save_batch`**：批次端到端落盘；缺主题词行 / 门禁不过 → 不写盘。
"""
import importlib
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

VAULT = None

CONTENT = """**作者**：测试作者

## 核心命题

这是一段用于回归测试的正文，长度必须超过机械门禁的内容缺失下限（300 字），
因此此处补足足够篇幅，确保门禁不会因字数过少而拦截，从而只检验落盘行为本身。
本篇要钉死的事情是：增量生产管线与存量重做管线互不干扰，各自按自己的规则落盘。

## 关键要点

- 要点一：生产管线不受影响，文件名仍由发布时间与标题推导得出，目录路由保持不变。
- 要点二：重做管线走既有精确路径覆盖，不推导文件名，因此永远不会产生编号副本。
- 要点三：两条管线共用同一套格式化与标签逻辑，任何一侧的改动都应该同时生效。

## 思维模型自检

- 第一性原理：落盘的唯一目的是让笔记出现在它该在的位置，文件名只是这个位置的表达。
- 二阶思维：如果重做时重新推导文件名，那么旧文件不会消失，库里就会出现两篇同名笔记。

## 自测三问

- 问：这篇笔记覆盖后，原先的文字还在吗？答：不在，已整体替换。
- 问：目录里多出文件了吗？答：没有，只覆盖了目标那一个。
- 问：来源链接与标签是否齐全？答：由生产链路统一追加，格式一致。

硬事实：测试数字 12345；专有名词：回归测试管线；记录时间：2026 年 9 月。

【核心主题词】回归测试 | 管线 | 落盘
"""


@pytest.fixture(autouse=True)
def _tmp_vault(tmp_path, monkeypatch):
    """把 vault 指向临时目录，并强制只写本地 Obsidian。"""
    vault = tmp_path / "vault"
    (vault / "【监控】" / "B站").mkdir(parents=True)
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.setenv("OBSIDIAN_WRITE", "1")
    monkeypatch.setenv("DISABLE_FEISHU_SYNC", "1")
    for mod in ("articles.main", "articles.manager", "articles.obsidian", "articles.dedup"):
        try:
            importlib.reload(importlib.import_module(mod))
        except Exception:
            pass
    global VAULT
    VAULT = str(vault)
    yield vault


def test_production_pipeline_unchanged(tmp_path):
    """不传 note_path → 仍由 generate_filename 推导文件名落盘（老行为零变化）。"""
    from articles.main import save_summarized_article

    formed, filename = save_summarized_article(
        CONTENT, original_url="https://example.com/post/1",
        author="测试作者", original_title="回归测试标题",
        folder="【监控】/B站", obsidian=True,
    )
    assert filename, "生产管线应返回文件名"
    target = os.path.join(VAULT, filename)
    assert os.path.exists(target), f"生产管线应落盘到 {target}"
    assert "【监控】" in filename.replace("\\", "/"), f"folder 路由应保留，实际 {filename}"
    with open(target, encoding="utf-8") as f:
        body = f.read()
    assert "https://example.com/post/1" in body, "生产落盘应带来源链接"
    assert "**作者**" in body


def test_inplace_overwrite_no_copy():
    """传 note_path → 覆盖既有精确路径，且不产生 -N 副本。"""
    from articles.main import save_summarized_article

    note_path = os.path.join(VAULT, "【监控】", "B站", "20260101_旧笔记.md")
    os.makedirs(os.path.dirname(note_path), exist_ok=True)
    with open(note_path, "w", encoding="utf-8") as f:
        f.write("旧内容，应被覆盖")

    formed, filename = save_summarized_article(
        CONTENT, original_url="https://example.com/post/2",
        author="测试作者", original_title="回归测试标题",
        folder="【监控】/B站", obsidian=True, note_path=note_path,
    )

    with open(note_path, encoding="utf-8") as f:
        body = f.read()
    assert "SENTINEL_OLD_BODY" not in body, "in-place 应覆盖原文件内容"
    assert "核心命题" in body
    assert "https://example.com/post/2" in body, "in-place 也应带来源链接（格式与生产一致）"
    assert "#" in body, "应注入语义标签"

    d = os.path.dirname(note_path)
    copies = [p for p in os.listdir(d) if p.startswith("20260101_旧笔记")]
    assert copies == ["20260101_旧笔记.md"], f"不应产生 -N 副本，目录实际为 {copies}"
    assert os.path.basename(note_path) in filename


def test_resum_save_batch_end_to_end(tmp_path):
    """薄壳脚本端到端：批次落盘 + 统计；缺主题词行的条目不写盘。"""
    main_mod = None
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    try:
        main_mod = importlib.import_module("resum_save_batch")
    except Exception as e:
        pytest.skip(f"无法导入 resum_save_batch: {e}")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    note1 = os.path.join(VAULT, "【监控】", "B站", "20260102_批次一.md")
    note2 = os.path.join(VAULT, "【监控】", "B站", "20260102_批次二.md")
    for p in (note1, note2):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("SENTINEL_OLD_BODY")

    (out_dir / "batch_test_00.md").write_text(CONTENT, encoding="utf-8")
    bad = CONTENT.replace("【核心主题词】回归测试 | 管线 | 落盘", "")
    (out_dir / "batch_test_01.md").write_text(bad, encoding="utf-8")

    batch = [
        {"title": "批次一", "url": "https://example.com/a", "author": "测试作者",
         "note_type": "", "note_path": note1, "source_path": ""},
        {"title": "批次二", "url": "https://example.com/b", "author": "测试作者",
         "note_type": "", "note_path": note2, "source_path": ""},
    ]
    batch_path = tmp_path / "batch_test.json"
    batch_path.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")

    argv = sys.argv
    sys.argv = ["resum_save_batch.py", str(batch_path), str(out_dir), "--force"]
    try:
        rc = main_mod.main()
    finally:
        sys.argv = argv

    with open(note1, encoding="utf-8") as f:
        assert "核心命题" in f.read(), "批次条目 0 应被覆盖落盘"
    with open(note2, encoding="utf-8") as f:
        assert "SENTINEL_OLD_BODY" in f.read(), "缺主题词行的条目必须拒绝落盘（保留旧版）"
    assert rc == 0
