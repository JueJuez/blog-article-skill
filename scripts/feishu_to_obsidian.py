#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""scripts/feishu_to_obsidian.py — 飞书「AI 总结笔记」→ 本地 Obsidian 镜像 + 标题同步。

两件事合并成一遍(每篇只 fetch 一次):
  (A) 内容补齐: 飞书有、Obsidian 没有(按 URL 认同一篇)的文章 → 写新 .md(标题=飞书标题)。
  (B) 标题同步: 飞书标题被"优化"过、与 Obsidian 现有文件名对不上的 → 把 Obsidian 文件名
      改成飞书标题, **保留尾部日期/序号后缀**(如 `-20260519`、`-20260612-1`),
      **不动正文 H1**(H1 是内容总结/第一章, 非文章标题), 仅同步 frontmatter 的 title: 字段。

去重主键(用户要求"按原文路径/URL 而非标题"):
  - 来源链接 URL 优先: 飞书笔记与 Obsidian 笔记 body 里都有 `**来源链接**` URL, 归一化后认定同一篇。
    标题被改过也能认出, 绝不重复落盘。
  - Feishu node token: 仅本次写的笔记带 frontmatter, 进度日志按 token 记, 续跑免重抓。

文件名/标题清洗: `[\\:*?"<>|\n\r\[\]#\^]` → `_` (含 Obsidian 双链歧义字符)。  # noqa
日期保留: 解析 `标题-YYYYMMDD[-N]`, 同步时只替换"标题"段。
H1 不动: 无论新旧笔记, 正文第一个 `# 行` 一律不修改。

