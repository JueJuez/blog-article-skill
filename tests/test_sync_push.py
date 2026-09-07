"""tests/test_sync_push.py — push 对账 + reconcile 收紧 + 被动触发（PLAN-20260906 任务5）。

设计要点（DECISION-20260907 拍板 + PLAN §7 原文）：
- link 语义：link =「最后一次验证成功时写入的指针」；验证 = 对账树遍历集合比对，
  不做逐链接 HTTP 探活；每次同步结束两端 link 有效且唯一。
- push 内核 _run_push：树遍历对账（收编 audit_sync 原语）→ 清死链（feishu_link 指向
  token 不在飞书树 → 只清 feishu_link、obsidian_link 保留）→ 补传 → 复验（apply 后
  重新 collect_feishu，按树真值回写）→ 回写登记表双端 link（feishu_link=节点 token、
  obsidian_link=vault 相对路径 per P2-1）→ journal sync_run。
- reconcile 收紧（P0-1 残留）：apply 只清「两端 link 均空」的丢失记录；有任一 link 的
  保留并报告（防清掉有飞书 link 的记录导致重总结 + 飞书端重复）。--scope-substr 门禁保留。
- 被动触发（PLAN 12 行）：任一入口发现距上次 sync_run >7 天 → 自动补跑对账+补传+清死链；
  补跑的是 push 动作（gc 不在补跑清单）；push 命令自身排除被动触发；异常绝不外泄。
- audit_sync 收编（P1-5）：scripts 版 main 改薄壳转发 vl._run_push；run_audit 删除。
- dedup 补 set_links_by_key：按登记表主键直改 link（set_links 按 url 键覆盖不了
  content-key 记录）。
"""
import importlib.util
import json
import os
import sys
import time
import types

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VL_PATH = os.path.join(_ROOT, "scripts", "vault_lifecycle.py")
_AUDIT_PATH = os.path.join(_ROOT, "scripts", "audit_sync.py")

_spec = importlib.util.spec_from_file_location("vault_lifecycle", _VL_PATH)
vl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vl)

_a_spec = importlib.util.spec_from_file_location("audit_sync", _AUDIT_PATH)
audit = importlib.util.module_from_spec(_a_spec)
_a_spec.loader.exec_module(audit)


# ── 通用 helper ──────────────────────────────────────────────────────────────


def _setup_vault(tmp_path, monkeypatch):
    """隔离 vault 与 journal；返回 (vault, journal 路径)。"""
    vault = os.path.join(str(tmp_path), "vault")
    os.makedirs(vault, exist_ok=True)
    jf = os.path.join(str(tmp_path), "j", "sync_journal.jsonl")
    monkeypatch.setattr(vl, "_JOURNAL_FILE", jf)
    monkeypatch.setattr(vl, "VAULT", vault)
    return vault, jf


