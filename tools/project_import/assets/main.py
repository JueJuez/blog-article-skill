import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from assets.env_loader import load_root_env
load_root_env()

from assets.extractor import (
    extract_repo_urls,
    batch_deduplicate,
    filter_imported,
    filter_pending,
    parse_repo,
    owner_repo_key,
)
from assets.collector import collect_project_data
from assets.analyzer import AnalysisResult
from assets.llm_client import llm_analyze
from assets.feishu_writer import is_feishu_configured, write_record_with_retry
from assets.storage import has_items, pop_all, count_items, append_items
from assets.tracker import append_to_imported_list
from assets.reporter import ReportItem, build_report
from assets.project_finder import find as finder_find
from assets.local_writer import is_local_configured, get_library_dir, write_from_analysis


def _detect_storage():
    """规则一：启动时自动检测存储目标，直接告知结果，不询问。"""
    storage = os.environ.get("PROJECT_STORAGE", "local").lower()
    if storage == "feishu":
        if is_feishu_configured():
            print("✅ 飞书已配置（Base Token / Wiki 链接就绪），分析结果将写入 Bitable。")
        else:
            print("ℹ️ 未检测到飞书配置，结果将本地暂存到 pending_results.json。")
        return
    lib = get_library_dir()
    if lib:
        print(f"✅ 本地项目库已就绪（{lib}），分析结果将写入 Obsidian markdown。")
    else:
        print("ℹ️ 未检测到本地项目库（PROJECT_LIBRARY_DIR / OBSIDIAN_VAULT_PATH 均未设置），结果将本地暂存到 pending_results.json。")


def phase1_extract(text):
    urls = extract_repo_urls(text)
    if not urls:
        print("未发现有效的 GitHub / Gitee 仓库链接")
        return [], [], [], []
    deduped = batch_deduplicate(urls)
    new_urls, imported_skipped = filter_imported(deduped)
    new_urls, pending_skipped = filter_pending(new_urls)
    print(f"📥 阶段一：链接提取完成")
    print(f"  提取到 {len(urls)} 个链接，去重后 {len(deduped)} 个")
    print(f"  新项目：{len(new_urls)} 个，已入库跳过：{len(imported_skipped)} 个，待上传跳过：{len(pending_skipped)} 个")
    for url in imported_skipped:
        print(f"    ⏭ 已入库 {url}")
    for url in pending_skipped:
        print(f"    ⏭ 待上传 {url}")
    return new_urls, imported_skipped, pending_skipped


def _process_repo(url: str, analysis_file: str):
    """单个仓库的「采集 + 分析」流水线（在线程池中并行执行）。

    返回: ("ok", url, owner_repo, stars, AnalysisResult)
          或 ("failed", url, owner_repo, error_reason, None)
    """
    try:
        platform, owner, repo = parse_repo(url)
    except ValueError as e:
        return ("failed", url, owner_repo_key(url), str(e), None)

    print(f"\n🔍 阶段二/三：采集并分析 [{platform}] {owner}/{repo}")
    readme, stars, error = collect_project_data(platform, owner, repo)
    if error:
        print(f"  ❌ 采集失败: {error}")
        return ("failed", url, f"{owner}/{repo}", error, None)
    print(f"  ✅ README 获取成功 ({len(readme)} 字符)  ⭐ Stars: {stars}")

    result = llm_analyze(repo, stars, readme, analysis_file=analysis_file)
    if result is None:
        print("  ❌ 分析失败：未能解析出有效 JSON")
        return ("failed", url, f"{owner}/{repo}", "分析失败（无有效 JSON）", None)
    print(f"  ✅ 分析完成：类型={result.project_type} 领域={result.domain} "
          f"文档={result.doc_score} 功能={result.func_score}")
    return ("ok", url, f"{owner}/{repo}", stars, result)


