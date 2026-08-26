#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/migrate_feishu_structure.py — 确定性飞书节点迁移（代码执行，非人工逐条敲命令）。

目标（用户 2026-08-25 决策）：
  1. 落盘根 = AI 总结笔记(FX33)（由 .env FEISHU_WIKI_PARENT_NODE 指定）。
  2. 【监控】与【我的总结】都必须是 AI 总结笔记 的直接子节点。
  3. 所有监控账号 / scys 内容归位到 【监控】/<平台>/<账号(或领域)>。
  4. 清理之前误建在 space 根的重复容器（【监控】/【我的笔记】），避免分裂。

实现要点（确定性 / 幂等）：
  - 节点定位只依赖已知 node_token（已通过 walk 核实），运行期再校验存在。
  - 每个 move 前检查「目标父下是否已存在同名节点」或「源已在目标父下」→ 跳过，可重复执行。
  - lark-cli 调用走 shell（继承 PATH 中的 lark-cli）。
用法：
  python scripts/migrate_feishu_structure.py            # 实际执行
  python scripts/migrate_feishu_structure.py --dry      # 只打印计划不执行
"""
import subprocess
import json
import sys

SPACE = "7636965310725115074"
AI_SUMMARY = "FX33wKHwZiMzJqk7BQQctHD3nKh"          # AI 总结笔记（根）

# 规范【监控】容器（已存在，位于 AI 总结笔记 下）
MONITOR = "VnVTwf8vJi7VFgkyKG4cwCTpnfb"            # 【监控】@AI总结笔记
MONITOR_SCYS = "F3vhwcDshieHoRkO8eMctsGdngz"       # 【监控】/生财有术（scys 根）
PF_BILI = "PQUpwc2lziwcaOkMKZMcXFS4ntb"           # 【监控】/B站
PF_PUB = "EkJ5wp3hXigXlfkvbg5cxxVinde"            # 【监控】/公众号

# 之前误建在 space 根的重复容器（本次清理）
DUP_MONITOR_ROOT = "FKF1wXn7biWmsIkIL5nc2qCJn8f"  # 【监控】@ROOT
DUP_MYNOTES_ROOT = "RBfIwZYTmiXtzkkfoUmcxe5ZnKc"  # 【我的笔记】@ROOT
DUP_PUB = "W4bqwMHCzih59tk6sKIc2o8lneh"           # FKF1/公众号
DUP_SERIES = "IpoQw7F8Hi5neqkuJtDcC2glnHg"        # FKF1/系列课
DUP_SCYS = "EcnJwyJNSi9YsXk6uklcgRPfndg"          # FKF1/生财有术（32 扁平）

# 误塞在 投资交易(TpRp) 下的 6 个监控账号
TPRP = "TpRpweO09iJY6LkMDK2cZZCPnsc"
ACCT_BILI = {  # B站账号 -> token（在 TpRp 下）
    "笨笨的韭菜": "RgGBwWYq5iwUb6kYRkgcrQbPnqP",
    "舟亦横": "UlMUwR8rNi8XRRksuepcUjLGnTg",
    "土斯土耶夫斯基": "WV0awKPYtin0dak5fbTcLMrBnhd",
    "Mark__Huang": "Wqvuw4SvGiLgDckJmPocIt2anXh",
    "青枫浦上Q": "Sd39wOiswi16KvkWIxCcfZVWn5e",
}
ACCT_PUB = {  # 公众号账号 -> token（在 TpRp 下）
    "DeepVan的逃生地牢": "LQUEwmhgsisCC1kws23ct4lTnNf",
}
# 误建容器里的账号
ZHONDER = "OFzswpEXfiJe5kksS0ecwGQpn8g"          # 中金点睛（在 DUP_PUB）
QIANDao = "MqrswUaGeiK53IkZxSbc9l3sn8d"           # 千刀千法（在 DUP_SERIES）

# scys：AI 总结笔记/生财有术（4 领域，需并入 【监控】/生财有术）
YBYM = "YByMw9zckiapIfkBMsucpjTsnOf"
# DUP_SCYS（32 扁平）并入 【监控】/生财有术/未分类

# 用户自己的笔记文件夹（移到 【我的总结】）
USER_FOLDERS = {
    "01_独立开发": "CFBDwOoHgiHHwokfr79cBRrsnEc",
    "02_流量变现": "S4ljwRnPziHlvfkSxtCcXAjInIg",
    "03_AI提效": "Z2aywBDUGiSkSikX5RJcixJVnGb",
    "04_流量获取": "AZMrwg9lyiSEBokYVkNcZyaOn6f",
    "05_投资交易": "N1AxwEoYwiIRekkR6VvcI0uentc",
    "06_认知成长": "K6WdwyIehiLvscki1hic1ZFnnac",
    "07_内容创作": "LWMRwwpdJiuduukSQQwc2pxFnKh",
    "优秀的直播录像": "X8pkwh4MmiqyBDk1wUrclEUenxb",
    "价值投资知行合一": "Rpljw0kE2iwXg4kZArZcRvqFnEd",
    "副业增长": "AbyEwFE1BiMDdtkXf8jcQUsjn2d",
    "独立开发出海": "SjBrwvPUJifU0dkZMTgcy8Nsnld",
    "哲学思辨，知行合一": "LF8swyTSCiU5VDkvJSrc2KJ3n0d",
    "中国好公司": "SmEcwiiJciaLXHk6oEgcduwanEd",
    "股市系统教学": "IarNw4WBMiBSrSkkSZpc1hYTnkh",
    "低估值投资方法论系列": "Tr7cw5NpticW3ZkZsmZcRbYynPe",
}

DRY = "--dry" in sys.argv


def cli(args):
    cmd = ["lark-cli"] + args
    out = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except Exception:
        return None


def node_get(tok):
    d = cli(["wiki", "+node-get", "--node-token", tok, "--as", "user", "--json"])
    if d and d.get("ok"):
        return d.get("data", {})
    return None


def children_of(tok):
    d = cli(["wiki", "+node-list", "--space-id", SPACE, "--parent-node-token", tok,
             "--as", "user", "--json"])
    if d and d.get("ok"):
        return d.get("data", {}).get("nodes", [])
    return []


def exists_under(parent, title):
    for n in children_of(parent):
        if n.get("title") == title:
            return n.get("node_token")
    return None


def parent_of(tok):
    n = node_get(tok)
    return n.get("parent_node_token") if n else None


def move_node(src, dst_parent, label):
    cur = parent_of(src)
    if cur == dst_parent:
        print(f"  ⏭ 跳过(已就位): {label}")
        return True
    if DRY:
        print(f"  🔀 DRY move: {label} -> parent {dst_parent}")
        return True
    d = cli(["wiki", "+move", "--node-token", src, "--target-parent-token", dst_parent,
             "--as", "user", "--json"])
    ok = bool(d and d.get("ok"))
    print(f"  {'✅' if ok else '❌'} move: {label} -> parent {dst_parent}")
    return ok


def merge_or_move_folder(src_folder, dst_parent, label):
    """账号/容器归位：若目标父下已有同名节点 → 合并笔记(去重)后删空源；否则整搬。"""
    info = node_get(src_folder)
    src_title = (info or {}).get("title", "")
    existing = exists_under(dst_parent, src_title) if src_title else None
    if existing:
        move_all_notes(src_folder, existing, label)
        if not children_of(src_folder):
            delete_node(src_folder, f"{label} (源已迁空)")
        else:
            print(f"  ⚠️ 源仍有残留子节点未迁: {label}")
    else:
        move_node(src_folder, dst_parent, label)


def move_all_notes(src_folder, dst_folder, label):
    """把 src_folder 下的直接笔记节点逐个移到 dst_folder（用于合并同名桶）。"""
    moved = 0
    for n in children_of(src_folder):
        if exists_under(dst_folder, n["title"]):
            continue
        if DRY:
            print(f"    🔀 DRY move note: {label}/{n['title'][:30]} -> {dst_folder}")
            moved += 1
            continue
        d = cli(["wiki", "+move", "--node-token", n["node_token"],
                 "--target-parent-token", dst_folder, "--as", "user", "--json"])
        if d and d.get("ok"):
            moved += 1
    print(f"  ✅ 迁移 {moved} 篇笔记: {label}")
    return moved


def create_node(parent, title):
    existing = exists_under(parent, title)
    if existing:
        return existing
    if DRY:
        print(f"  📁 DRY create: {title} under {parent}")
        return "DRY_" + title
    d = cli(["wiki", "+node-create", "--title", title, "--node-type", "origin",
             "--obj-type", "docx", "--parent-node-token", parent,
             "--space-id", SPACE, "--as", "user", "--json"])
    if d and d.get("ok"):
        tok = (d.get("data", {}) or {}).get("node_token")
        print(f"  📁 已建容器「{title}」：{tok}")
        return tok
    return ""


def delete_node(tok, label, lift_children=False):
    if DRY:
        print(f"  🗑 DRY delete: {label} ({tok})")
        return
    args = ["wiki", "+node-delete", "--node-token", tok, "--as", "user", "--json"]
    if lift_children:
        args.append("--include-children=false")
    d = cli(args)
    print(f"  {'✅' if (d and d.get('ok')) else '❌'} delete: {label}")


def main():
    print(f"{'[DRY-RUN] ' if DRY else ''}=== 飞书节点迁移（确定性）===")
    # 0) 校验关键节点存在
    assert node_get(AI_SUMMARY), "AI 总结笔记 token 失效"
    assert node_get(MONITOR), "【监控】@AI总结笔记 token 失效"
    assert node_get(MONITOR_SCYS), "【监控】/生财有术 token 失效"

    # 1) 确保 【监控】/系列课 桶存在（千刀千法用）
    series_bucket = create_node(MONITOR, "系列课")

    # 2) 迁移 中金点睛（公众号）
    merge_or_move_folder(ZHONDER, PF_PUB, "中金点睛 -> 【监控】/公众号")
    # 3) 迁移 千刀千法（系列课）
    merge_or_move_folder(QIANDao, series_bucket, "千刀千法 -> 【监控】/系列课")

    # 4) 迁移 TpRp 下的 6 个监控账号（与 【监控】/<平台> 下已有同名节点合并）
    TPRP_ACCOUNTS = {
        "笨笨的韭菜": (ACCT_BILI["笨笨的韭菜"], PF_BILI),
        "舟亦横": (ACCT_BILI["舟亦横"], PF_BILI),
        "土斯土耶夫斯基": (ACCT_BILI["土斯土耶夫斯基"], PF_BILI),
        "Mark__Huang": (ACCT_BILI["Mark__Huang"], PF_BILI),
        "青枫浦上Q": (ACCT_BILI["青枫浦上Q"], PF_BILI),
        "DeepVan的逃生地牢": (ACCT_PUB["DeepVan的逃生地牢"], PF_PUB),
    }
    for name, (tok, target) in TPRP_ACCOUNTS.items():
        merge_or_move_folder(tok, target, f"{name} -> 【监控】/<平台>")

    # 5) scys：YByM 的 4 领域并入 【监控】/生财有术
    #    小程序需合并（F3vh 已有 小程序 Ltb5）；其余 3 个直接移
    f3vh_xiaochengxu = exists_under(MONITOR_SCYS, "小程序")
    for dom in children_of(YBYM):
        if dom["title"] == "小程序":
            if f3vh_xiaochengxu:
                move_all_notes(dom["node_token"], f3vh_xiaochengxu,
                               "YByM/小程序 -> 【监控】/生财有术/小程序")
            else:
                move_node(dom["node_token"], MONITOR_SCYS, "YByM/小程序 -> 【监控】/生财有术")
        else:
            move_node(dom["node_token"], MONITOR_SCYS,
                      f"YByM/{dom['title']} -> 【监控】/生财有术")
    # YBYM 域移空后删除
    if not children_of(YBYM) and node_get(YBYM):
        delete_node(YBYM, "生财有术@AI总结笔记(已迁空)")

    # 6) scys：DUP_SCYS（32 扁平）并入 【监控】/生财有术/未分类
    uncat = create_node(MONITOR_SCYS, "未分类")
    move_all_notes(DUP_SCYS, uncat, "FKF1/生财有术(32扁平) -> 【监控】/生财有术/未分类")
    if not children_of(DUP_SCYS) and node_get(DUP_SCYS):
        delete_node(DUP_SCYS, "FKF1/生财有术(已迁空)")

    # 7) 创建 【我的总结】 容器（用户指定的名称），迁移用户自己的笔记文件夹
    mynotes = create_node(AI_SUMMARY, "【我的总结】")
    if not DRY and mynotes:
        for name, tok in USER_FOLDERS.items():
            move_node(tok, mynotes, f"{name} -> 【我的总结】/{name}")

    # 8) 清理误建在 space 根的重复容器（其子节点已迁走，应已空）
    for tok, label in [
        (DUP_PUB, "FKF1/公众号(空)"),
        (DUP_SERIES, "FKF1/系列课(空)"),
        (DUP_MONITOR_ROOT, "【监控】@ROOT(误建)"),
        (DUP_MYNOTES_ROOT, "【我的笔记】@ROOT(误建)"),
        (TPRP, "投资交易@AI总结笔记(误塞处,已迁空)"),
    ]:
        if node_get(tok) and not children_of(tok):
            delete_node(tok, label, lift_children=False)
        else:
            print(f"  ⚠️ 未删(仍非空或失效): {label}")

    print(f"{'[DRY-RUN] ' if DRY else ''}=== 迁移完成 ===")


if __name__ == "__main__":
    main()
