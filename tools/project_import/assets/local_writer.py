"""Local Obsidian writer for project_import.

Stores each project as one Markdown file with YAML frontmatter inside the
project library folder (default: ``$OBSIDIAN_VAULT_PATH/开源项目``; override with
``$PROJECT_LIBRARY_DIR``). This is the primary store for the tool — chosen over
Feishu Bitable because any agent in any project can grep / parse / embed these
files with zero auth, which is exactly the "discover which projects are usable"
use case.

Frontmatter carries every structured field (project_type / run_form /
target_user / domain / tags / scores / …) so filtering is a one-liner for an
agent, while Obsidian (Dataview) still gives a human table view.
"""
import os
import re
import sys

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if str(_BASE_DIR) not in sys.path:
    sys.path.append(_BASE_DIR)

DEFAULT_SUBDIR = "开源项目"

# Logical field -> YAML key used in frontmatter. Kept aligned with
# analyzer.LOGICAL_FIELDS / feishu_writer.DEFAULT_FIELD_MAP so a record carries
# the same information in either backend.
LOGICAL_TO_YAML = [
    ("project_name", "project_name"),
    ("summary", "summary"),
    ("tags", "tags"),
    ("git_url", "url"),
    ("project_type", "project_type"),
    ("run_form", "run_form"),
    ("target_user", "target_user"),
    ("domain", "domain"),
    ("highlights", "highlights"),
    ("community_score", "community_score"),
    ("doc_score", "doc_score"),
    ("func_score", "func_score"),
    ("total_score", "total_score"),
    ("eval_date", "imported_at"),
    ("status", "status"),
]


def get_library_dir() -> str:
    """Resolve the library folder.

    Priority: ``$PROJECT_LIBRARY_DIR`` > ``$OBSIDIAN_VAULT_PATH/开源项目``.
    Returns "" when neither is configured.
    """
    explicit = os.environ.get("PROJECT_LIBRARY_DIR", "").strip()
    if explicit:
        return explicit
    vault = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
    if vault:
        return os.path.join(vault, DEFAULT_SUBDIR)
    return ""


def is_local_configured() -> bool:
    return bool(get_library_dir())


def owner_repo_to_filename(owner_repo: str) -> str:
    """owner/repo -> owner__repo.md (filesystem-safe)."""
    safe = (owner_repo or "").strip().replace("/", "__")
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", safe)
    return safe + ".md"


def parse_owner_repo_from_url(url: str) -> str:
    m = re.search(r"(github\.com|gitee\.com)/([^/\s]+)/([^/\s)#?]+)", url or "")
    if m:
        repo = m.group(3)
        # strip a trailing ".git" if present
        repo = re.sub(r"\.git$", "", repo)
        return f"{m.group(2)}/{repo}"
    return ""


def extract_raw_url(cell: str) -> str:
    """Bitable git_url cells are often markdown links "[text](url)"."""
    if not cell:
        return ""
    m = re.search(r"\]\(([^)]+)\)", cell)
    if m:
        return m.group(1).strip()
    return cell.strip()


def platform_from_url(url: str) -> str:
    if not url:
        return ""
    if "gitee.com" in url:
        return "gitee"
    if "github.com" in url:
        return "github"
    return ""


def _yaml_scalar(value: str) -> str:
    """Quote a scalar when it would confuse the YAML parser."""
    s = str(value)
    if s == "":
        return '""'
    # Quote if it contains characters meaningful to YAML, or looks like a number/bool.
    if re.search(r'[:#\[\]{}&*?|<>=!%@`",\n]', s) or s.lower() in (
        "true",
        "false",
        "null",
        "yes",
        "no",
    ):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def render_markdown(frontmatter: dict, body: str = "") -> str:
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, (list, tuple)):
            if value:
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {_yaml_scalar(str(item))}")
            else:
                lines.append(f"{key}: []")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {_yaml_scalar(str(value))}")
    lines.append("---")
    if body and body.strip():
        lines.append("")
        lines.append(body.strip())
    return "\n".join(lines) + "\n"


def build_frontmatter(
    owner_repo: str,
    url: str,
    stars: int = 0,
    *,
    project_type: str = "",
    run_form: str = "",
    target_user: str = "",
    domain: str = "",
    summary: str = "",
    tags=None,
    highlights: str = "",
    doc_score: int = 0,
    func_score: int = 0,
    community_score: int = 0,
    total_score: int = 0,
    source_kind: str = "direct",
    imported_at: str = "",
    status: str = "已入库",
) -> dict:
    from datetime import datetime

    if not imported_at:
        imported_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "owner_repo": owner_repo,
        "url": url,
        "platform": platform_from_url(url),
        "summary": summary,
        "project_type": project_type,
        "run_form": run_form,
        "target_user": target_user,
        "domain": domain,
        "tags": list(tags or []),
        "highlights": highlights,
        "doc_score": doc_score,
        "func_score": func_score,
        "community_score": community_score,
        "total_score": total_score,
        "source_kind": source_kind,
        "imported_at": imported_at,
        "status": status,
    }


def write_project_md(
    owner_repo: str,
    url: str,
    stars: int = 0,
    *,
    project_type: str = "",
    run_form: str = "",
    target_user: str = "",
    domain: str = "",
    summary: str = "",
    tags=None,
    highlights: str = "",
    doc_score: int = 0,
    func_score: int = 0,
    source_kind: str = "direct",
    imported_at: str = "",
    body: str = "",
) -> str:
    """Write one project Markdown file.

    Returns: "written" | "skipped" (file already exists) | "unconfigured".
    """
    lib = get_library_dir()
    if not lib:
        return "unconfigured"
    os.makedirs(lib, exist_ok=True)

    community_score = 0
    try:
        from assets.collector import stars_to_score

        community_score = stars_to_score(stars)
    except Exception:
        pass
    total_score = community_score + (doc_score or 0) + (func_score or 0)

    fm = build_frontmatter(
        owner_repo=owner_repo,
        url=url,
        stars=stars,
        project_type=project_type,
        run_form=run_form,
        target_user=target_user,
        domain=domain,
        summary=summary,
        tags=tags,
        highlights=highlights,
        doc_score=doc_score,
        func_score=func_score,
        community_score=community_score,
        total_score=total_score,
        source_kind=source_kind,
        imported_at=imported_at,
    )
    path = os.path.join(lib, owner_repo_to_filename(owner_repo))
    if os.path.exists(path):
        return "skipped"
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_markdown(fm, body=body))
    return "written"


def write_from_analysis(
    owner_repo: str,
    url: str,
    stars: int,
    result,
    *,
    source_kind: str = "direct",
    imported_at: str = "",
) -> str:
    """Convenience wrapper taking an ``AnalysisResult`` (used by main.py)."""
    return write_project_md(
        owner_repo,
        url,
        stars,
        project_type=result.project_type,
        run_form=result.run_form,
        target_user=result.target_user,
        domain=result.domain,
        summary=result.summary,
        tags=result.tags,
        highlights=result.highlights,
        doc_score=result.doc_score,
        func_score=result.func_score,
        source_kind=source_kind,
        imported_at=imported_at,
    )