def _store_feishu(completed, failed):
    """Legacy: write to Feishu Bitable. Used only when PROJECT_STORAGE=feishu."""
    print(f"\n📤 阶段四：已检测到飞书配置，开始入库")
    items = []
    # 1) 先上传历史本地暂存
    if has_items():
        pending = pop_all()
        print(f"  📤 检测到 {len(pending)} 条本地待上传记录，一并上传")
        for item in pending:
            upload_fields = {k: v for k, v in item.items() if not k.startswith("_")}
            owner_repo = item.get("_owner_repo", "")
            if write_record_with_retry(upload_fields):
                if owner_repo:
                    append_to_imported_list(owner_repo)
                items.append(ReportItem(item.get("fldvVvQNRR", ""), owner_repo, "success"))
            else:
                items.append(ReportItem(item.get("fldvVvQNRR", ""), owner_repo, "failed",
                                        error_reason="Feishu write failed"))
    # 2) 上传本次新结果
    for url, owner_repo, stars, result in completed:
        fields = result.to_feishu_fields(owner_repo.split("/")[-1], url, stars)
        if write_record_with_retry(fields):
            append_to_imported_list(owner_repo)
            items.append(ReportItem(url, owner_repo, "success", project_type=result.project_type))
        else:
            items.append(ReportItem(url, owner_repo, "failed", error_reason="Feishu write failed"))
    for url, owner_repo, error in failed:
        items.append(ReportItem(url, owner_repo, "failed", error_reason=error))
    return items


def phase4_store(completed, failed, source_kind: str = "direct"):
    """Store results. Default = local Obsidian markdown; PROJECT_STORAGE=feishu -> Bitable."""
    storage = os.environ.get("PROJECT_STORAGE", "local").lower()
    items = []

    # Explicit feishu request (legacy backend)
    if storage == "feishu" and is_feishu_configured():
        return {"items": _store_feishu(completed, failed), "local_saved": False}

    # Default: local Obsidian markdown
    lib = get_library_dir()
    if lib:
        os.makedirs(lib, exist_ok=True)
        print(f"\n📥 阶段四：写入本地项目库 {lib}")
        for url, owner_repo, stars, result in completed:
            status = write_from_analysis(owner_repo, url, stars, result, source_kind=source_kind)
            if status == "written":
                append_to_imported_list(owner_repo)
                items.append(ReportItem(url, owner_repo, "success", project_type=result.project_type))
            elif status == "skipped":
                items.append(ReportItem(url, owner_repo, "skipped", error_reason="本地已存在"))
            else:
                items.append(ReportItem(url, owner_repo, "failed", error_reason="本地库未配置"))
        for url, owner_repo, error in failed:
            items.append(ReportItem(url, owner_repo, "failed", error_reason=error))
        return {"items": items, "local_saved": False}

    # No local config: try feishu as a fallback, else local temp queue
    if is_feishu_configured():
        return {"items": _store_feishu(completed, failed), "local_saved": False}

    print(f"\n📦 阶段四：未检测到本地库/飞书配置，本地暂存")
    for url, owner_repo, stars, result in completed:
        fields = result.to_feishu_fields(owner_repo.split("/")[-1], url, stars)
        fields["_owner_repo"] = owner_repo
        append_items([fields])
        items.append(ReportItem(url, owner_repo, "success", project_type=result.project_type,
                                not_uploaded=True, error_reason="本地暂存（未上传）"))
    for url, owner_repo, error in failed:
        items.append(ReportItem(url, owner_repo, "failed", error_reason=error))
    return {"items": items, "local_saved": True}


def phase5_report(report_data):
    report = build_report(report_data["items"])
    print("\n" + report.generate())
    if report_data["local_saved"]:
        print()
        print("  ℹ️ 提示：配置飞书环境变量后重新运行此工具")
        print('       export FEISHU_BASE_TOKEN="你的_Base_Token_或_Wiki链接"')
        print('       export FEISHU_TABLE_ID="你的_Table_ID"')
        print("       → 配置后会自动将本地暂存记录 + 新结果一并上传到飞书")


