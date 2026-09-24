"""覆盖语义单测（DECISION-20260915 阶段3）：修订已有笔记不得产生 `-N` 副本。

背景（真实事故）：引擎的文件名冲突处理是无条件「禁止覆盖」→ 每次 force 修订都留下
`-1.md`，且登记表 filename 指向副本；事后手工合并删除副本后，登记表就指向已删文件
（2026-09-15 本批 15 条中招；全库历史累积 50 个 `*-N.md`）。

现在 `_save_summary_from_file.py --force` 会同时传 `force`（绕过 dedup）与
`overwrite`（覆盖同名文件）。本文件守住后者。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import pytest  # noqa: E402

from articles import dedup, main as articles_main  # noqa: E402


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir()
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(v))
    monkeypatch.setattr(dedup, "_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(dedup, "_INDEX_FILE", str(tmp_path / "cache" / "dedup.json"))
    return v


def _save(url, body, folder, overwrite=False):
    return articles_main.save_summarized_article(
        body, original_url=url, author="作者A", tags=["测试类"],
        original_title="测试标题", folder=folder, obsidian=True,
        note_type="structured", overwrite=overwrite)


class TestOverwriteSemantics:
    FOLDER = "测试目录"

    def test_default_keeps_no_overwrite_produces_copy(self, vault):
        """默认语义保持历史行为：同名 → 改名 -1（不覆盖）。"""
        _save("https://g.com/o1", "第一版内容" * 10, self.FOLDER)
        _, name2 = _save("https://g.com/o1", "第二版内容" * 10, self.FOLDER)
        base = vault / self.FOLDER
        files = sorted(p.name for p in base.glob("*.md"))
        assert len(files) == 2, files
        assert any(n.endswith("-1.md") for n in files)

    def test_overwrite_replaces_in_place(self, vault):
        """overwrite=True：同名文件被替换，不产生 `-1`。"""
        _, name1 = _save("https://g.com/o2", "第一版内容" * 10, self.FOLDER)
        _, name2 = _save("https://g.com/o2", "第二版内容" * 10, self.FOLDER, overwrite=True)
        assert name1 == name2, f"覆盖模式下文件名应保持不变：{name1} vs {name2}"
        base = vault / self.FOLDER
        files = sorted(p.name for p in base.glob("*.md"))
        assert len(files) == 1, files
        assert "第二版内容" in (base / files[0]).read_text(encoding="utf-8")

    def test_overwrite_twice_still_single_file(self, vault):
        """反复修订也不堆积副本（历史坑：每修一次多一个 -N）。"""
        for i in range(4):
            _save("https://g.com/o3", f"第{i}版内容" * 10, self.FOLDER, overwrite=True)
        base = vault / self.FOLDER
        files = list(base.glob("*.md"))
        assert len(files) == 1, [p.name for p in files]
        assert "第3版内容" in files[0].read_text(encoding="utf-8")

    def test_copy_lands_under_expected_filename(self, vault):
        """覆盖后的文件名在 vault 中真实存在（守住「登记表不得指向已删文件」）。"""
        _, name = _save("https://g.com/o4", "内容" * 20, self.FOLDER, overwrite=True)
        assert (vault / name.replace("/", "/")).exists()


class TestForceRevisionNoCopy:
    """force 修订不得留下 `-N` 副本（2026-09-24 补齐）。

    事故来源：队列 / 子 Agent 路径只传 `force=True`（绕 dedup），而 force 历史上
    **不接管文件名冲突策略** → 新版落成 `-1`、旧版留在库里，登记表指向副本。
    `save_summary_only` 现已把「force + 有旧登记」升级为就地覆盖。本类钉死该行为。
    """

    def _call(self, url, text, force=False):
        return articles_main.save_summary_only({
            "summarized_content": text,
            "original_url": url,
            "author": "作者A",
            "original_title": "测试标题",
            "folder": "测试目录",
            "note_type": "structured",
            "obsidian": True,
            "force": force,
        })

    def _body(self, tag):
        # 无 H1（门禁硬拦）、无 URL 字面量；含 structured 必备模块且正文 >300 字
        return ("## 分层速览\n\n" + f"{tag}速览内容" * 40 + "\n\n"
                "## 正反例对照\n\n" + f"{tag}对照内容" * 40 + "\n\n"
                "## 我的想法\n\n" + f"{tag}想法内容" * 40 + "\n")

    def test_force_revision_replaces_in_place(self, vault):
        url = "https://g.com/force1"
        r1 = self._call(url, self._body("第一版"))
        assert r1.get("success"), r1
        r2 = self._call(url, self._body("第二版"), force=True)
        assert r2.get("success"), r2

        files = list(vault.rglob("*.md"))
        assert len(files) == 1, [p.name for p in files]  # 不留 -1 副本
        assert "第二版" in files[0].read_text(encoding="utf-8")

        rec = dedup.is_summarized(url=url)
        assert rec, "修订后登记表仍须有记录"
        assert (vault / rec["filename"]).exists(), "登记表不得指向已删文件"

    def test_force_revision_repeated_never_accumulates(self, vault):
        url = "https://g.com/force2"
        for i in range(3):
            r = self._call(url, self._body(f"第{i}版"), force=(i > 0))
            assert r.get("success"), r
        files = list(vault.rglob("*.md"))
        assert len(files) == 1, [p.name for p in files]
        assert "第2版" in files[0].read_text(encoding="utf-8")
