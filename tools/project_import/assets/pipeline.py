import json
import sys
import os
from typing import Dict, List, Tuple, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from assets.env_loader import load_root_env
load_root_env()

from assets.analyzer import AnalysisResult
from assets.feishu_writer import write_record_with_retry, is_feishu_configured
from assets.tracker import append_to_imported_list
from assets.extractor import (
    extract_repo_urls,
    parse_repo,
    normalize_url,
    batch_deduplicate,
    filter_imported,
    filter_pending,
)
from assets.collector import collect_project_data


def collect_to_file(
    text: str,
    out_file: str = "collected_data.json",
    dedup: bool = True,
) -> int:
    """从文本提取仓库链接并采集 README+Stars，写出 collected_data.json。

    每条记录：{platform, owner, repo, url, readme, stars, error}
    供 export_analysis_prompts / batch_upload_from_files 使用，打通子代理分析分支。

    dedup=True 时做三层去重（批次内 / 已入库 imported.txt / 待上传 pending_results.json），
    避免子代理分析路径重复导入已处理过的仓库。
    """
    urls = extract_repo_urls(text)
    urls = list(dict.fromkeys(normalize_url(u) for u in urls))  # 批次内去重保序
    if dedup:
        urls, imported_skipped = filter_imported(urls)
        urls, pending_skipped = filter_pending(urls)
        for u in imported_skipped:
            print(f"  ⏭ 已入库，跳过 {u}")
        for u in pending_skipped:
            print(f"  ⏭ 待上传，跳过 {u}")
    records = []
    for url in urls:
        platform, owner, repo = parse_repo(url)
        print(f"采集 [{platform}] {owner}/{repo} ...")
        readme, stars, error = collect_project_data(platform, owner, repo)
        records.append({
            "platform": platform,
            "owner": owner,
            "repo": repo,
            "url": url,
            "readme": readme,
            "stars": stars,
            "error": error,
        })
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"采集完成：{len(records)} 条 → {out_file}")
    return len(records)


def export_analysis_prompts(
    collected_file: str,
    output_file: str = "analysis_prompts.txt",
    max_readme_chars: int = 4000,
) -> int:
    """从采集数据导出截断后的 README 到文本文件，供 LLM 批量分析使用。

    Args:
        collected_file: collected_data.json 的路径
        output_file: 输出文本文件路径
        max_readme_chars: README 截断长度（默认 4000 字符）

    Returns:
        成功导出的项目数量
    """
    with open(collected_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    count = 0
    with open(output_file, "w", encoding="utf-8") as out:
        for i, d in enumerate(data):
            if d.get("error"):
                out.write(
                    f"=== [{i}] {d['owner']}/{d['repo']} === FAILED: {d['error']}\n"
                    f"===END===\n\n"
                )
                continue

            readme = d.get("readme") or ""
            truncated = readme[:max_readme_chars]
            if len(readme) > max_readme_chars:
                truncated += "...[truncated]"

            out.write(f"=== [{i}] {d['owner']}/{d['repo']} (stars={d.get('stars', 0)}) ===\n")
            out.write(truncated + "\n")
            out.write("===END===\n\n")
            count += 1

    print(f"导出完成：{count} 个项目 → {output_file}")
    return count


def batch_upload_from_files(
    collected_file: str,
    analysis_file: str,
    base_token: str = "",
    table_id: str = "",
) -> Dict:
    """从采集数据和分析结果批量上传到飞书多维表格。

    Args:
        collected_file: collected_data.json 的路径
        analysis_file: analysis_results.json 的路径（dict，key 为 owner/repo）
        base_token: 飞书 Base Token（为空则从环境变量读取）
        table_id: 飞书 Table ID（为空则从环境变量读取）

    Returns:
        {"success": int, "failed": int, "skipped": int, "items": List[ReportItem]}
    """
    if not is_feishu_configured() and (not base_token or not table_id):
        print("飞书未配置，无法上传")
        return {"success": 0, "failed": 0, "skipped": 0, "items": []}

    with open(collected_file, "r", encoding="utf-8") as f:
        collected = json.load(f)
    with open(analysis_file, "r", encoding="utf-8") as f:
        analysis = json.load(f)

    from assets.reporter import ReportItem, build_report

    report_items: List[ReportItem] = []
    success_count = 0
    fail_count = 0
    skip_count = 0

    for d in collected:
        owner = d["owner"]
        repo = d["repo"]
        owner_repo = f"{owner}/{repo}"
        url = d["url"]

        if d.get("error"):
            report_items.append(ReportItem(url, owner_repo, "failed", error_reason=d["error"]))
            fail_count += 1
            continue

        if owner_repo not in analysis:
            report_items.append(
                ReportItem(url, owner_repo, "failed", error_reason="Analysis not found")
            )
            fail_count += 1
            continue

        ar = analysis[owner_repo]
        stars_val = d.get("stars") or 0

        result = AnalysisResult(
            summary=ar.get("summary", ""),
            project_type=ar.get("project_type", ""),
            run_form=ar.get("run_form", ""),
            target_user=ar.get("target_user", ""),
            domain=ar.get("domain", ""),
            tags=ar.get("tags", []),
            highlights=ar.get("highlights", ""),
            doc_score=ar.get("doc_score", 0),
            func_score=ar.get("func_score", 0),
        )
        fields = result.to_feishu_fields(repo, url, stars_val)

        print(f"  [{owner_repo}] ... ", end="", flush=True)
        if write_record_with_retry(fields, base_token=base_token, table_id=table_id):
            append_to_imported_list(owner_repo)
            print("OK")
            success_count += 1
            report_items.append(
                ReportItem(url, owner_repo, "success", project_type=ar.get("project_type", ""))
            )
        else:
            print("FAILED")
            fail_count += 1
            report_items.append(
                ReportItem(url, owner_repo, "failed", error_reason="Feishu write failed")
            )

    report = build_report(report_items)
    print("\n" + report.generate())
    print(f"\n上传完成: {success_count} 成功, {fail_count} 失败, {skip_count} 跳过")

    return {
        "success": success_count,
        "failed": fail_count,
        "skipped": skip_count,
        "items": report_items,
    }


def _cli():
    if len(sys.argv) < 2:
        print("用法:")
        print('  python -m assets.pipeline collect "https://github.com/owner/repo ..." [out.json]')
        print("  python -m assets.pipeline prompts <collected.json> [prompts.txt]")
        print("  python -m assets.pipeline upload <collected.json> <analysis.json>")
        return 1
    cmd = sys.argv[1]
    if cmd == "collect":
        text = " ".join(sys.argv[2:])
        out = sys.argv[3] if len(sys.argv) > 3 else "collected_data.json"
        collect_to_file(text, out)
        return 0
    if cmd == "prompts":
        if len(sys.argv) < 3:
            print("用法: python -m assets.pipeline prompts <collected.json> [prompts.txt]")
            return 1
        collected = sys.argv[2]
        out = sys.argv[3] if len(sys.argv) > 3 else "analysis_prompts.txt"
        export_analysis_prompts(collected, out)
        return 0
    if cmd == "upload":
        if len(sys.argv) < 4:
            print("用法: python -m assets.pipeline upload <collected.json> <analysis.json>")
            return 1
        batch_upload_from_files(sys.argv[2], sys.argv[3])
        return 0
    print(f"未知命令: {cmd}")
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