健壮性: 进度实时落盘(每篇 save_log), 被杀可续跑; 频限 10s/篇 + 429 退避 60s×5。
"""
import os
import re
import sys
import json
import time
import argparse
import subprocess
import atexit
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, ".env"))

VAULT = os.getenv("OBSIDIAN_VAULT_PATH", "")
SPACE = "7636965310725115074"

# ── 环境自纠(2026-09-05): detached 进程(尤其 PowerShell Start-Process)继承的环境
# 常缺 HOME / node 不在 PATH → lark-cli 子进程 spawn 失败(FileNotFoundError)或配置找不到。
# 这里脚本自己补齐, 不依赖调用方给环境。 ──
def _bootstrap_env():
    # USERPROFILE 必须在最前: lark-cli(Node) 用 os.homedir() 读它定位 token 配置
    # (~/.lark-cli), 缺了会静默失败(非0退出无输出 → cli_error detail 空)。
    # 后台任务(run_in_background / detached)常不加载用户 profile, USERPROFILE 缺失 → 必补。
    if not os.environ.get("USERPROFILE"):
        os.environ["USERPROFILE"] = os.path.expanduser("~")
    if not os.environ.get("HOME"):
        os.environ["HOME"] = os.environ.get("USERPROFILE")
    # 把 managed node 目录加进 PATH: lark-cli 的 run.js 内部用 execFileSync('node',...)
    # 拉起子进程, 必须能在 PATH 里找到 node(仅顶层用绝对路径不够)。
    pkg = os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries",
                       "node", "cli-connector-packages")
    node_exe = os.path.join(pkg, "node.exe")
    if os.path.exists(node_exe):
        node_dir = pkg
    else:
        import shutil
        node_dir = os.path.dirname(shutil.which("node") or "")
    if node_dir and node_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = node_dir + os.pathsep + os.environ.get("PATH", "")
_bootstrap_env()
ROOT = "FX33wKHwZiMzJqk7BQQctHD3nKh"
TREE_JSON = os.path.join(BASE_DIR, "scripts", "_feishu_tree.json")
LOG_PATH = os.path.join(BASE_DIR, "scripts", "feishu_to_obsidian_log.json")
# 当前迁移进程 PID(供自愈监控识别死活; os.execv 自我重启保持同 PID, 故始终准确)
MIGRATE_PID = os.path.join(BASE_DIR, "scripts", "_migrate_pid.txt")
RATE_DELAY = 30.0
MAX_STEM = 100

# Windows 非法 + Obsidian 双链歧义字符 (含 / 和 \: 飞书文件夹/标题里可能带斜杠,
# 不清洗会变成路径分隔符导致父目录不存在 → open() 抛 FileNotFoundError 2026-09-05 发现)
_ILLEGAL = re.compile(r'[/\\:*?"<>|\n\r\[\]#\^]')
# 文件名尾部: 标题-YYYYMMDD[-N]
_DATE_SUFFIX = re.compile(r'^(?P<title>.+?)-(?P<date>\d{8})(?:-(?P<idx>\d+))?$')


def sanitize_seg(p: str) -> str:
    return _ILLEGAL.sub('_', (p or "").strip())


def stem_of(title: str) -> str:
    s = sanitize_seg(title)
    if len(s) > MAX_STEM:
        s = s[:MAX_STEM].rstrip("._ ")
    return s or "未命名"


def split_stem(stem: str):
    """拆出 (标题段, 日期后缀)。无日期返回 (stem, '')。"""
    m = _DATE_SUFFIX.match(stem)
    if m:
        suf = "-" + m.group("date")
        if m.group("idx"):
            suf += "-" + m.group("idx")
        return m.group("title"), suf
    return stem, ""


def norm_url(u: str) -> str:
    if not u:
        return ""
    u = u.strip().strip(")>].,;\"'")
    try:
        p = urlparse(u)
        if not p.scheme:
            return u.lower().rstrip("/")
        q = ("?" + p.query) if p.query else ""
        return (p.scheme.lower() + "://" + p.netloc.lower()
                + p.path.rstrip("/") + q)
    except Exception:
        return u.lower().rstrip("/")


def extract_source_url(body: str):
    if not body:
        return ""
    m = re.search(r'(来源链接|原文链接|source\s*url)\b[^\n]*?(https?://\S+)',
                  body, re.I)
    if m:
        return norm_url(m.group(2))
    return ""


def _lark_cli_node():
    """解析 node 可执行文件绝对路径(不依赖 PATH), 优先紧邻 run.js 的 node.exe,
    否则 PATH 里的 node, 否则 managed node 候选目录。

    关键背景(2026-09-05): lark-cli.cmd 自身不含 node, 且仅当 node 在 shell PATH
    时才回退 `SET _prog=node`。后台 detached 进程继承的环境常缺 node → .cmd 直接失败
    (returncode!=0) → 整批记 cli_error。故此处直接解析绝对 node, 连 .cmd 都不走。
    """
    pkg = os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries",
                       "node", "cli-connector-packages")
    cand = os.path.join(pkg, "node.exe")
    if os.path.exists(cand):
        return cand
    import shutil
    p = shutil.which("node")
    if p:
        return p
    base = os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries",
                        "node", "versions")
    if os.path.isdir(base):
        for d in sorted(os.listdir(base), reverse=True):
            c2 = os.path.join(base, d, "node.exe")
            if os.path.exists(c2):
                return c2
    return "node"  # 最后回退: 赌 PATH 有 node


_LARK_RUN_JS = os.path.join(
    os.path.expanduser("~"), ".workbuddy", "binaries", "node",
    "cli-connector-packages", "node_modules", "@larksuite", "cli", "scripts",
    "run.js")


def cli(args):
    # 直接 `node <run.js>` 绝对路径调用, 不依赖 .cmd / 不依赖 PATH 里的 node。
    # 强制 UTF-8 解码 lark-cli 输出, 否则 Windows 下 subprocess 默认用 GBK 解码
    # UTF-8 中文 → UnicodeDecodeError 整进程崩溃(2026-09-05 detached 跑发现的坑).
    # timeout: 单篇 lark-cli 卡死(如某些文档触发 re-auth 等待 stdin)会永久阻塞主进程,
    # 故设上限, 超时由 fetch_markdown 兜底记为 cli_error 继续往下跑.
    # 重试: Windows 快速连续 spawn node 偶发"非0退出且无任何输出"(CreateProcess 抖动),
    #   此特征重试 3 次即可过(2026-09-05 排查: 211 篇 cli_error detail 全空,
    #   隔离单跑却 ok=true, 即 spawn 偶发失败而非频限/权限).
    node = _lark_cli_node()
    last = None
    for attempt in range(4):  # 1 主 + 3 重试
        try:
            out = subprocess.run([node, _LARK_RUN_JS] + args, capture_output=True,
                                 text=True, shell=False, encoding="utf-8",
                                 errors="replace", timeout=120)
        except subprocess.TimeoutExpired as e:
            last = subprocess.CompletedProcess(args, None, "", str(e))
            time.sleep(2)
            continue
        # spawn 失败特征: 非0 退出且 stdout/stderr 都空 → 重试
        if out.returncode != 0 and not (out.stdout or out.stderr):
            last = out
            time.sleep(2)
            continue
        return out
    return last


def is_rate_limit(out: subprocess.CompletedProcess):
    t = (out.stderr or "") + (out.stdout or "")
    t = t.lower()
    return any(k in t for k in ("frequency limit", "超出频率", "429",
                                "request trigger frequency", "99991400"))


def fetch_markdown(token: str):
    try:
        out = cli(["docs", "+fetch", "--doc", token, "--doc-format", "markdown",
                   "--scope", "full", "--as", "user", "--format", "json"])
    except subprocess.TimeoutExpired:
        return None, False, "cli_error", "lark-cli timeout(120s)"
    if out.returncode != 0:
        return None, False, "cli_error", (out.stderr or out.stdout or "")[:200]
    try:
        d = json.loads(out.stdout)
    except Exception:
        return None, False, "json_error", (out.stdout or "")[:200]
    if not d.get("ok"):
        if is_rate_limit(out):
            return None, False, "rate_limit", ""
        # 抓取飞书具体错误码/消息, 用于分类
        err = ((d.get("error") or {}).get("message") or "")
        code = str((d.get("error") or {}).get("code") or "")
        detail = f"{code}:{err}"
        return None, False, "not_ok", detail
    content = (d.get("data", {}).get("document", {}).get("content") or "")
    return content, True, "ok", ""


def is_missing_doc(detail: str) -> bool:
    d = (detail or "").lower()
    return ("3380002" in detail
            or "invalid document_id" in d
            or "document not found" in d
            or "not found" in d)


def build_content(title, token, body, source_url):
    fm = [
        "---",
        f'title: "{title.replace(chr(34), chr(39))}"',
        "source: feishu",
        f"feishu_node: {token}",
        f"migrated_from: feishu-to-obsidian",
    ]
    if source_url:
        fm.append(f"source_url: {source_url}")
    fm.append("---\n")
    return "\n".join(fm) + "\n" + body + "\n"


def build_existing_index(vault):
    """扫描现有 Obsidian 笔记, 返回 norm_url -> [filepath,...] (用于 URL 去重/标题同步匹配)。"""
    urls = {}
    if not os.path.isdir(vault):
        return urls
    for root, _, files in os.walk(vault):
        for fn in files:
            if not fn.lower().endswith(".md"):
                continue
            fp = os.path.join(root, fn)
            try:
                txt = open(fp, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            found = set()
            m = re.search(r'^source_url:\s*(\S+)', txt, re.M)
            if m:
                u = norm_url(m.group(1))
                if u:
                    found.add(u)
            u = extract_source_url(txt)
            if u:
                found.add(u)
            for u in found:
                urls.setdefault(u, []).append(fp)
    return urls


def target_path(item):
    segs = item["path"]
    *folders, title = segs
    rel_dir = [sanitize_seg(s) for s in folders]
    stem = stem_of(title)
    d = os.path.join(VAULT, *rel_dir) if rel_dir else VAULT
    return d, stem, title


def exists_in_dir(d, stem):
    exact = os.path.join(d, stem + ".md")
    if os.path.exists(exact):
        return exact
    if os.path.isdir(d):
        for fn in os.listdir(d):
            if fn.lower().endswith(".md") and sanitize_seg(os.path.splitext(fn)[0]) == stem:
                return os.path.join(d, fn)
    return None


def uniq_path(d, stem):
    p = os.path.join(d, stem + ".md")
    if not os.path.exists(p):
        return p
    i = 2
    while os.path.exists(f"{os.path.splitext(p)[0]}-{i}.md"):
        i += 1
    return f"{os.path.splitext(p)[0]}-{i}.md"


def update_frontmatter_title(path, feishu_title):
    """仅改/加 frontmatter 的 title: 字段, 不动正文 H1。无 frontmatter 的旧笔记不改。"""
    try:
        txt = open(path, encoding="utf-8", errors="ignore").read()
    except Exception:
        return
    safe = feishu_title.replace('"', "'")
    if re.match(r'^---\s*\n', txt):
        if re.search(r'^title:\s', txt, re.M):
            txt2 = re.sub(r'^title:\s.*$', f'title: "{safe}"', txt,
                          count=1, flags=re.M)
        else:
            txt2 = re.sub(r'^(---\s*\n)', f'\\1title: "{safe}"\n', txt,
                          count=1)
        try:
            open(path, "w", encoding="utf-8").write(txt2)
        except Exception:
            pass


def sync_title(old_path, feishu_title):
    """把 Obsidian 文件名(标题段)改成飞书标题, 保留尾部日期/序号后缀。返回新路径或 None(无需改)。"""
    d, fn = os.path.split(old_path)
    old_stem = os.path.splitext(fn)[0]
    new_stem = sanitize_seg(feishu_title)
    if not new_stem or new_stem == old_stem:
        return None
    cand = os.path.join(d, new_stem + ".md")
    if os.path.exists(cand):
        i = 2
        while os.path.exists(os.path.join(d, f"{new_stem}-{i}.md")):
            i += 1
        cand = os.path.join(d, f"{new_stem}-{i}.md")
    try:
        os.rename(old_path, cand)
    except Exception:
        return None
    update_frontmatter_title(cand, feishu_title)
    return cand


def load_log():
    if os.path.exists(LOG_PATH):
        try:
            return json.load(open(LOG_PATH, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_log(run_log):
    try:
        json.dump(run_log, open(LOG_PATH, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只统计, 不拉不写")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 篇(试跑)")
    ap.add_argument("--force", action="store_true", help="忽略进度日志, 全部重来")
    ap.add_argument("--recheck-missing", action="store_true",
                    help="重试先前被标记为 skip_missing_doc 的文章 "
                         "(2026-09-04 实战发现: 多数不是真删, 而是 token 权限问题,"
                         "用户在浏览器能打开 CLI 打不开)")
    args = ap.parse_args()
    # 登记 PID 给自愈监控(仅真实运行写, --dry 不污染)
    if not args.dry:
        try:
            open(MIGRATE_PID, "w", encoding="utf-8").write(str(os.getpid()))
        except Exception:
            pass
    # 自动多轮计数: 通过环境变量在递归/重启间传递, 避免 cli_error(频限)篇死循环。
    round_no = int(os.environ.get("MIGRATE_ROUND", "0"))

    if not VAULT or not os.path.isdir(VAULT):
        print(f"✗ OBSIDIAN_VAULT_PATH 未配置或不存在: {VAULT!r}")
        return
    if not os.path.exists(TREE_JSON):
        print(f"✗ 缺 {TREE_JSON}，先跑 scripts/scan_feishu_tree.py --json")
        return

    tree = json.load(open(TREE_JSON, encoding="utf-8"))
    log = {} if args.force else load_log()
    # 终态: 已处理完, 续跑不再碰(含各类失败——失败的重试无意义且浪费频限)。
    # 例外: skip_url_dup(旧版记录的)需重跑以便补做标题同步。
    TERMINAL = {"written", "synced_title", "skip_same_title", "skip_path"}
    # fetch_fail:* 不在终态 -> 失败篇可重跑(2026-09-05 修复: 后台任务环境缺 lark-cli
    # PATH 导致 555 篇假 cli_error, 必须在前台/绝对路径下重跑救回)
    # skip_missing_doc 默认算终态(不浪费频限), 但 --recheck-missing 可重新尝试
    TERMINAL_DEFAULT = TERMINAL | (set() if args.recheck_missing else {"skip_missing_doc"})
    done_tokens = {tok for tok, v in log.items()
                   if isinstance(v, dict) and v.get("status") in TERMINAL_DEFAULT} \
        if not args.force else set()

    existing = build_existing_index(VAULT)
    print(f"=== 飞书→Obsidian 迁移 + 标题同步 (URL主键) ===")
    print(f"  飞书文章: {len(tree)}  现有Obsidian URL索引: {len(existing)}  "
          f"已写跳过: {len(done_tokens)}")

    present, todo = [], []
    for it in tree:
        tok = it["token"]
        # #2 飞书→Obsidian：仅当目标文件「不存在」才重抓（删了才重新上传）；
        # 已终态且文件在 → 跳过（绝不覆盖已有笔记）；已终态但文件被删 → 重新拉取。
        if tok in done_tokens and exists_in_dir(*target_path(it)[:2]):
            present.append(it)
            continue
        todo.append(it)
    print(f"  待处理: {len(todo)}  (已就绪跳过: {len(present)})")

    if args.dry:
        print("[dry] 仅统计。URL/标题同步需 fetch, 见 --limit/正式运行。")
        return
    if args.limit:
        todo = todo[:args.limit]
        print(f"[limit] 仅处理前 {len(todo)} 篇待处理。")

    written = synced = skip_same = skipped_path = failed = 0
    has_img_total = 0
    run_log = load_log() if not args.force else {}
    t0 = time.time()

    def do_fetch(tok):
        """拉取+退避重试, 返回 (content, ok, why, detail)。

        rate_limit: lark-cli(user token) 频限, 退避 60s 重试最多 3 次。
        cli_error: 多为频限窗口较长(>45s)的瞬时失败, 这里**不重试**
           (单轮内重试 3×15s 反而死循环, 见 2026-09-05 排查); 单篇只试 1 次,
           失败则交回主循环记 fetch_fail:cli_error, 由"自动多轮"冷却 90s 后
           整体重跑(此时频限已恢复, 重试即成功)。
        """
        content, ok, why, detail = fetch_markdown(tok)
        attempts = 0
        while not ok and why == "rate_limit" and attempts < 3:
            time.sleep(60)
            attempts += 1
            content, ok, why, detail = fetch_markdown(tok)
        return content, ok, why, detail

    def progress():
        if idx % 10 == 0 or idx == len(todo):
            print(f"  …{idx}/{len(todo)} 写{written} 标题同步{synced} "
                  f"同标题跳过{skip_same} 路径跳过{skipped_path} 败{failed} "
                  f"图{has_img_total} 耗时{int(time.time()-t0)}s")

    for idx, it in enumerate(todo, 1):
        # 自我重启: 每 20 篇 execv 换新进程, 刷新 lark-cli(user token) 状态 —
        # 老进程连跑几分钟后偶发 fetch 静默失败(空输出 cli_error), 新进程全 ok
        # (2026-09-05 实测: 后台 cli() 抓 5 篇 cli_error token 全 rc=0 ok=True)。
        if idx % 20 == 0 and round_no < 5:
            print(f"\n  已处理 {idx} 篇, 重启进程刷新 lark-cli 状态...")
            save_log(run_log)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        tok = it["token"]
        try:
            feishu_title = it["path"][-1]
            d, stem, _ = target_path(it)

            # 复用上一轮已抽到的 URL(省一次 fetch)
            prev = log.get(tok) if not args.force else None
            reuse_url = norm_url(prev["source_url"]) if (prev
                           and prev.get("status") == "skip_url_dup"
                           and prev.get("source_url")) else None

            url = None
            content = None
            if reuse_url:
                url = reuse_url
            else:
                content, ok, why, detail = do_fetch(tok)
                if not ok:
                    if is_missing_doc(detail):
                        # 不知道真假没, 默认仍记 skip_missing_doc 终态;
                        # --recheck-missing 跑可让旧 token 失效/权限恢复后自动重试。
                        run_log[tok] = {"path": "", "status": "skip_missing_doc",
                                        "detail": detail}
                        print(f"  ⊘ 飞书文档不可访问(已记终态, 浏览器能开就是权限问题): {'/'.join(it['path'])}")
                    else:
                        run_log[tok] = {"path": "", "status": f"fetch_fail:{why}",
                                        "detail": detail}
                        print(f"  ✗ 拉取失败 [{why}] {detail[:60]}: "
                              f"{'/'.join(it['path'])}")
                        failed += 1
                    time.sleep(RATE_DELAY)
                    save_log(run_log)
                    continue
                url = extract_source_url(content)

            # (B) 标题同步: vault 有同 URL 文章 → 改文件名(保留日期), 不动 H1
            hits = existing.get(url, []) if url else []
            if hits:
                synced_n = 0
                for hp in hits:
                    if sync_title(hp, feishu_title):
                        synced_n += 1
                rel = os.path.relpath(hits[0], VAULT)
                run_log[tok] = {"path": rel,
                                "status": "synced_title" if synced_n else "skip_same_title",
                                "source_url": url}
                if synced_n:
                    synced += 1
                else:
                    skip_same += 1
                time.sleep(RATE_DELAY)
                save_log(run_log)
                progress()
                continue

            # (A) 内容补齐: 真缺失 → 写新文件(标题=飞书标题, 无日期)
            if content is None:
                content, ok, why, detail = do_fetch(tok)
                if not ok:
                    if is_missing_doc(detail):
                        run_log[tok] = {"path": "", "status": "skip_missing_doc",
                                        "detail": detail}
                    else:
                        run_log[tok] = {"path": "", "status": f"fetch_fail:{why}",
                                        "detail": detail}
                        failed += 1
                    time.sleep(RATE_DELAY)
                    save_log(run_log)
                    continue
            cur_url = extract_source_url(content)
            has_img = "![" in content
            if has_img:
                has_img_total += 1
            if not args.force and exists_in_dir(d, stem):
                rel = os.path.relpath(exists_in_dir(d, stem), VAULT)
                run_log[tok] = {"path": rel, "status": "skip_path",
                                "source_url": cur_url, "has_images": has_img}
                skipped_path += 1
                time.sleep(RATE_DELAY)
                save_log(run_log)
                progress()
                continue
            os.makedirs(d, exist_ok=True)
            path = uniq_path(d, stem)
            final = build_content(feishu_title, tok, content, cur_url)
            with open(path, "w", encoding="utf-8") as f:
                f.write(final)
            rel = os.path.relpath(path, VAULT)
            run_log[tok] = {"path": rel, "status": "written",
                            "source_url": cur_url, "has_images": has_img}
            written += 1
            time.sleep(RATE_DELAY)
            save_log(run_log)
            progress()
        except Exception as e:
            run_log[tok] = {"path": "", "status": "fetch_fail:exception",
                            "detail": repr(e)[:200]}
            print(f"  ⚠ 异常 [{type(e).__name__}] {'/'.join(it['path'])}: {e}")
            failed += 1
            try:
                save_log(run_log)
            except Exception:
                pass
            time.sleep(RATE_DELAY)
            continue

    save_log(run_log)

    # ── 自动多轮(2026-09-05 根治 cli_error 死循环) ──
    # cli_error 多为 lark-cli(user token) 频限瞬时失败: 单轮内重试会死循环,
    # 故单轮只各试一次, 跑完若仍有 cli_error, 冷却 90s 后自动重跑(频限已恢复)。
    # 最多 6 轮, 防真失败篇无限循环。
    cli_err = sum(1 for v in run_log.values()
                  if isinstance(v, dict) and v.get("status") == "fetch_fail:cli_error")
    if cli_err > 0 and round_no < 5:
        print(f"\n  剩 {cli_err} 篇 cli_error(频限瞬时失败), "
              f"冷却 90s 后自动第 {round_no + 2} 轮...")
        time.sleep(90)
        os.environ["MIGRATE_ROUND"] = str(round_no + 1)
        main()
        return
    print(f"\n=== 完成 ===")
    if cli_err > 0:
        print(f"  ⚠ 已达最大轮次, 仍剩 {cli_err} 篇 cli_error(可能真失败/真权限)")
    print(f"  本次写: {written}  标题同步: {synced}  同标题跳过: {skip_same}  "
          f"路径跳过: {skipped_path}  失败: {failed}")
    print(f"  含图片笔记(本批): {has_img_total}")
    print(f"  进度日志: {LOG_PATH}")


def _pid_alive(pid):
    # Windows 下 os.kill(pid, 0) 用于探测存活: 进程存在→无异常, 不存在→ProcessLookupError。
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return False


def _single_instance_lock_path():
    return os.path.join(BASE_DIR, "scripts", "_migrate_lock.pid")


def _acquire_single_instance():
    # 单实例互斥(2026-09-05): 排查期反复启动会留下多个迁移进程抢同一日志/同一批 token
    # 互相 race, 导致 cli_error 上蹦下跳。此锁仅防重复启动, 不是监控/看门狗。
    # 进程崩溃留下的陈旧 pid 会被 _pid_alive 判死而自动放行(下次启动覆盖锁文件)。
    lp = _single_instance_lock_path()
    try:
        if os.path.exists(lp):
            old = open(lp, encoding="utf-8").read().strip()
            if old.isdigit() and _pid_alive(int(old)):
                print(f"[单实例锁] 另一迁移实例已在运行 (pid={old}), 本进程退出。")
                sys.exit(0)
    except Exception:
        pass
    with open(lp, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    atexit.register(_release_single_instance)


def _release_single_instance():
    try:
        lp = _single_instance_lock_path()
        if os.path.exists(lp) and open(lp, encoding="utf-8").read().strip() == str(os.getpid()):
            os.remove(lp)
    except Exception:
        pass


if __name__ == "__main__":
    _acquire_single_instance()
    main()
