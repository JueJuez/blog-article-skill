"""编排：把 notes/哲学思辨，知行合一/_summary_*.md 落盘到系列容器（默认飞书，--obsidian 时追加 Obsidian）。

复用生产逻辑（videos/main.py 的 _save_series_note / _generate_series_overview 等价实现），
不重新发明：format_note_with_prompt(add_metadata=False) 原样保留子 Agent 产出的 #标签/作者 行；
Obsidian 落子目录、飞书落同名 wiki 容器节点（ensure_series_node 复用已有容器）。

第6集因平台内容安全策略被拦：无 _summary_ 文件，本脚本自然跳过；其原文已由主 Agent 按用户指令直转进 Obsidian 系列目录（头部标「未经 AI 总结」），未推飞书。
"""
import os
import re
import sys
import json

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv(".env")

from prompts.templates import format_note_with_prompt
from articles.manager import OutputManager
from articles.feishu import FeishuOutput

SERIES_TITLE = "哲学思辨，知行合一"
SERIES_DIR = os.path.join("notes", SERIES_TITLE)  # 仅用于取 basename，不直接写本地
AUTHOR = "土斯土耶夫斯基"
SERIES_URL = "https://www.bilibili.com/video/BV1p9376JEJS"

# 各集 bvid（来自 monitors/_series_manifest.json）
_manifest = {}
try:
    for it in json.load(open("monitors/_series_manifest.json", encoding="utf-8")):
        _manifest[it["page"]] = it
except Exception:
    pass


def local_write_enabled(obsidian: bool = False) -> bool:
    mgr = OutputManager(obsidian=obsidian)
    names = {out.name for out in mgr.get_available_outputs()}
    return not (names & {"obsidian", "feishu"})