def _build_empty_report(imported_skipped, pending_skipped):
    items = []
    for url in imported_skipped:
        items.append(ReportItem(url, owner_repo_key(url), "skipped",
                                error_reason="已入库"))
    for url in pending_skipped:
        items.append(ReportItem(url, owner_repo_key(url), "skipped",
                                error_reason="待上传（本地暂存）"))
    return build_report(items)


def main():
    args = sys.argv[1:]
    analysis_file = None
    feishu_spec = None
    feishu_table = None
    from_article = False
    from_video = False
    text_parts = []
    i = 0
    while i < len(args):
        if args[i] in ("--analysis-file", "-f"):
            if i + 1 < len(args):
                analysis_file = args[i + 1]
                i += 2
                continue
            else:
                print("用法: --analysis-file 需要一个文件路径参数")
                return 1
        elif args[i] in ("--feishu",):
            if i + 1 < len(args):
                feishu_spec = args[i + 1]
                i += 2
                continue
            else:
                print("用法: --feishu 需要一个 Wiki 链接或 Base Token")
                return 1
        elif args[i] in ("--table",):
            if i + 1 < len(args):
                feishu_table = args[i + 1]
                i += 2
                continue
            else:
                print("用法: --table 需要一个 Table ID")
                return 1
        elif args[i] in ("--from-article",):
            from_article = True
            i += 1
            continue
        elif args[i] in ("--from-video",):
            from_video = True
            i += 1
            continue
        else:
            text_parts.append(args[i])
            i += 1

    # 命令行传入的飞书目标优先于环境变量
    if feishu_spec:
        os.environ["FEISHU_BASE_TOKEN"] = feishu_spec
    if feishu_table:
        os.environ["FEISHU_TABLE_ID"] = feishu_table

    if not text_parts:
        print('用法: python assets/main.py "https://github.com/owner/repo ..." '
              '[--analysis-file path] [--feishu <wiki链接或token>] [--table <id>]')
        print('示例: python assets/main.py "我找到一个好用的PPT MCP https://github.com/A/B"')
        return 1

    input_text = " ".join(text_parts)

    print("=" * 50)
    print("  Batch Link Import - 批量导入工具")
    print("=" * 50)
    _detect_storage()

    if from_article or from_video:
        candidates, skipped = finder_find(input_text)
        new_urls = [c.url for c in candidates]
        imported_skipped = skipped
        pending_skipped = []
        print(f"\n📥 阶段一：从{'视频' if from_video else '文章'}提取完成")
        print(f"  新项目：{len(new_urls)} 个，去重跳过：{len(skipped)} 个")
        for c in candidates:
            print(f"    ✓ {c.owner_repo}  (来源: {c.source_kind})")
        for u in skipped:
            print(f"    ⏭ 已处理/待上传 {u}")
    else:
        new_urls, imported_skipped, pending_skipped = phase1_extract(input_text)

    if not new_urls:
        print("\n无新项目需要处理。")
        report = _build_empty_report(imported_skipped, pending_skipped)
        print(report.generate())
        return 0

    # 并发采集 + 分析（受 BATCH_MAX_WORKERS 控制，不超过待处理数量）
    try:
        max_workers = int(os.environ.get("BATCH_MAX_WORKERS", "5"))
    except ValueError:
        max_workers = 5
    max_workers = max(1, min(max_workers, len(new_urls)))

    completed = []
    failed = []

    print(f"\n🚀 并发处理 {len(new_urls)} 个仓库（线程数 {max_workers}）")
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(_process_repo, url, analysis_file): url
            for url in new_urls
        }
        for fut in as_completed(futures):
            kind, url, owner_repo, a, b = fut.result()
            if kind == "ok":
                completed.append((url, owner_repo, a, b))
            else:
                failed.append((url, owner_repo, a))

    print(f"\n📊 采集+分析完成：{len(completed)} 个成功，{len(failed)} 个失败")

    source_kind = "direct"
    if from_article:
        source_kind = "article"
    elif from_video:
        source_kind = "video"
    report_data = phase4_store(completed, failed, source_kind=source_kind)
    phase5_report(report_data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
