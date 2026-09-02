"""Agent-driven single-repo ingestion.

In FORCE_AGENT_MODE the main pipeline refuses to let the *executing model's
main session* read a README (it would pollute context). Instead a sub-agent (or
the main agent) reads the README, produces the 9-field analysis JSON, and calls
this script to persist it:

    python assets/ingest_repo.py <owner/repo> <analysis.json> [--source-kind direct]

The script re-collects stars (cheap) so community_score / total_score stay
correct, then writes a local Obsidian .md via local_writer. Idempotent: if the
file already exists it is skipped and imported.txt is left untouched.
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assets.collector import collect_project_data
from assets.analyzer import parse_llm_response
from assets.local_writer import write_from_analysis, get_library_dir
from assets.tracker import append_to_imported_list
from assets.extractor import parse_repo
from assets.quality_gate import (
    is_low_quality,
    gate_reason,
    route_to_review,
)


def main(argv):
    # Accept both sys.argv (script name at index 0) and a plain argument list.
    if argv and (argv[0].endswith("ingest_repo.py") or argv[0].endswith("ingest_repo")):
        args = argv[1:]
    else:
        args = argv
    if len(args) < 2:
        print("用法: python assets/ingest_repo.py <owner/repo> <analysis.json> "
              "[--source-kind direct|article|video|name-search]")
        return 2
    owner_repo = args[0].strip().lower()
    analysis_path = args[1]
    source_kind = "direct"
    if len(args) > 2 and args[2] == "--source-kind" and len(args) > 3:
        source_kind = args[3]

    try:
        platform, owner, repo = parse_repo(f"https://github.com/{owner_repo}")
    except ValueError as e:
        print(f"❌ 非法 owner/repo: {e}")
        return 2

    if not os.path.exists(analysis_path):
        print(f"❌ 分析文件不存在: {analysis_path}")
        return 2
    with open(analysis_path, "r", encoding="utf-8") as f:
        result = parse_llm_response(f.read())
    if result is None:
        print(f"❌ 分析文件未解析出有效 JSON: {analysis_path}")
        return 2

    _, stars, _ = collect_project_data(platform, owner, repo)
    url = f"https://github.com/{owner}/{repo}"

    # 收录质量门禁：低星 / 低评分不自动入库，转入待复核队列
    if is_low_quality(result.doc_score, result.func_score, stars):
        reason = gate_reason(result.doc_score, result.func_score, stars)
        route_to_review(owner_repo, url, stars, result, source_kind, reason)
        print(f"⚠️ 质量门禁未过，转入待复核队列 {owner_repo}  ⭐{stars}  {reason}")
        return 0

    status = write_from_analysis(
        owner_repo, url, stars, result, source_kind=source_kind)
    if status == "written":
        append_to_imported_list(owner_repo)
        print(f"✅ 入库 {owner_repo}  ⭐{stars}  类型={result.project_type}")
        return 0
    if status == "skipped":
        print(f"⏭ 已存在，跳过 {owner_repo}")
        return 0
    print(f"❌ 写入失败（本地库未配置？）{owner_repo}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
