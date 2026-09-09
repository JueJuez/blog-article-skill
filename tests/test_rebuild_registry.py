"""rebuild_registry：vault 扫描 → 统一登记表 bootstrap（PLAN-20260908 阶段 2）。

真源 = vault 成品笔记；rebuild 默认只读扫描（--apply 才写登记表）：
排除 1 `_` 前缀目录、排除 2 重抓队列 old_paths（D2 完全不登记）、
排除 3 文件名与父目录同名的疑似容器页（单列供人工核对）；
URL 三级兜底复用 migrate_gate._main_url（fm source_url → 正文来源链接 → 标题指纹）。
"""
import json

from articles import dedup

import scripts.rebuild_registry as rebuild


def _mk_note(dirpath, name, url="", title="", body="正文内容。"):
    lines = [f"# {title}"] if title else []
    if url:
        lines += ["", f"**作者**：甲 | **来源链接**：[原文链接]({url})", ""]
    lines.append(body)
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / f"{name}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


# ── 2.1 扫描骨架 ──

def test_scan_normal_vault(tmp_path):
    """正常笔记：扫描计数、来源链接提取、entries 含 url/title/folder。"""
    v = tmp_path / "vault"
    _mk_note(v / "【我的总结】" / "作者A", "n1", url="https://e.com/1", title="甲文")
    res = rebuild.scan_vault(str(v))
    assert res["scanned"] == 1
    assert res["registered"] == 1
    e = res["entries"][0]
    assert e["url"] == "https://e.com/1"
    assert e["key_type"] == "url"
    assert e["title"] == "甲文"
    assert "作者A" in e["folder"]


def test_scan_excludes_underscore_dirs(tmp_path):
    """排除 1：路径任一段 _ 前缀目录（_meta/_scraped）不扫描。"""
    v = tmp_path / "vault"
    _mk_note(v / "blog", "a", url="https://e.com/a")
    _mk_note(v / "_meta", "b", url="https://e.com/b")
    _mk_note(v / "notes" / "_scraped", "c", url="https://e.com/c")
    res = rebuild.scan_vault(str(v))
    assert res["scanned"] == 1
    assert res["excluded_underscore_dirs"] == 2


def test_scan_empty_vault(tmp_path):
    """空库：0 扫描 0 登记，不报错。"""
    v = tmp_path / "vault"
    v.mkdir()
    res = rebuild.scan_vault(str(v))
    assert res["scanned"] == 0
    assert res["registered"] == 0


# ── 2.2 重抓队列排除 ──

def test_scan_excludes_queue_old_paths(tmp_path):
    """排除 2：命中重抓队列 old_paths 的文件不登记（D2 完全不登记）。"""
    v = tmp_path / "vault"
    p = _mk_note(v / "blog", "bad", url="https://e.com/bad", title="旧题")
    arch = tmp_path / "_migrate_gate_archive"
    arch.mkdir()
    (arch / "migrate_scan_20260908_232003.json").write_text(json.dumps({
        "queue": [{"url": "https://e.com/bad", "kind": "article",
                   "folder_hint": "", "old_paths": [str(p)], "old_titles": ["旧题"]}],
    }, ensure_ascii=False), encoding="utf-8")
    res = rebuild.rebuild(str(v))
    assert res["excluded_queue"] == 1
    assert res["registered"] == 0


def test_scan_without_archive_dir(tmp_path):
    """无 _migrate_gate_archive 目录：不排除，照常登记。"""
    v = tmp_path / "vault"
    _mk_note(v / "blog", "a", url="https://e.com/a")
    res = rebuild.rebuild(str(v))
    assert res["excluded_queue"] == 0
    assert res["registered"] == 1


# ── 2.3 三级 URL 提取 ──

def test_url_from_frontmatter_source_url(tmp_path):
    """迁移文带 YAML frontmatter source_url：fm 优先于正文链接。"""
    d = tmp_path / "vault" / "blog"
    d.mkdir(parents=True)
    (d / "m.md").write_text(
        "---\nsource_url: https://e.com/fm\nmigrated_from: 飞书\n---\n"
        "# 标题\n\n**来源链接**：[原文链接](https://e.com/other)\n", encoding="utf-8")
    res = rebuild.scan_vault(str(tmp_path / "vault"))
    assert res["entries"][0]["url"] == "https://e.com/fm"


def test_url_from_body_source_link(tmp_path):
    """真实笔记无 frontmatter：正文「来源链接」行命中。"""
    v = tmp_path / "vault"
    _mk_note(v / "blog", "a", url="https://e.com/body", title="乙文")
    res = rebuild.scan_vault(str(v))
    assert res["entries"][0]["url"] == "https://e.com/body"
    assert res["entries"][0]["key_type"] == "url"


def test_url_fallback_title_fingerprint(tmp_path):
    """无 fm 无正文链接（直播回放类）：落标题指纹键。"""
    d = tmp_path / "vault" / "blog"
    d.mkdir(parents=True)
    (d / "n.md").write_text("# 第10期直播回放\n\n纯正文无链接。\n", encoding="utf-8")
    res = rebuild.scan_vault(str(tmp_path / "vault"))
    e = res["entries"][0]
    assert e["key_type"] == "title_fp"
    assert e["url"] == ""
    assert res["title_fp"] == 1


# ── 2.4 幂等 merge + 容器页 ──

def test_apply_is_idempotent_merge(tmp_path):
    """apply 写登记表；重复跑幂等——人工补的 link 保留、键集合不变。"""
    v = str(tmp_path / "vault")
    _mk_note(tmp_path / "vault" / "blog", "a", url="https://e.com/a", title="甲")
    r1 = rebuild.rebuild(v, apply=True)
    assert r1["registered"] == 1
    rec = dedup.is_summarized(url="https://e.com/a")
    assert rec and rec["source_url"] == "https://e.com/a"
    dedup.set_links(url="https://e.com/a", feishu_link="https://fl/x")
    r2 = rebuild.rebuild(v, apply=True)
    assert r2["registered"] == 1
    assert dedup.is_summarized(url="https://e.com/a")["feishu_link"] == "https://fl/x"
    assert [e["key"] for e in r1["entries"]] == [e["key"] for e in r2["entries"]]


def test_container_page_listed_not_registered(tmp_path):
    """排除 3：文件名与父目录同名的疑似容器页单列、不登记。"""
    v = tmp_path / "vault"
    _mk_note(v / "【监控】" / "B站" / "UP甲" / "系列一", "系列一", url="https://e.com/s1")
    _mk_note(v / "blog", "a", url="https://e.com/a")
    res = rebuild.scan_vault(str(v))
    assert len(res["containers"]) == 1
    assert res["registered"] == 1
