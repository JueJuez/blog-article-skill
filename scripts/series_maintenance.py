"""scripts/series_maintenance.py — 系列课飞书侧运维工具（抽象自多个一次性脚本）。

把原先散落的 _probe_feishu / _relaunch_series / _regen_overview / _verify_series
收敛为单一可复用 CLI，覆盖三类常用运维动作：

  verify         抽样校验某系列在飞书的落盘质量（集数 / 总览数 / 元数据头 / 结论填充率）
  regen-overview 重生成系列总览（走 videos.main._generate_series_overview，upsert 不重复）
  reland         把本地 notes/<系列名>/*.body.md 重新落飞书（upsert 幂等，无需先删）

用法：
  python scripts/series_maintenance.py verify --series "中国好公司"
  python scripts/series_maintenance.py regen-overview --series "中国好公司" --series-dir notes/中国好公司 --url <bvid>
  python scripts/series_maintenance.py reland --series-dir notes/中国好公司 --author <up> --url <bvid>

注意：系列课只落飞书（符合项目默认规则），本工具不碰 Obsidian。
"""
import os
import re
import sys
import json
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

from articles.feishu import FeishuOutput
from videos.main import (
    _save_series_note, _generate_series_overview, _NOTE_TYPE_TAG, _sanitize_filename,
)


def _feishu_series_children(f: FeishuOutput, series_title: str, author: str = "", url: str = ""):
    """沿 series_folder 期望路径逐层 list_children 定位系列容器（B7 修复：只查不建）。

    旧实现走的是缺省挂 wiki 根、找不到就新建的容器接口，verify 这种只读场景
    也会把刚清理过的根容器再次污染。新实现任一层缺失即返回 (None, [])，
    绝不创建任何节点。
    """
    from videos.main import series_folder
    folder = series_folder({}, author or "", series_title, url or "")
    parts = [p for p in folder.split("/") if p]
    tok = f.wiki_parent_node
    if not tok:
        return None, []
    for part in parts:
        hit = next((k for k in f.list_children(tok) if k.get("title") == part), None)
        if hit is None:
            return None, []
        tok = hit.get("obj_token") or hit.get("node_token")
        if not tok:
            return None, []
    return tok, f.list_children(tok)


def cmd_verify(args):
    f = FeishuOutput()
    if not f.is_available():
        print("⚠️ 飞书不可用"); return
    ctok, kids = _feishu_series_children(f, args.series, author=args.author, url=args.url or "")
    if ctok is None:
        print(f"⚠️ 飞书无「{args.series}」容器"); return
    eps = [k for k in kids if re.match(r'^第\d{2}集', k.get("title", ""))]
    overviews = [k for k in kids if k.get("title") == "00_系列总览"]
    print(f"📚 系列「{args.series}」飞书容器：")
    print(f"   集节点：{len(eps)} 个")
    print(f"   总览节点：{len(overviews)} 个" + ("  ⚠️ 应为 1" if len(overviews) != 1 else "  ✅"))
    if not eps:
        return
    # 抽样首集，检查元数据头 + 结论填充
    sample = eps[0]
    obj = sample.get("obj_token") or sample.get("node_token")
    r = f._run_cli_command(["docs", "+fetch", "--doc", obj, "--doc-format", "markdown",
                            "--as", "user", "--json"])
    c = (r.get("data", {}) or {}).get("document", {}).get("content", "") if isinstance(r, dict) else ""
    has_tag = "#" in c and ("标签" in c or c.lstrip().startswith("#"))
    has_author = "**作者**" in c
    has_link = "**来源链接**" in c
    print(f"   抽样首集《{sample.get('title')}》："
          f"含#标签={'✅' if has_tag else '❌'} 含作者={'✅' if has_author else '❌'} 含来源链接={'✅' if has_link else '❌'}")
    # 结论填充率（总览表里 （待总结） 占比）
    if overviews:
        ov = overviews[0]
        oobj = ov.get("obj_token") or ov.get("node_token")
        ro = f._run_cli_command(["docs", "+fetch", "--doc", oobj, "--doc-format", "markdown",
                                 "--as", "user", "--json"])
        oc = (ro.get("data", {}) or {}).get("document", {}).get("content", "") if isinstance(ro, dict) else ""
        pending = oc.count("（待总结）")
        print(f"   总览结论（待总结）格数：{pending}" + ("  ✅" if pending == 0 else "  ⚠️ 有空缺"))


def cmd_regen_overview(args):
    f = FeishuOutput()
    if not f.is_available():
        print("⚠️ 飞书不可用"); return
    series_dir = args.series_dir or os.path.join(ROOT, "notes", _sanitize_filename(args.series))
    from videos.main import series_folder
    folder = series_folder({}, args.author or "", args.series, args.url or "")
    out = _generate_series_overview(args.series, series_dir, args.url or "", obsidian=False, folder=folder)
    print(f"✅ 总览已重生成（upsert，不重复）：{out}")


def cmd_reland(args):
    f = FeishuOutput()
    if not f.is_available():
        print("⚠️ 飞书不可用"); return
    sd = args.series_dir
    if not os.path.isdir(sd):
        print(f"⚠️ 目录不存在：{sd}"); return
    from videos.main import series_folder
    folder = series_folder({}, args.author or "", args.series, args.url or "")
    bodies = sorted(glob_body(sd))
    print(f"🔁 重落地 {len(bodies)} 个 body（upsert 幂等）...")
    ok = 0
    for b in bodies:
        base = os.path.splitext(os.path.basename(b))[0]
        try:
            content = open(b, encoding="utf-8").read()
        except Exception as e:
            print(f"  ❌ 读 {b}: {e}"); continue
        note_type = "structured"
        tags = [args.series, _NOTE_TYPE_TAG.get(note_type, "视频笔记")]
        try:
            _save_series_note(content, sd, base, args.author or "", args.url or "", tags, note_type,
                              obsidian=False, folder=folder)
            ok += 1
            print(f"  ✅ 已落盘：{base}")
        except Exception as e:
            print(f"  ❌ 落盘失败 {base}: {e}")
    # 重生成总览
    try:
        _generate_series_overview(args.series, sd, args.url or "", obsidian=False, folder=folder)
        print("  🧭 总览已重生成")
    except Exception as e:
        print(f"  ⚠️ 总览重生成失败（非致命）：{e}")
    print(f"=== reland 汇总：成功 {ok}/{len(bodies)} ===")


def glob_body(sd):
    import glob
    return glob.glob(os.path.join(sd, "*.body.md"))


def main():
    ap = argparse.ArgumentParser(description="系列课飞书运维工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("verify", help="抽样校验飞书落盘质量")
    v.add_argument("--series", required=True)
    v.add_argument("--author", default="")
    v.add_argument("--url", default="")

    r = sub.add_parser("regen-overview", help="重生成系列总览（upsert）")
    r.add_argument("--series", required=True)
    r.add_argument("--series-dir", default=None)
    r.add_argument("--author", default="")
    r.add_argument("--url", default="")

    rl = sub.add_parser("reland", help="重落地本地 body 到飞书（upsert）")
    rl.add_argument("--series", required=True)
    rl.add_argument("--series-dir", required=True)
    rl.add_argument("--author", default="")
    rl.add_argument("--url", default="")

    args = ap.parse_args()
    {"verify": cmd_verify, "regen-overview": cmd_regen_overview, "reland": cmd_reland}[args.cmd](args)


if __name__ == "__main__":
    main()