def _mk_note(vault: str, name: str, content: str = "正文") -> str:
    p = os.path.join(vault, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p


def _read_journal(jf: str) -> list:
    if not os.path.exists(jf):
        return []
    with open(jf, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def _sync_runs(jf: str) -> list:
    return [r for r in _read_journal(jf) if r.get("action") == "sync_run"]


@pytest.fixture
def feishu_mock(monkeypatch):
    """FeishuOutput 假树：list_children/_find_child_node/ensure_folder_path/save 全内存态。

    save 语义与真实一致：upsert（父下同名删旧建新），_last_created 带新节点 token，
    新节点挂回树保证复验 collect_feishu 可见。
    """
    from articles.feishu import FeishuOutput
    state = {"tree": {"root_tok": []}, "saved": []}
    counter = {"n": 0}

    def fake_init(self, name: str = "feishu"):
        self.name = name
        self.wiki_space = "sp_test"
        self.wiki_parent_node = "root_tok"
        self._cli_available = True
        self._last_created = None

    monkeypatch.setattr(FeishuOutput, "__init__", fake_init)
    monkeypatch.setattr(FeishuOutput, "is_available", lambda self: True)

    def fake_list_children(self, parent_token: str) -> list:
        return list(state["tree"].get(parent_token, []))

    def fake_find_child(self, parent_token: str, title: str):
        for node in state["tree"].get(parent_token, []):
            if node.get("title") == title:
                return node
        return None

    def fake_split_subdir(self, filename: str):
        parts = [p for p in filename.replace("\\", "/").split("/") if p.strip()]
        if len(parts) <= 1:
            return [], filename
        return parts[:-1], parts[-1]

    def fake_ensure_inbox_node(self) -> str:
        return "root_tok"

    def fake_ensure_folder_path(self, dirs: list) -> str:
        parent = "root_tok"
        for d in dirs:
            found = fake_find_child(self, parent, d)
            if found:
                parent = found["node_token"]
                continue
            counter["n"] += 1
            tok = f"tok_new_{counter['n']}"
            state["tree"].setdefault(parent, []).append({"title": d, "node_token": tok})
            parent = tok
        return parent

    def fake_save(self, content: str, filename: str, parent_token=None) -> bool:
        counter["n"] += 1
        tok = f"tok_new_{counter['n']}"
        _dirs, base = fake_split_subdir(self, filename)
        title = base[:-3] if base.endswith(".md") else base
        parent = parent_token or "root_tok"
        state["tree"].setdefault(parent, [])
        state["tree"][parent] = [n for n in state["tree"][parent]
                                 if n.get("title") != title]
        state["tree"][parent].append({"title": title, "node_token": tok})
        state["saved"].append({"filename": filename, "parent": parent,
                               "content": content})
        self._last_created = {"node_token": tok}
        return True

    monkeypatch.setattr(FeishuOutput, "list_children", fake_list_children)
    monkeypatch.setattr(FeishuOutput, "_find_child_node", fake_find_child)
    monkeypatch.setattr(FeishuOutput, "_split_subdir", fake_split_subdir)
    monkeypatch.setattr(FeishuOutput, "ensure_inbox_node", fake_ensure_inbox_node)
    monkeypatch.setattr(FeishuOutput, "ensure_folder_path", fake_ensure_folder_path)
    monkeypatch.setattr(FeishuOutput, "save", fake_save)
    return state


class _FakeFeishuUnavailable:
    """audit_sync 薄壳测试用：真 main 不该再实例化 FeishuOutput（RED 期防真 CLI 调用）。"""

    def __init__(self):
        self.wiki_parent_node = ""

    def is_available(self) -> bool:
        return False


# ── reconcile 收紧（P0-1 残留：apply 只清两端 link 均空的丢失记录）────────────


class TestReconcileTighten:
    def test_apply_clears_only_never_uploaded_lost(self, tmp_path, monkeypatch):
        vault, _jf = _setup_vault(tmp_path, monkeypatch)
        os.makedirs(vault, exist_ok=True)
        from articles import dedup
        dedup.mark_summarized(url="https://a.example/x", title="Note A",
                              filename="NoteA.md")
        dedup.mark_summarized(url="https://b.example/y", title="Note B",
                              filename="NoteB.md")
        dedup.set_links(url="https://b.example/y", feishu_link="tok_b1")
        vl.cmd_reconcile(apply=True, scope_substr="Note")
        index = dedup._load_index()
        urls = {r.get("source_url") for r in index.values()}
        assert "https://a.example/x" not in urls  # 两端 link 空 → 可清
        assert "https://b.example/y" in urls      # 有飞书 link → 保留

    def test_apply_retains_obsidian_only_link(self, tmp_path, monkeypatch):
        vault, _jf = _setup_vault(tmp_path, monkeypatch)
        os.makedirs(vault, exist_ok=True)
        from articles import dedup
        dedup.mark_summarized(url="https://c.example/z", title="Note C",
                              filename="NoteC.md")
        dedup.set_links(url="https://c.example/z", obsidian_link="NoteC.md")
        vl.cmd_reconcile(apply=True, scope_substr="Note")
        assert "https://c.example/z" in {r.get("source_url")
                                        for r in dedup._load_index().values()}

    def test_dry_partition_no_changes(self, tmp_path, monkeypatch):
        vault, _jf = _setup_vault(tmp_path, monkeypatch)
        os.makedirs(vault, exist_ok=True)
        from articles import dedup
        dedup.mark_summarized(url="https://a.example/x", title="Note A",
                              filename="NoteA.md")
        dedup.mark_summarized(url="https://b.example/y", title="Note B",
                              filename="NoteB.md")
        dedup.set_links(url="https://b.example/y", feishu_link="tok_b1")
        vl.cmd_reconcile(apply=False, scope_substr="")
        urls = {r.get("source_url") for r in dedup._load_index().values()}
        assert "https://a.example/x" in urls and "https://b.example/y" in urls

    def test_scope_guard_still_required(self, tmp_path, monkeypatch):
        _setup_vault(tmp_path, monkeypatch)
        with pytest.raises(SystemExit):
            vl.cmd_reconcile(apply=True, scope_substr="")


# ── 死链清理（feishu_link 指向 token 不在飞书树 → 只清 feishu_link）──────────


class TestDeadLinks:
    def test_apply_clears_dead_link_keeps_obsidian(self, tmp_path, monkeypatch,
                                                   feishu_mock):
        vault, jf = _setup_vault(tmp_path, monkeypatch)
        from articles import dedup
        dedup.mark_summarized(url="https://d.example/1", title="Gone D",
                              filename="GoneD.md")
        dedup.set_links(url="https://d.example/1", feishu_link="tok_dead9",
                        obsidian_link="notes/GoneD.md")
        vl._run_push(apply=True, vault=vault)
        rec = dedup.get_entry(url="https://d.example/1")
        assert rec["feishu_link"] == ""
        assert rec["obsidian_link"] == "notes/GoneD.md"
        runs = _sync_runs(jf)
        assert runs and runs[0]["dead_links_cleared"] == 1

    def test_alive_link_untouched(self, tmp_path, monkeypatch, feishu_mock):
        vault, jf = _setup_vault(tmp_path, monkeypatch)
        from articles import dedup
        feishu_mock["tree"]["root_tok"] = [{"title": "Alive", "node_token": "tok_alive1"}]
        dedup.mark_summarized(url="https://e.example/2", title="Alive E",
                              filename="AliveE.md")
        dedup.set_links(url="https://e.example/2", feishu_link="tok_alive1",
                        obsidian_link="AliveE.md")
        vl._run_push(apply=True, vault=vault)
        rec = dedup.get_entry(url="https://e.example/2")
        assert rec["feishu_link"] == "tok_alive1"
        assert _sync_runs(jf)[0]["dead_links_cleared"] == 0

    def test_dry_reports_dead_no_write(self, tmp_path, monkeypatch, feishu_mock):
        vault, jf = _setup_vault(tmp_path, monkeypatch)
        from articles import dedup
        dedup.mark_summarized(url="https://d.example/1", title="Gone D",
                              filename="GoneD.md")
        dedup.set_links(url="https://d.example/1", feishu_link="tok_dead9")
        res = vl._run_push(apply=False, vault=vault)
        assert res is not None
        assert dedup.get_entry(url="https://d.example/1")["feishu_link"] == "tok_dead9"
        assert _read_journal(jf) == []  # dry 不写 journal

    def test_url_form_dead_link_cleared(self, tmp_path, monkeypatch, feishu_mock):
        vault, _jf = _setup_vault(tmp_path, monkeypatch)
        from articles import dedup
        dedup.mark_summarized(url="https://d.example/1", title="Gone D",
                              filename="GoneD.md")
        dedup.set_links(url="https://d.example/1",
                        feishu_link="https://xx.feishu.cn/wiki/tok_dead9")
        vl._run_push(apply=True, vault=vault)
        assert dedup.get_entry(url="https://d.example/1")["feishu_link"] == ""


# ── push 补传 + 复验回写登记表 ───────────────────────────────────────────────


class TestPushMissing:
    def test_push_new_note_writes_back_token_and_ledger(self, tmp_path, monkeypatch,
                                                        feishu_mock):
        vault, jf = _setup_vault(tmp_path, monkeypatch)
        from articles import dedup
        note = _mk_note(vault, "NoteP.md",
                        "---\ntitle: NoteP\nsource_url: https://p.example/1\n"
                        "---\n\n正文")
        dedup.mark_summarized(url="https://p.example/1", title="NoteP",
                              filename="NoteP.md")
        res = vl._run_push(apply=True, vault=vault)
        assert res["missing"] == 1 and res["pushed"] == 1
        assert len(feishu_mock["saved"]) == 1
        assert "title: NoteP" not in feishu_mock["saved"][0]["content"]  # 剥 frontmatter
        with open(note, encoding="utf-8") as f:
            assert "feishu_node_token: tok_new_1" in f.read()  # 本地写回 token
        rec = dedup.get_entry(url="https://p.example/1")
        assert rec["feishu_link"] == "tok_new_1"      # 登记表飞书 link = 节点 token
        assert rec["obsidian_link"] == "NoteP.md"     # vault 相对路径（P2-1）
        runs = _sync_runs(jf)
        assert runs and runs[0]["pushed"] == 1 and runs[0]["ledger_linked"] == 1

    def test_existing_note_skipped_and_linked(self, tmp_path, monkeypatch,
                                              feishu_mock):
        vault, jf = _setup_vault(tmp_path, monkeypatch)
        from articles import dedup
        _mk_note(vault, "NoteQ.md",
                 "---\nsource_url: https://q.example/2\n---\n\n正文")
        feishu_mock["tree"]["root_tok"] = [{"title": "NoteQ", "node_token": "tok_q1"}]
        dedup.mark_summarized(url="https://q.example/2", title="NoteQ",
                              filename="NoteQ.md")
        res = vl._run_push(apply=True, vault=vault)
        assert res["pushed"] == 0 and res["skipped"] == 1
        assert feishu_mock["saved"] == []  # 已在飞书 → 不重复推送
        rec = dedup.get_entry(url="https://q.example/2")
        assert rec["feishu_link"] == "tok_q1"   # 惰性回填（PLAN 21 行）
        assert rec["obsidian_link"] == "NoteQ.md"
        assert _sync_runs(jf)[0]["ledger_linked"] == 1

    def test_dry_no_save_no_ledger_change(self, tmp_path, monkeypatch, feishu_mock):
        vault, jf = _setup_vault(tmp_path, monkeypatch)
        from articles import dedup
        _mk_note(vault, "NoteP.md", "正文")
        dedup.mark_summarized(url="https://p.example/1", title="NoteP",
                              filename="NoteP.md")
        res = vl._run_push(apply=False, vault=vault)
        assert res["missing"] == 1
        assert feishu_mock["saved"] == []
        assert dedup.get_entry(url="https://p.example/1")["feishu_link"] == ""
        assert _read_journal(jf) == []

    def test_no_ledger_record_still_pushes(self, tmp_path, monkeypatch, feishu_mock):
        vault, _jf = _setup_vault(tmp_path, monkeypatch)
        from articles import dedup
        note = _mk_note(vault, "NoteR.md", "无登记正文")
        res = vl._run_push(apply=True, vault=vault)
        assert res["pushed"] == 1
        with open(note, encoding="utf-8") as f:
            assert "feishu_node_token: tok_new_1" in f.read()
        assert dedup._load_index() == {}  # 无登记记录 → 登记表不动

    def test_ambiguous_basename_skips_ledger(self, tmp_path, monkeypatch,
                                             feishu_mock):
        vault, jf = _setup_vault(tmp_path, monkeypatch)
        from articles import dedup
        _mk_note(vault, "NoteS.md", "正文")
        dedup.mark_summarized(url="https://s.example/1", title="NoteS",
                              filename="sub/NoteS.md")
        dedup.mark_summarized(url="https://s.example/2", title="NoteS",
                              filename="NoteS.md")
        res = vl._run_push(apply=True, vault=vault)
        assert res["pushed"] == 1  # push 本身不受登记歧义影响
        assert dedup.get_entry(url="https://s.example/1")["feishu_link"] == ""
        assert dedup.get_entry(url="https://s.example/2")["feishu_link"] == ""
        assert _sync_runs(jf)[0]["ledger_linked"] == 0  # 歧义 → 跳过回写不崩


# ── --only 单篇模式 ─────────────────────────────────────────────────────────


class TestOnlyMode:
    def test_only_mode_pushes_single(self, tmp_path, monkeypatch, feishu_mock):
        vault, jf = _setup_vault(tmp_path, monkeypatch)
        _mk_note(vault, "Only1.md", "一")
        _mk_note(vault, "Only2.md", "二")
        res = vl._run_push(apply=True, vault=vault,
                           only=os.path.join(vault, "Only1.md"))
        assert res["pushed"] == 1
        assert [s["filename"] for s in feishu_mock["saved"]] == ["Only1.md"]
        assert _sync_runs(jf)[0]["pushed"] == 1


# ── 被动触发（PLAN 12 行：>7 天自动补跑对账+补传+清死链）──────────────────────


class TestPassiveSync:
    def _patch_journal(self, tmp_path, monkeypatch) -> str:
        jf = os.path.join(str(tmp_path), "j", "sync_journal.jsonl")
        monkeypatch.setattr(vl, "_JOURNAL_FILE", jf)
        return jf

    def test_journal_last_run_action_param(self, tmp_path, monkeypatch):
        self._patch_journal(tmp_path, monkeypatch)
        vl.journal_append("sync_run", ts=100)
        vl.journal_append("gc_run", ts=200)
        assert vl.journal_last_run("sync_run") == 100
        assert vl.journal_last_run("gc_run") == 200
        assert vl.journal_last_run() == 200  # 默认 gc_run 向后兼容

    def test_should_run_sync_window(self, tmp_path, monkeypatch):
        self._patch_journal(tmp_path, monkeypatch)
        now = time.time()
        assert vl.should_run_sync(days=7, now=now) is True  # 从未跑过
        vl.journal_append("sync_run", ts=int(now - 8 * 86400))
        assert vl.should_run_sync(days=7, now=now) is True
        vl.journal_append("sync_run", ts=int(now - 1 * 86400))
        assert vl.should_run_sync(days=7, now=now) is False

    def test_passive_triggers_when_stale(self, tmp_path, monkeypatch):
        self._patch_journal(tmp_path, monkeypatch)
        vl.journal_append("sync_run", ts=int(time.time() - 8 * 86400))
        calls = []

        def fake_run(apply, vault="", only=""):
            calls.append(apply)

        monkeypatch.setattr(vl, "_run_push", fake_run)
        vl.maybe_passive_sync(days=7)
        assert calls == [True]

    def test_passive_skips_when_recent(self, tmp_path, monkeypatch):
        self._patch_journal(tmp_path, monkeypatch)
        vl.journal_append("sync_run", ts=int(time.time()))
        calls = []

        def fake_run(apply, vault="", only=""):
            calls.append(apply)

        monkeypatch.setattr(vl, "_run_push", fake_run)
        vl.maybe_passive_sync(days=7)
        assert calls == []

    def test_passive_swallows_run_errors(self, tmp_path, monkeypatch):
        self._patch_journal(tmp_path, monkeypatch)

        def boom(apply, vault="", only=""):
            raise RuntimeError("网络炸了")

        monkeypatch.setattr(vl, "_run_push", boom)
        vl.maybe_passive_sync(days=7)  # 被动补跑异常绝不外泄


# ── audit_sync 薄壳（P1-5：收编为 push 转发，watchdog 兼容）──────────────────


class TestAuditSyncShell:
    def _fake_vl(self, monkeypatch, calls: list, result):
        fake = types.ModuleType("vault_lifecycle")

        def fake_run(apply, vault="", only=""):
            calls.append({"apply": apply, "vault": vault, "only": only})
            return result

        fake._run_push = fake_run
        monkeypatch.setitem(sys.modules, "vault_lifecycle", fake)

    def test_main_forwards_to_push(self, monkeypatch):
        monkeypatch.setattr(audit, "FeishuOutput", _FakeFeishuUnavailable)
        calls = []
        self._fake_vl(monkeypatch, calls, {"missing": 0})
        monkeypatch.setattr(sys, "argv", ["audit_sync.py", "--fix"])
        audit.main()
        assert calls == [{"apply": True, "vault": "", "only": ""}]

    def test_main_report_mode_no_fix(self, monkeypatch):
        monkeypatch.setattr(audit, "FeishuOutput", _FakeFeishuUnavailable)
        calls = []
        self._fake_vl(monkeypatch, calls, {"missing": 0})
        monkeypatch.setattr(sys, "argv", ["audit_sync.py"])
        audit.main()  # missing=0 → 正常返回
        assert calls[0]["apply"] is False

    def test_main_report_exits_on_missing(self, monkeypatch):
        monkeypatch.setattr(audit, "FeishuOutput", _FakeFeishuUnavailable)
        self._fake_vl(monkeypatch, [], {"missing": 3})
        monkeypatch.setattr(sys, "argv", ["audit_sync.py"])
        with pytest.raises(SystemExit):
            audit.main()

    def test_main_exits_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(audit, "FeishuOutput", _FakeFeishuUnavailable)
        self._fake_vl(monkeypatch, [], None)  # 飞书不可用 → None
        monkeypatch.setattr(sys, "argv", ["audit_sync.py", "--fix"])
        with pytest.raises(SystemExit):
            audit.main()

    def test_main_deprecated_flag_warns(self, monkeypatch, capsys):
        monkeypatch.setattr(audit, "FeishuOutput", _FakeFeishuUnavailable)
        self._fake_vl(monkeypatch, [], {"missing": 0})
        monkeypatch.setattr(sys, "argv",
                            ["audit_sync.py", "--fix", "--feishu-node", "tok_x"])
        audit.main()
        assert "废弃" in capsys.readouterr().out


# ── dedup.set_links_by_key（按登记表主键直改 link）───────────────────────────


class TestSetLinksByKey:
    def _mk_rec(self, url: str = "https://k.example/1", title: str = "K Note") -> str:
        from articles import dedup
        dedup.mark_summarized(url=url, title=title, filename="K.md")
        return dedup.get_entry(url=url)["key"]

    def test_updates_single_field(self):
        from articles import dedup
        key = self._mk_rec()
        out = dedup.set_links_by_key(key, feishu_link="tok_k1")
        assert out["feishu_link"] == "tok_k1"
        rec = dedup.get_entry(url="https://k.example/1")
        assert rec["feishu_link"] == "tok_k1"
        assert rec["obsidian_link"] == ""  # 未传字段不动

    def test_both_fields_preserve_untouched(self):
        from articles import dedup
        key = self._mk_rec()
        dedup.set_links_by_key(key, feishu_link="tok_k1", obsidian_link="K.md")
        out = dedup.set_links_by_key(key, feishu_link="tok_k2")
        assert out["feishu_link"] == "tok_k2"
        assert out["obsidian_link"] == "K.md"
        assert out["title"] == "K Note"  # 非 link 字段原样保留

    def test_unknown_key_returns_empty(self):
        from articles import dedup
        assert dedup.set_links_by_key("no_such_key", feishu_link="t") == {}

    def test_empty_key_returns_empty(self):
        from articles import dedup
        assert dedup.set_links_by_key("", feishu_link="t") == {}