def save_one(content: str, base_name: str, url: str, obsidian: bool = False):
    """写到所有可用输出端（默认仅飞书；带 obsidian 时追加 Obsidian），返回「失败的输出名」列表（用于末尾汇总，绝不静默吞掉）。"""
    formatted = format_note_with_prompt(
        content=content, author=AUTHOR, url=url,
        tags=["要点提炼", "转载"], add_metadata=False,
    )
    filename = base_name + ".md"
    failed = []
    # 有云同步则不写本地 notes/（用户偏好）
    if local_write_enabled():
        path = os.path.join(SERIES_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(formatted)
    mgr = OutputManager(obsidian=obsidian)
    series_folder = os.path.basename(SERIES_DIR)
    for out in mgr.get_available_outputs():
        try:
            ok = out.save_series(formatted, filename, series_folder)
        except Exception as e:
            ok = False
            print(f"   ⚠️ {out.name} 同步异常（非致命）：{e}")
        if ok:
            print(f"   🔗 已同步 {out.name}：{series_folder}/{filename}")
        else:
            failed.append(out.name)
            print(f"   ❌ {out.name} 同步失败：{series_folder}/{filename}")
    return failed


def extract_h1(md: str) -> str:
    for line in md.splitlines():
        s = line.strip()
        if s.startswith('# ') and not s.startswith('## '):
            return s[2:].strip()
    return ""


def extract_one_liner(md: str) -> str:
    for line in md.splitlines():
        s = line.strip().lstrip('>').strip()
        if s.startswith('**一句话核心结论') or s.startswith('**30秒速览'):
            for sep in ('：', ':'):
                if sep in s:
                    return s.split(sep, 1)[1].strip().strip('*').strip()
    return ""


def main() -> None:
    import argparse
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--obsidian", action="store_true", help="同时写入 Obsidian（默认只写飞书）")
    _args = _ap.parse_args()
    obsidian = _args.obsidian

    summary_files = sorted(
        f for f in os.listdir(SERIES_DIR)
        if f.startswith("_summary_") and f.endswith(".md")
    )
    print(f"=== 待落盘 {len(summary_files)} 集 ===")
    all_failed = set()
    for f in summary_files:
        m = re.match(r'^_summary_第(\d{2})集_(.*)\.md$', f)
        page = int(m.group(1)) if m else 0
        base = f[len("_summary_"):-len(".md")]  # 第XX集_标题
        with open(os.path.join(SERIES_DIR, f), encoding="utf-8") as fh:
            content = fh.read()
        url = SERIES_URL
        if page in _manifest and _manifest[page].get("bvid"):
            url = "https://www.bilibili.com/video/" + _manifest[page]["bvid"]
        print(f"[第{page:02d}集] {base}")
        all_failed.update(save_one(content, base, url))

    # ---- 生成 00_系列总览.md（从本地 _summary_ 读，更可靠）----
    print("\n=== 生成系列总览 ===")
    rows = []
    for f in summary_files:
        m = re.match(r'^_summary_第(\d{2})集_(.*)\.md$', f)
        page = int(m.group(1)) if m else 0
        with open(os.path.join(SERIES_DIR, f), encoding="utf-8") as fh:
            md = fh.read()
        title = extract_h1(md) or m.group(2)
        one = extract_one_liner(md) or "（待总结）"
        title = title.replace('|', '/')
        one = one.replace('|', '/')
        note_file = f[len("_summary_"):]  # 第XX集_标题.md
        note_link = f"[笔记](./{note_file})"
        rows.append((page, title, one, note_link))
    rows.sort(key=lambda r: r[0])

    if len(rows) > 1:
        chain = " → ".join(f"第{r[0]:02d}集 {r[1]}" for r in rows)
        learning_path = (
            "（无 AI 生成路径，按发布顺序的朴素建议）\n"
            f"建议从「第{rows[0][0]:02d}集 {rows[0][1]}」开始，依次：{chain}。"
            "若某集正文标注了先修/依赖，请优先补齐前置集再进入后续。"
        )
    else:
        learning_path = ""

    lines = [
        f"# {SERIES_TITLE} · 系列总览", "",
        f"> 系列链接：{SERIES_URL}",
        f"> 共 {len(rows)} 集（每集独立成篇，详见下方链接）", "",
        "## 各集导航", "",
        "| 集 | 标题 | 一句话核心结论 | 笔记 |",
        "| --- | --- | --- | --- |",
    ]
    for page, title, one, note_link in rows:
        lines.append(f"| 第{page:02d}集 | {title} | {one} | {note_link} |")
    lines += ["", "---", ""]
    if learning_path:
        lines += ["## 学习路径", "", learning_path, "", "---", ""]
    lines += ["*本总览由 blog-article-skill 自动生成，系列课每集总结后更新。", ""]
    overview_content = "\n".join(lines)
    overview_name = "00_系列总览.md"

    # 删旧总览节点（避免飞书重复）
    feishu = FeishuOutput()
    if feishu.is_available():
        parent = feishu.ensure_series_node(SERIES_TITLE)
        if parent:
            try:
                listing = feishu._run_cli_command([
                    "wiki", "+node-list",
                    "--parent-node-token", parent,
                    "--space-id", feishu.wiki_space,
                    "--as", "user", "--json", "--page-all",
                ])
                if listing and listing.get("ok"):
                    for item in listing.get("data", {}).get("nodes", []):
                        if item.get("title") == "00_系列总览":
                            feishu.delete_node(item.get("node_token"))
                            print("   🗑️ 已删旧总览节点")
            except Exception as e:
                print(f"   ⚠️ 查旧总览失败（不影响继续）：{e}")

    if local_write_enabled(obsidian):
        with open(os.path.join(SERIES_DIR, overview_name), "w", encoding="utf-8") as fh:
            fh.write(overview_content)
    series_folder = os.path.basename(SERIES_DIR)
    mgr = OutputManager(obsidian=obsidian)
    for out in mgr.get_available_outputs():
        try:
            ok = out.save_series(overview_content, overview_name, series_folder)
        except Exception as e:
            ok = False
            print(f"   ⚠️ {out.name} 总览同步异常（非致命）：{e}")
        if ok:
            print(f"   🔗 已同步 {out.name} 总览：{series_folder}/{overview_name}")
        else:
            all_failed.add(out.name)
            print(f"   ❌ {out.name} 总览同步失败：{series_folder}/{overview_name}")

    # ---- 同步失败汇总（绝不静默）----
    if all_failed:
        print(f"\n⚠️ 以下输出端有同步失败：{', '.join(sorted(all_failed))}（请结合下方审计排查）")
    else:
        print("\n✓ 全部输出端同步成功")

    # ---- 一致性闸门（仅双写模式启用：Obsidian ↔ 飞书 整树 diff，不靠记忆力兜底）----
    # 单写（默认只写飞书）模式下 Obsidian 为空，跑审计只会误报"缺飞书"，故仅在用户显式要 Obsidian 时校验。
    if obsidian and os.getenv("AUDIT_SYNC", "1") != "0":
        try:
            from audit_sync import run_audit
            print("\n=== 一致性闸门（audit_sync：Obsidian ↔ 飞书）===")
            missing, orphan = run_audit(fix=False)
            if missing:
                print(f"⚠️ 检测到 {len(missing)} 篇缺飞书，可运行：python audit_sync.py --fix 补传")
        except Exception as e:
            print(f"   ⚠️ 审计跳过（不影响已写入内容）：{e}")

    print("\n=== 系列课落盘完成 ===")


if __name__ == "__main__":
    main()
